from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterator, Mapping, Sequence, Tuple

import numpy as np

from .common import ChunkedPlaneWriter, infer_run_window, iter_base_key_chunks, iter_causal_records

DEFAULT_LIQ_WINDOWS_MS = (1000, 10000, 60000, 300000)


@dataclass
class _Liquidation:
    ts_ns: int
    long_usd: float
    short_usd: float


class _RollingLiquidationWindow:
    def __init__(self, window_ms: int):
        self.window_ns = int(window_ms) * 1_000_000
        self.rows: Deque[_Liquidation] = deque()
        self.long_usd = 0.0
        self.short_usd = 0.0

    def add(self, row: _Liquidation) -> None:
        self.rows.append(row)
        self.long_usd += row.long_usd
        self.short_usd += row.short_usd

    def trim(self, asof_ns: int) -> None:
        cutoff = int(asof_ns) - self.window_ns
        while self.rows and self.rows[0].ts_ns <= cutoff:
            row = self.rows.popleft()
            self.long_usd -= row.long_usd
            self.short_usd -= row.short_usd

    def features(self, asof_ns: int) -> Dict[str, float]:
        self.trim(asof_ns)
        total = self.long_usd + self.short_usd
        imbalance = (self.short_usd - self.long_usd) / total if total > 0 else float("nan")
        return {
            "liquidation_long_usd": float(self.long_usd),
            "liquidation_short_usd": float(self.short_usd),
            "liquidation_total_usd": float(total),
            "liquidation_imbalance": float(imbalance),
        }


class DerivativesPlaneState:
    def __init__(self, venues: Sequence[str], symbols: Sequence[str], liquidation_windows_ms: Sequence[int] = DEFAULT_LIQ_WINDOWS_MS):
        self.venues = tuple(str(v).lower() for v in venues)
        self.symbols = tuple(str(s).upper() for s in symbols)
        self.windows_ms = tuple(sorted({int(x) for x in liquidation_windows_ms}))
        self.latest: Dict[Tuple[str, str, str], float] = {}
        self.latest_ts: Dict[Tuple[str, str, str], int] = defaultdict(int)
        self.oi_change_pct: Dict[Tuple[str, str], float] = defaultdict(lambda: float("nan"))
        self.liquidations = {(v, s, w): _RollingLiquidationWindow(w) for v in self.venues for s in self.symbols for w in self.windows_ms}
        self.records = 0

    def ingest(self, row: Mapping[str, object]) -> None:
        venue = str(row.get("venue", "")).lower()
        symbol = str(row.get("symbol", "")).upper()
        kind = str(row.get("kind", ""))
        receive_ns = int(row.get("receive_ts_ns", 0) or 0)
        if venue not in self.venues or symbol not in self.symbols or receive_ns <= 0:
            return
        if kind == "liquidation":
            value = abs(float(row.get("value", 0.0) or 0.0))
            side = str(row.get("side") or "")
            long_usd = value if side in {"long", "sell"} else 0.0
            short_usd = value if side in {"short", "buy"} else 0.0
            liq = _Liquidation(receive_ns, long_usd, short_usd)
            for window_ms in self.windows_ms:
                self.liquidations[(venue, symbol, window_ms)].add(liq)
            self.latest_ts[(venue, symbol, "liquidation")] = receive_ns
            self.records += 1
            return
        if kind not in {"open_interest", "funding", "mark", "index", "premium"}:
            return
        value = float(row.get("value", float("nan")))
        if not np.isfinite(value):
            return
        key = (venue, symbol, kind)
        if kind == "open_interest":
            old = self.latest.get(key)
            if old is not None and old > 0:
                self.oi_change_pct[(venue, symbol)] = float(value / old - 1.0)
        self.latest[key] = value
        self.latest_ts[key] = receive_ns
        self.records += 1

    def row(self, asof_ns: int, symbol: str) -> Dict[str, object]:
        symbol = str(symbol).upper()
        out: Dict[str, object] = {"asof_ns": int(asof_ns), "symbol": symbol}
        basis_values = []
        oi_changes = []
        latest_any = 0
        for venue in self.venues:
            prefix = venue + "__"
            for kind in ("open_interest", "funding", "mark", "index", "premium"):
                key = (venue, symbol, kind)
                value = self.latest.get(key)
                ts = self.latest_ts.get(key, 0)
                out[prefix + kind] = float(value) if value is not None else np.nan
                out[prefix + kind + "_available_ts_ns"] = int(ts) if ts else np.nan
                latest_any = max(latest_any, ts)
            oi_delta = self.oi_change_pct[(venue, symbol)]
            out[prefix + "open_interest_change_pct"] = float(oi_delta)
            if np.isfinite(oi_delta):
                oi_changes.append(float(oi_delta))
            mark = self.latest.get((venue, symbol, "mark"))
            index = self.latest.get((venue, symbol, "index"))
            basis = 1e4 * (mark - index) / index if mark is not None and index is not None and index > 0 else float("nan")
            out[prefix + "basis_bps"] = float(basis)
            if np.isfinite(basis):
                basis_values.append(float(basis))
            liq_ts = self.latest_ts.get((venue, symbol, "liquidation"), 0)
            out[prefix + "liquidation_available_ts_ns"] = int(liq_ts) if liq_ts else np.nan
            latest_any = max(latest_any, liq_ts)
            for window_ms in self.windows_ms:
                features = self.liquidations[(venue, symbol, window_ms)].features(asof_ns)
                for name, value in features.items():
                    out[prefix + name + "_%sms" % window_ms] = value
        out["deriv__available_ts_ns"] = int(latest_any) if latest_any else np.nan
        out["deriv__basis_dispersion_bps"] = float(np.std(basis_values, ddof=0)) if basis_values else np.nan
        out["deriv__median_oi_change_pct"] = float(np.median(oi_changes)) if oi_changes else np.nan
        return out


def _next_or_none(iterator: Iterator[Mapping[str, object]]):
    try:
        return next(iterator)
    except StopIteration:
        return None


def build_derivatives_plane(base_tape: str, raw_root: str, out_dir: str, venues: Sequence[str], symbols: Sequence[str], liquidation_windows_ms: Sequence[int] = DEFAULT_LIQ_WINDOWS_MS, chunk_rows: int = 50000) -> Mapping[str, object]:
    start_ns, stop_ns = infer_run_window(base_tape)
    events = iter_causal_records(raw_root, "derivatives", start_ns, stop_ns, venues, symbols)
    event = _next_or_none(events)
    state = DerivativesPlaneState(venues, symbols, liquidation_windows_ms)
    writer = ChunkedPlaneWriter(out_dir, chunk_rows=chunk_rows)
    last_asof = -1
    for keys in iter_base_key_chunks(base_tape):
        for asof_ns, symbol in keys[["asof_ns", "symbol"]].itertuples(index=False, name=None):
            asof_ns = int(asof_ns)
            if asof_ns < last_asof:
                raise ValueError("base tape must be globally sorted by asof_ns")
            last_asof = asof_ns
            while event is not None and int(event.get("receive_ts_ns", 0) or 0) <= asof_ns:
                state.ingest(event)
                event = _next_or_none(events)
            writer.append(state.row(asof_ns, str(symbol)))
    return writer.close({"plane": "derivatives", "start_ns": start_ns, "stop_ns": stop_ns, "records": state.records, "liquidation_windows_ms": list(state.windows_ms), "funding_clock_materialized": False})
