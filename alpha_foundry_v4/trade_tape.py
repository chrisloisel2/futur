from __future__ import annotations

import heapq
import json
import tempfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Iterable, Iterator, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

from market_physics_v3.schema import TradeEvent
from market_physics_v3.state_tape import DEFAULT_SYMBOLS, DEFAULT_VENUES
from market_physics_v3.state_tape_stream import _iter_filtered_lines, _receive_ordered


EXTERNAL_SORT_CHUNK_ROWS = 200_000
DEFAULT_TIME_WINDOWS_MS = (100, 500, 2000, 10000, 60000)
DEFAULT_EVENT_WINDOWS = (10, 50, 250)


def trade_event_from_record(row: Mapping[str, object]) -> TradeEvent:
    return TradeEvent(venue=str(row["venue"]), symbol=str(row["symbol"]), event_ts_ns=int(row["event_ts_ns"]), receive_ts_ns=int(row["receive_ts_ns"]), trade_id=str(row["trade_id"]), price=float(row["price"]), qty=float(row["qty"]), aggressor=str(row["aggressor"]), buyer=None if row.get("buyer") is None else str(row["buyer"]), seller=None if row.get("seller") is None else str(row["seller"]), tx_hash=None if row.get("tx_hash") is None else str(row["tx_hash"]), source_stream=None if row.get("source_stream") is None else str(row["source_stream"]), granularity=None if row.get("granularity") is None else str(row["granularity"]))


def _paths(root: Path, venue: str, symbol: str):
    return (root / "raw" / "trades" / ("venue=" + venue) / ("symbol=" + symbol)).glob("date=*/events.jsonl")


def _direct_iter(path: Path, start_ns: int, stop_ns: int) -> Iterator[TradeEvent]:
    for _receive_ns, line in _iter_filtered_lines((path,), start_ns, stop_ns):
        try:
            yield trade_event_from_record(json.loads(line))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue


def _external_sorted_iter(path: Path, start_ns: int, stop_ns: int, chunk_rows: int = EXTERNAL_SORT_CHUNK_ROWS) -> Iterator[TradeEvent]:
    chunk_rows = max(1, int(chunk_rows))
    with tempfile.TemporaryDirectory(prefix="afv4-trade-sort-") as tmp_name:
        tmp = Path(tmp_name)
        runs = []
        chunk = []
        run_id = 0
        def flush():
            nonlocal chunk, run_id
            if not chunk:
                return
            chunk.sort(key=lambda item: item[0])
            run_path = tmp / ("run-%05d.jsonl" % run_id)
            with run_path.open("w", encoding="utf-8") as out:
                for _receive_ns, line in chunk:
                    out.write(line + "\n")
            runs.append(run_path)
            run_id += 1
            chunk = []
        for receive_ns, line in _iter_filtered_lines((path,), start_ns, stop_ns):
            chunk.append((int(receive_ns), line))
            if len(chunk) >= chunk_rows:
                flush()
        flush()
        streams = [_direct_iter(run_path, start_ns, stop_ns) for run_path in runs]
        for event in heapq.merge(*streams, key=lambda x: int(x.receive_ts_ns)):
            yield event


def _file_iter(path: Path, start_ns: int, stop_ns: int) -> Iterator[TradeEvent]:
    if _receive_ordered((path,), start_ns, stop_ns):
        for event in _direct_iter(path, start_ns, stop_ns):
            yield event
        return
    for event in _external_sorted_iter(path, start_ns, stop_ns):
        yield event


def _partition_iter(paths: Iterable[Path], start_ns: int, stop_ns: int) -> Iterator[TradeEvent]:
    physical = tuple(sorted(path for path in paths if path.is_file()))
    streams = [_file_iter(path, start_ns, stop_ns) for path in physical]
    for event in heapq.merge(*streams, key=lambda x: int(x.receive_ts_ns)):
        yield event


def iter_merged_trades(root: str, start_ns: int, stop_ns: int, venues: Sequence[str] = DEFAULT_VENUES, symbols: Sequence[str] = DEFAULT_SYMBOLS) -> Iterator[TradeEvent]:
    base = Path(root)
    streams = []
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        for symbol_raw in symbols:
            symbol = str(symbol_raw).upper()
            streams.append(_partition_iter(_paths(base, venue, symbol), start_ns, stop_ns))
    return heapq.merge(*streams, key=lambda x: int(x.receive_ts_ns))


