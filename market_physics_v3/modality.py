from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, Sequence

from .orderbook import BBO_STREAMS

DEFAULT_VENUES = ("binance", "bybit", "okx", "hyperliquid")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _iter_jsonl(paths: Iterable[Path], start_ns: int = 0, stop_ns: int = 0):
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                receive_ns = int(row.get("receive_ts_ns", 0) or 0)
                if start_ns and receive_ns < start_ns:
                    continue
                if stop_ns and receive_ns > stop_ns:
                    continue
                yield row


def _health_window(health_dir: Path, venue: str) -> Dict[str, object]:
    path = health_dir / (venue + ".json")
    if not path.exists():
        return {"exists": False, "started_ns": 0, "stopped_ns": 0}
    data = json.loads(path.read_text())
    return {
        "exists": True,
        "started_ns": int(data.get("started_ns", 0) or 0),
        "stopped_ns": int(data.get("stopped_ns", 0) or 0),
        "health": data,
    }


def _glob(root: Path, kind: str, venue: str, symbol: str):
    return (root / "raw" / kind / ("venue=" + venue) / ("symbol=" + symbol)).glob("date=*/events.jsonl")


def audit_cell(
    root: Path,
    venue: str,
    symbol: str,
    start_ns: int,
    stop_ns: int,
    fresh_max_lag_ms: float = 5000.0,
) -> Dict[str, object]:
    venue = str(venue).lower()
    symbol = str(symbol).upper()

    book_events = 0
    fresh_book_events = 0
    provenance_missing = 0
    stream_counts: Dict[str, int] = {}
    deep_events = 0
    deep_snapshots = 0
    deep_incrementals = 0
    bbo_events = 0
    max_book_lag_ms = 0.0

    for row in _iter_jsonl(_glob(root, "book_events", venue, symbol), start_ns, stop_ns):
        book_events += 1
        event_ns = int(row.get("event_ts_ns", 0) or 0)
        receive_ns = int(row.get("receive_ts_ns", 0) or 0)
        lag_ms = max(0.0, (receive_ns - event_ns) / 1e6) if event_ns and receive_ns else float("inf")
        max_book_lag_ms = max(max_book_lag_ms, lag_ms if lag_ms != float("inf") else 0.0)
        if lag_ms <= float(fresh_max_lag_ms):
            fresh_book_events += 1
        stream = row.get("source_stream")
        if not stream:
            provenance_missing += 1
            continue
        stream = str(stream)
        stream_counts[stream] = stream_counts.get(stream, 0) + 1
        if stream in BBO_STREAMS:
            bbo_events += 1
        else:
            deep_events += 1
            if row.get("event_type") == "snapshot":
                deep_snapshots += 1
            else:
                deep_incrementals += 1

    trades = 0
    fresh_trades = 0
    fresh_individual_trades = 0
    fresh_aggregate_trades = 0
    trade_provenance_missing = 0
    trade_stream_counts: Dict[str, int] = {}
    granularity_counts: Dict[str, int] = {}
    max_trade_lag_ms = 0.0
    for row in _iter_jsonl(_glob(root, "trades", venue, symbol), start_ns, stop_ns):
        trades += 1
        event_ns = int(row.get("event_ts_ns", 0) or 0)
        receive_ns = int(row.get("receive_ts_ns", 0) or 0)
        lag_ms = max(0.0, (receive_ns - event_ns) / 1e6) if event_ns and receive_ns else float("inf")
        max_trade_lag_ms = max(max_trade_lag_ms, lag_ms if lag_ms != float("inf") else 0.0)
        stream = row.get("source_stream")
        granularity = row.get("granularity")
        if stream:
            trade_stream_counts[str(stream)] = trade_stream_counts.get(str(stream), 0) + 1
        else:
            trade_provenance_missing += 1
        if granularity:
            granularity_counts[str(granularity)] = granularity_counts.get(str(granularity), 0) + 1
        if lag_ms <= float(fresh_max_lag_ms):
            fresh_trades += 1
            if granularity == "individual":
                fresh_individual_trades += 1
            elif granularity == "aggregate":
                fresh_aggregate_trades += 1

    derivative_counts: Dict[str, int] = {}
    fresh_derivative_counts: Dict[str, int] = {}
    max_derivative_lag_ms = 0.0
    for row in _iter_jsonl(_glob(root, "derivatives", venue, symbol), start_ns, stop_ns):
        kind = str(row.get("kind") or "unknown")
        derivative_counts[kind] = derivative_counts.get(kind, 0) + 1
        event_ns = int(row.get("event_ts_ns", 0) or 0)
        receive_ns = int(row.get("receive_ts_ns", 0) or 0)
        lag_ms = max(0.0, (receive_ns - event_ns) / 1e6) if event_ns and receive_ns else float("inf")
        max_derivative_lag_ms = max(max_derivative_lag_ms, lag_ms if lag_ms != float("inf") else 0.0)
        if lag_ms <= float(fresh_max_lag_ms):
            fresh_derivative_counts[kind] = fresh_derivative_counts.get(kind, 0) + 1

    deep_ready = bool(
        deep_events > 0
        and deep_snapshots > 0
        and provenance_missing == 0
        and fresh_book_events > 0
    )
    explicit_bbo_ready = bool(
        bbo_events > 0 and provenance_missing == 0 and fresh_book_events > 0
    )
    bbo_ready = bool(explicit_bbo_ready or deep_ready)
    bbo_mode = "explicit" if explicit_bbo_ready else ("derived_from_deep" if deep_ready else "missing")
    event_trade_ready = bool(trades > 0 and fresh_trades > 0 and trade_provenance_missing == 0)
    tick_trade_ready = bool(fresh_individual_trades > 0 and trade_provenance_missing == 0)

    return {
        "venue": venue,
        "symbol": symbol,
        "window": {"started_ns": int(start_ns), "stopped_ns": int(stop_ns)},
        "book": {
            "events": int(book_events),
            "fresh_events": int(fresh_book_events),
            "source_stream_counts": dict(sorted(stream_counts.items())),
            "provenance_missing": int(provenance_missing),
            "deep_events": int(deep_events),
            "deep_snapshots": int(deep_snapshots),
            "deep_incrementals": int(deep_incrementals),
            "bbo_events": int(bbo_events),
            "deep_ready": deep_ready,
            "bbo_ready": bbo_ready,
            "bbo_mode": bbo_mode,
            "max_lag_ms": float(max_book_lag_ms),
        },
        "trades": {
            "events": int(trades),
            "fresh_events": int(fresh_trades),
            "fresh_individual_events": int(fresh_individual_trades),
            "fresh_aggregate_events": int(fresh_aggregate_trades),
            "source_stream_counts": dict(sorted(trade_stream_counts.items())),
            "granularity_counts": dict(sorted(granularity_counts.items())),
            "provenance_missing": int(trade_provenance_missing),
            "event_stream_ready": event_trade_ready,
            "tick_ready": tick_trade_ready,
            "max_lag_ms": float(max_trade_lag_ms),
        },
        "derivatives": {
            "counts": dict(sorted(derivative_counts.items())),
            "fresh_counts": dict(sorted(fresh_derivative_counts.items())),
            "max_lag_ms": float(max_derivative_lag_ms),
        },
        # Cross-venue book synchronization needs a real-time event trade flow,
        # but it does not require every venue to expose one-row-per-match trades.
        # The stricter generic tick_trades gate is reported separately.
        "synchronized_book_input_ready": bool(deep_ready and bbo_ready and event_trade_ready),
    }


