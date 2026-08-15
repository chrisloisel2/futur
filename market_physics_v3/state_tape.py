from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import pandas as pd

from .microstructure import book_feature_vector, top_of_book_ofi
from .schema import BookEvent
from .synchronized import SynchronizedBookEngine

DEFAULT_VENUES = ("binance", "bybit", "okx", "hyperliquid")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def concurrent_health_window(
    health_dir: str,
    venues: Sequence[str] = DEFAULT_VENUES,
    max_start_skew_ms: float = 5000.0,
) -> Dict[str, object]:
    """Return the common receive-time window proven by all venue health files.

    Sequential smokes deliberately fail: a cross-venue state tape is only valid
    when all required collectors were alive at the same time. The start-skew
    guard catches accidentally reusing health files from different runs even if
    a tiny overlap happens to exist.
    """
    health_root = Path(health_dir)
    rows = {}
    missing = []
    starts = []
    stops = []
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        path = health_root / (venue + ".json")
        if not path.exists():
            missing.append(venue)
            continue
        data = json.loads(path.read_text())
        start_ns = int(data.get("started_ns", 0) or 0)
        stop_ns = int(data.get("stopped_ns", 0) or 0)
        rows[venue] = data
        if start_ns <= 0 or stop_ns <= start_ns:
            missing.append(venue + ":invalid_window")
            continue
        starts.append(start_ns)
        stops.append(stop_ns)
    if missing:
        raise ValueError("missing/invalid health windows: %s" % ",".join(sorted(missing)))
    overlap_start_ns = max(starts)
    overlap_stop_ns = min(stops)
    if overlap_stop_ns <= overlap_start_ns:
        raise ValueError("no concurrent venue window; run all required venues simultaneously")
    start_skew_ms = (max(starts) - min(starts)) / 1e6
    if start_skew_ms > float(max_start_skew_ms):
        raise ValueError(
            "venue health files do not look like one concurrent run: start_skew_ms=%.3f > %.3f"
            % (start_skew_ms, float(max_start_skew_ms))
        )
    return {
        "started_ns": int(overlap_start_ns),
        "stopped_ns": int(overlap_stop_ns),
        "duration_s": float((overlap_stop_ns - overlap_start_ns) / 1e9),
        "start_skew_ms": float(start_skew_ms),
        "venues": [str(v).lower() for v in venues],
        "health": rows,
    }


def _iter_jsonl(paths: Iterable[Path], start_ns: int, stop_ns: int):
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
                if receive_ns < int(start_ns) or receive_ns > int(stop_ns):
                    continue
                yield row


def _book_paths(root: Path, venue: str, symbol: str):
    return (root / "raw" / "book_events" / ("venue=" + venue) / ("symbol=" + symbol)).glob(
        "date=*/events.jsonl"
    )


def book_event_from_record(row: Mapping[str, object]) -> BookEvent:
    return BookEvent(
        venue=str(row["venue"]),
        symbol=str(row["symbol"]),
        event_ts_ns=int(row["event_ts_ns"]),
        receive_ts_ns=int(row["receive_ts_ns"]),
        sequence_id=int(row.get("sequence_id", 0) or 0),
        event_type=str(row["event_type"]),
        side=str(row["side"]),
        price=float(row["price"]),
        qty=float(row["qty"]),
        order_count=(None if row.get("order_count") is None else int(row["order_count"])),
        source_stream=(None if row.get("source_stream") is None else str(row["source_stream"])),
        first_sequence_id=(None if row.get("first_sequence_id") is None else int(row["first_sequence_id"])),
        previous_sequence_id=(None if row.get("previous_sequence_id") is None else int(row["previous_sequence_id"])),
    )


def load_book_events(
    root: str,
    start_ns: int,
    stop_ns: int,
    venues: Sequence[str] = DEFAULT_VENUES,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
) -> List[BookEvent]:
    base = Path(root)
    events = []
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        for symbol_raw in symbols:
            symbol = str(symbol_raw).upper()
            for row in _iter_jsonl(_book_paths(base, venue, symbol), start_ns, stop_ns):
                events.append(book_event_from_record(row))
    events.sort(key=lambda x: (int(x.receive_ts_ns), int(x.event_ts_ns), int(x.sequence_id), x.venue, x.symbol, x.side))
    return events


def _grid(start_ns: int, stop_ns: int, cadence_ms: int):
    step_ns = int(cadence_ms) * 1_000_000
    if step_ns <= 0:
        raise ValueError("cadence_ms must be positive")
    t = int(start_ns) + step_ns
    while t <= int(stop_ns):
        yield t
        t += step_ns