def _trade_row(event: TradeEvent) -> Dict[str, object]:
    side_sign = 1.0 if event.aggressor == "buy" else -1.0
    return {"receive_ts_ns": int(event.receive_ts_ns), "event_ts_ns": int(event.event_ts_ns), "price": float(event.price), "notional": float(event.price * event.qty), "side_sign": side_sign, "granularity": event.granularity or "unknown", "trade_id": event.trade_id}


def _window_features(rows: Sequence[Dict[str, object]], duration_s: float) -> Dict[str, float]:
    if not rows:
        return {"trade_count": 0.0, "gross_notional": 0.0, "signed_notional": 0.0, "flow_imbalance": float("nan"), "large_trade_fraction": float("nan"), "trade_size_entropy": float("nan"), "trades_per_second": 0.0, "interarrival_cv": float("nan"), "impact_bps": float("nan"), "absorption": float("nan"), "individual_fraction": float("nan"), "aggregate_fraction": float("nan")}
    notional = np.asarray([float(row["notional"]) for row in rows], dtype=float)
    side = np.asarray([float(row["side_sign"]) for row in rows], dtype=float)
    ts = np.asarray([int(row["receive_ts_ns"]) for row in rows], dtype=np.int64)
    prices = np.asarray([float(row["price"]) for row in rows], dtype=float)
    signed = float(np.sum(notional * side))
    gross = float(np.sum(notional))
    weights = notional / gross if gross > 0 else np.zeros_like(notional)
    entropy = float(-np.sum(weights[weights > 0] * np.log(weights[weights > 0]))) if gross > 0 else float("nan")
    q90 = float(np.quantile(notional, 0.90)) if len(notional) else float("nan")
    dt = np.diff(ts.astype(float)) / 1e9
    interarrival_cv = float(np.std(dt) / np.mean(dt)) if len(dt) and np.mean(dt) > 0 else float("nan")
    impact = float(1e4 * np.log(prices[-1] / prices[0])) if len(prices) >= 2 and prices[0] > 0 and prices[-1] > 0 else 0.0
    granularity = [str(row.get("granularity", "unknown")) for row in rows]
    return {"trade_count": float(len(rows)), "gross_notional": gross, "signed_notional": signed, "flow_imbalance": float(signed / gross) if gross > 0 else float("nan"), "large_trade_fraction": float(np.mean(notional >= q90)) if np.isfinite(q90) else float("nan"), "trade_size_entropy": entropy, "trades_per_second": float(len(rows) / max(float(duration_s), 1e-9)), "interarrival_cv": interarrival_cv, "impact_bps": impact, "absorption": float(abs(signed) / (abs(impact) + 1e-9)), "individual_fraction": float(sum(g == "individual" for g in granularity) / len(granularity)), "aggregate_fraction": float(sum(g == "aggregate" for g in granularity) / len(granularity))}