def audit_modality_matrix(
    root: str = "data/market_physics_v3",
    health_dir: str = "reports/market_physics_v3/health",
    venues: Sequence[str] = DEFAULT_VENUES,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    fresh_max_lag_ms: float = 5000.0,
) -> Dict[str, object]:
    root_path = Path(root)
    health_path = Path(health_dir)
    cells = {}
    missing_health = []

    for venue_raw in venues:
        venue = str(venue_raw).lower()
        window = _health_window(health_path, venue)
        if not window.get("exists"):
            missing_health.append(venue)
        start_ns = int(window.get("started_ns", 0) or 0)
        stop_ns = int(window.get("stopped_ns", 0) or 0)
        for symbol_raw in symbols:
            symbol = str(symbol_raw).upper()
            key = venue + ":" + symbol
            cells[key] = audit_cell(
                root_path, venue, symbol, start_ns, stop_ns, fresh_max_lag_ms
            )

    cell_values = list(cells.values())
    all_deep = bool(cell_values) and all(x["book"]["deep_ready"] for x in cell_values)
    all_bbo = bool(cell_values) and all(x["book"]["bbo_ready"] for x in cell_values)
    all_event_trades = bool(cell_values) and all(x["trades"]["event_stream_ready"] for x in cell_values)
    all_tick_trades = bool(cell_values) and all(x["trades"]["tick_ready"] for x in cell_values)
    all_sync_inputs = bool(cell_values) and all(x["synchronized_book_input_ready"] for x in cell_values)

    def all_fresh_derivative(kind: str) -> bool:
        return bool(cell_values) and all(
            int(x["derivatives"]["fresh_counts"].get(kind, 0)) > 0 for x in cell_values
        )

    blockers = []
    tick_blockers = []
    for key, cell in cells.items():
        if not cell["book"]["deep_ready"]:
            blockers.append(key + ":deep_book")
        if not cell["book"]["bbo_ready"]:
            blockers.append(key + ":bbo")
        if not cell["trades"]["event_stream_ready"]:
            blockers.append(key + ":event_trades")
        if not cell["trades"]["tick_ready"]:
            tick_blockers.append(key + ":individual_trades")

    suggestions = {
        "l2_book_events": "EVENT_LEVEL" if all_deep else "BLOCKED",
        "tick_trades": "EVENT_LEVEL" if all_tick_trades else "BLOCKED",
        "bbo": "EVENT_LEVEL" if all_bbo else "BLOCKED",
        "open_interest": "EVENT_LEVEL" if all_fresh_derivative("open_interest") else "PARTIAL",
        "funding": "EVENT_LEVEL" if all_fresh_derivative("funding") else "PARTIAL",
        "mark": "EVENT_LEVEL" if all_fresh_derivative("mark") else "PARTIAL",
        "index": "EVENT_LEVEL" if all_fresh_derivative("index") else "PARTIAL",
    }

    return {
        "venues": [str(v).lower() for v in venues],
        "symbols": [str(s).upper() for s in symbols],
        "fresh_max_lag_ms": float(fresh_max_lag_ms),
        "missing_health": sorted(set(missing_health)),
        "cells": cells,
        "summary": {
            "all_deep_books_ready": all_deep,
            "all_bbo_ready": all_bbo,
            "all_event_trade_streams_ready": all_event_trades,
            "all_tick_trades_ready": all_tick_trades,
            "ready_for_synchronized_books": all_sync_inputs,
            "blocking_cells": sorted(blockers),
            "tick_trade_blocking_cells": sorted(tick_blockers),
            "manifest_status_suggestions": suggestions,
        },
    }