def build_state_tape(
    events: Sequence[BookEvent],
    start_ns: int,
    stop_ns: int,
    cadence_ms: int,
    venues: Sequence[str] = DEFAULT_VENUES,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    max_receive_age_ms: float = 1500.0,
    max_transport_lag_ms: float = 5000.0,
    max_sync_span_ms: float = 1000.0,
) -> pd.DataFrame:
    """Replay book events by receive time and sample causal synchronized states."""
    ordered = sorted(
        events,
        key=lambda x: (int(x.receive_ts_ns), int(x.event_ts_ns), int(x.sequence_id), x.venue, x.symbol, x.side),
    )
    engine = SynchronizedBookEngine()
    cursor = 0
    previous_snapshots: Dict[Tuple[str, str], object] = {}
    rows = []

    for asof_ns in _grid(start_ns, stop_ns, cadence_ms):
        while cursor < len(ordered) and int(ordered[cursor].receive_ts_ns) <= int(asof_ns):
            engine.ingest(ordered[cursor])
            cursor += 1

        for symbol_raw in symbols:
            symbol = str(symbol_raw).upper()
            state = engine.state(
                symbol=symbol,
                asof_ns=asof_ns,
                required_venues=venues,
                require_deep=True,
                max_receive_age_ms=max_receive_age_ms,
                max_transport_lag_ms=max_transport_lag_ms,
                max_sync_span_ms=max_sync_span_ms,
                min_venues=len(venues),
            )
            row = {
                "asof_ns": int(asof_ns),
                "symbol": symbol,
                "cadence_ms": int(cadence_ms),
                "ready": bool(state.ready),
                "sync_span_ms": float(state.sync_span_ms),
                "fair_value": float(state.fair_value),
                "dispersion_bps": float(state.dispersion_bps),
                "venues_used": ",".join(state.venues_used),
                "venues_missing": ",".join(state.venues_missing),
                "reasons": ",".join(state.reasons),
            }
            for venue_raw in venues:
                venue = str(venue_raw).lower()
                prefix = venue + "__"
                book = engine.books.get((venue, symbol))
                if book is None:
                    continue
                snap = book.snapshot(prefer_deep=True)
                if snap is None or snap.available_ts_ns > asof_ns:
                    continue
                for key, value in book_feature_vector(snap).items():
                    row[prefix + key] = float(value)
                for key, value in book.fragmentation_features().items():
                    row[prefix + key] = float(value)
                row[prefix + "receive_age_ms"] = float((asof_ns - snap.available_ts_ns) / 1e6)
                row[prefix + "transport_lag_ms"] = float(snap.transport_lag_ms)
                row[prefix + "dislocation_bps"] = float(state.dislocation_bps.get(venue, float("nan")))
                row[prefix + "weight"] = float(state.weights.get(venue, 0.0))
                prev_key = (venue, symbol)
                previous = previous_snapshots.get(prev_key)
                row[prefix + "ofi_l1_grid"] = (
                    float(top_of_book_ofi(previous, snap)) if previous is not None else float("nan")
                )
                previous_snapshots[prev_key] = snap
            rows.append(row)
    return pd.DataFrame(rows)


def _token_counts(series: pd.Series) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for raw in series.fillna("").astype(str):
        for token in [x.strip() for x in raw.split(",") if x.strip()]:
            counts[token] = counts.get(token, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: (-x[1], x[0])))


def _finite_quantiles(series: pd.Series) -> Dict[str, float]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    values = values[values.map(lambda x: float(x) != float("inf"))]
    if values.empty:
        return {}
    return {
        "p50": float(values.quantile(0.50)),
        "p90": float(values.quantile(0.90)),
        "p95": float(values.quantile(0.95)),
        "p99": float(values.quantile(0.99)),
        "max": float(values.max()),
    }


def state_tape_summary(frame: pd.DataFrame, window: Mapping[str, object], cadence_ms: int) -> Dict[str, object]:
    if frame.empty:
        ready_rows = 0
        total_rows = 0
        by_symbol = {}
        reason_counts = {}
        missing_venue_counts = {}
        receive_age_quantiles = {}
        sync_span_quantiles = {}
    else:
        total_rows = int(len(frame))
        ready_rows = int(frame["ready"].astype(bool).sum())
        rejected = frame.loc[~frame["ready"].astype(bool)]
        reason_counts = _token_counts(rejected["reasons"]) if "reasons" in rejected else {}
        missing_venue_counts = _token_counts(rejected["venues_missing"]) if "venues_missing" in rejected else {}
        sync_span_quantiles = _finite_quantiles(frame["sync_span_ms"]) if "sync_span_ms" in frame else {}
        receive_age_quantiles = {}
        for col in sorted(x for x in frame.columns if x.endswith("__receive_age_ms")):
            receive_age_quantiles[col[: -len("__receive_age_ms")]] = _finite_quantiles(frame[col])

        by_symbol = {}
        for symbol, group in frame.groupby("symbol"):
            rejected_symbol = group.loc[~group["ready"].astype(bool)]
            by_symbol[str(symbol)] = {
                "rows": int(len(group)),
                "ready_rows": int(group["ready"].astype(bool).sum()),
                "ready_fraction": float(group["ready"].astype(bool).mean()),
                "median_sync_span_ms": float(group.loc[group["ready"], "sync_span_ms"].median()) if group["ready"].any() else None,
                "rejection_reason_counts": _token_counts(rejected_symbol["reasons"]) if "reasons" in rejected_symbol else {},
                "missing_venue_counts": _token_counts(rejected_symbol["venues_missing"]) if "venues_missing" in rejected_symbol else {},
            }
    return {
        "window": {
            "started_ns": int(window["started_ns"]),
            "stopped_ns": int(window["stopped_ns"]),
            "duration_s": float(window["duration_s"]),
            "start_skew_ms": float(window["start_skew_ms"]),
        },
        "cadence_ms": int(cadence_ms),
        "rows": total_rows,
        "ready_rows": ready_rows,
        "ready_fraction": float(ready_rows / total_rows) if total_rows else 0.0,
        "rejection_reason_counts": reason_counts,
        "missing_venue_counts": missing_venue_counts,
        "sync_span_ms_quantiles": sync_span_quantiles,
        "receive_age_ms_quantiles": receive_age_quantiles,
        "by_symbol": by_symbol,
    }