class TradeTapeBuilder:
    def __init__(self, venues: Sequence[str] = DEFAULT_VENUES, symbols: Sequence[str] = DEFAULT_SYMBOLS, time_windows_ms: Sequence[int] = DEFAULT_TIME_WINDOWS_MS, event_windows: Sequence[int] = DEFAULT_EVENT_WINDOWS):
        self.venues = tuple(str(v).lower() for v in venues)
        self.symbols = tuple(str(s).upper() for s in symbols)
        self.time_windows_ms = tuple(sorted({int(v) for v in time_windows_ms}))
        self.event_windows = tuple(sorted({int(v) for v in event_windows}))
        self.max_time_ns = max(self.time_windows_ms) * 1_000_000
        self.max_events = max(self.event_windows)
        self.rows: Dict[Tuple[str, str], Deque[Dict[str, object]]] = defaultdict(deque)
        self.cvd: Dict[Tuple[str, str], float] = defaultdict(float)
        self.previous_flow: Dict[Tuple[str, str, int], float] = {}
        self.previous_accel: Dict[Tuple[str, str, int], float] = {}

    def ingest(self, event: TradeEvent) -> None:
        key = (event.venue.lower(), event.symbol.upper())
        row = _trade_row(event)
        self.rows[key].append(row)
        self.cvd[key] += float(row["notional"]) * float(row["side_sign"])
        while len(self.rows[key]) > self.max_events * 20:
            self.rows[key].popleft()

    def _trim(self, key: Tuple[str, str], asof_ns: int) -> None:
        cutoff = int(asof_ns) - int(self.max_time_ns)
        q = self.rows[key]
        while q and int(q[0]["receive_ts_ns"]) < cutoff and len(q) > self.max_events:
            q.popleft()

    def state(self, asof_ns: int, symbol: str) -> Dict[str, object]:
        symbol = str(symbol).upper()
        out: Dict[str, object] = {"asof_ns": int(asof_ns), "symbol": symbol}
        cross_signed = defaultdict(float)
        for venue in self.venues:
            key = (venue, symbol)
            self._trim(key, asof_ns)
            rows = list(self.rows[key])
            prefix = venue + "__"
            out[prefix + "cvd"] = float(self.cvd[key])
            for window_ms in self.time_windows_ms:
                cutoff = int(asof_ns) - int(window_ms) * 1_000_000
                selected = [row for row in rows if cutoff < int(row["receive_ts_ns"]) <= int(asof_ns)]
                features = _window_features(selected, duration_s=float(window_ms) / 1000.0)
                for name, value in features.items():
                    out[prefix + name + "_%sms" % window_ms] = value
                flow = float(features["signed_notional"])
                flow_key = (venue, symbol, int(window_ms))
                previous = self.previous_flow.get(flow_key)
                accel = float("nan") if previous is None else flow - previous
                previous_accel = self.previous_accel.get(flow_key)
                jerk = float("nan") if previous_accel is None or not np.isfinite(accel) else accel - previous_accel
                out[prefix + "flow_acceleration_%sms" % window_ms] = accel
                out[prefix + "flow_jerk_%sms" % window_ms] = jerk
                self.previous_flow[flow_key] = flow
                if np.isfinite(accel):
                    self.previous_accel[flow_key] = accel
                cross_signed[window_ms] += flow
            for n in self.event_windows:
                selected = rows[-n:]
                duration_s = max((int(selected[-1]["receive_ts_ns"]) - int(selected[0]["receive_ts_ns"])) / 1e9, 1e-9) if len(selected) >= 2 else 1e-9
                features = _window_features(selected, duration_s=duration_s)
                for name, value in features.items():
                    out[prefix + name + "_last%s" % n] = value
        for window_ms, value in cross_signed.items():
            out["cross__signed_notional_%sms" % window_ms] = float(value)
        return out


def build_trade_tape(events: Iterator[TradeEvent], start_ns: int, stop_ns: int, cadence_ms: int = 100, venues: Sequence[str] = DEFAULT_VENUES, symbols: Sequence[str] = DEFAULT_SYMBOLS) -> Iterator[Dict[str, object]]:
    builder = TradeTapeBuilder(venues=venues, symbols=symbols)
    event = next(events, None)
    step_ns = int(cadence_ms) * 1_000_000
    t = int(start_ns) + step_ns
    while t <= int(stop_ns):
        while event is not None and int(event.receive_ts_ns) <= t:
            builder.ingest(event)
            event = next(events, None)
        for symbol in symbols:
            row = builder.state(t, symbol)
            row["cadence_ms"] = int(cadence_ms)
            yield row
        t += step_ns


def write_trade_tape(rows: Iterator[Dict[str, object]], out_dir: str, chunk_rows: int = 50000) -> Dict[str, object]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    chunk = []
    part = 0
    total = 0
    columns = set()
    for row in rows:
        chunk.append(row)
        columns.update(row.keys())
        if len(chunk) >= int(chunk_rows):
            pd.DataFrame(chunk).to_parquet(root / ("part-%05d.parquet" % part), index=False)
            total += len(chunk)
            part += 1
            chunk = []
    if chunk:
        pd.DataFrame(chunk).to_parquet(root / ("part-%05d.parquet" % part), index=False)
        total += len(chunk)
        part += 1
    (root / "_SUCCESS").write_text("ok\n")
    summary = {"rows": int(total), "parts": int(part), "columns": sorted(columns)}
    (root / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return summary
