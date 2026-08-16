from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import heapq
from typing import Deque, Dict, Iterator, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

from .common import ChunkedPlaneWriter, base_part_paths, infer_run_window, iter_causal_records

DEFAULT_FLOW_WINDOWS_MS = (1000, 10000, 60000, 300000)
DEFAULT_SCORE_HORIZONS_MS = (5000, 30000, 300000)


@dataclass
class _WalletStats:
    n: int = 0
    sum_markout_bps: float = 0.0

    def update(self, markout_bps: float) -> None:
        if np.isfinite(markout_bps):
            self.n += 1
            self.sum_markout_bps += float(markout_bps)

    def posterior_mean(self, prior_trades: float) -> float:
        return float(self.sum_markout_bps / (self.n + max(float(prior_trades), 1e-9)))


@dataclass
class _PendingMarkout:
    maturity_ns: int
    wallet: str
    sign: float
    trade_price: float
    horizon_ms: int


@dataclass
class _FlowEntry:
    ts_ns: int
    wallet: str
    signed_notional: float
    notional: float
    score_bps: float
    scored: bool


class WalletIntelligenceState:
    def __init__(self, symbols: Sequence[str], flow_windows_ms: Sequence[int] = DEFAULT_FLOW_WINDOWS_MS, score_horizons_ms: Sequence[int] = DEFAULT_SCORE_HORIZONS_MS, prior_trades: float = 20.0, min_scored_trades: int = 5):
        self.symbols = tuple(str(s).upper() for s in symbols)
        self.flow_windows_ms = tuple(sorted({int(x) for x in flow_windows_ms}))
        self.score_horizons_ms = tuple(sorted({int(x) for x in score_horizons_ms}))
        self.prior_trades = float(prior_trades)
        self.min_scored_trades = int(min_scored_trades)
        self.stats: Dict[Tuple[str, int], _WalletStats] = defaultdict(_WalletStats)
        self.pending: Dict[str, list] = {s: [] for s in self.symbols}
        self.flow: Dict[str, Deque[_FlowEntry]] = {s: deque() for s in self.symbols}
        self.latest_trade_receive: Dict[str, int] = defaultdict(int)
        self.latest_score_update: Dict[str, int] = defaultdict(int)
        self.identity_trades: Dict[str, int] = defaultdict(int)
        self.total_trades: Dict[str, int] = defaultdict(int)

    def _score(self, wallet: str) -> Tuple[float, bool]:
        means = []
        weights = []
        total_n = 0
        for horizon_ms in self.score_horizons_ms:
            stat = self.stats[(wallet, horizon_ms)]
            total_n += stat.n
            if stat.n:
                reliability = stat.n / (stat.n + self.prior_trades)
                means.append(stat.posterior_mean(self.prior_trades))
                weights.append(reliability)
        if not weights or sum(weights) <= 0:
            return 0.0, False
        score = float(np.average(np.asarray(means), weights=np.asarray(weights)))
        return score, total_n >= self.min_scored_trades

    @staticmethod
    def aggressor_wallet(row: Mapping[str, object]) -> str:
        aggressor = str(row.get("aggressor", ""))
        wallet = row.get("buyer") if aggressor == "buy" else row.get("seller") if aggressor == "sell" else None
        if wallet is None:
            return ""
        wallet = str(wallet).strip().lower()
        return wallet if wallet.startswith("0x") and len(wallet) == 42 else ""

    def ingest_trade(self, row: Mapping[str, object]) -> None:
        if str(row.get("venue", "")).lower() != "hyperliquid":
            return
        symbol = str(row.get("symbol", "")).upper()
        if symbol not in self.symbols:
            return
        ts_ns = int(row.get("receive_ts_ns", 0) or 0)
        price = float(row.get("price", 0.0) or 0.0)
        qty = float(row.get("qty", 0.0) or 0.0)
        aggressor = str(row.get("aggressor", ""))
        if ts_ns <= 0 or price <= 0 or qty <= 0 or aggressor not in {"buy", "sell"}:
            return
        self.total_trades[symbol] += 1
        self.latest_trade_receive[symbol] = max(self.latest_trade_receive[symbol], ts_ns)
        wallet = self.aggressor_wallet(row)
        if not wallet:
            return
        self.identity_trades[symbol] += 1
        sign = 1.0 if aggressor == "buy" else -1.0
        notional = price * qty
        score_bps, scored = self._score(wallet)
        self.flow[symbol].append(_FlowEntry(ts_ns, wallet, sign * notional, notional, score_bps, scored))
        for horizon_ms in self.score_horizons_ms:
            pending = _PendingMarkout(ts_ns + int(horizon_ms) * 1_000_000, wallet, sign, price, int(horizon_ms))
            heapq.heappush(self.pending[symbol], (pending.maturity_ns, pending.wallet, pending.horizon_ms, pending))

    def mature(self, asof_ns: int, symbol: str, fair_value: float) -> None:
        symbol = str(symbol).upper()
        if symbol not in self.pending or not np.isfinite(fair_value) or fair_value <= 0:
            return
        heap = self.pending[symbol]
        while heap and int(heap[0][0]) <= int(asof_ns):
            _maturity, _wallet, _horizon, pending = heapq.heappop(heap)
            markout = pending.sign * 1e4 * (float(fair_value) / pending.trade_price - 1.0)
            self.stats[(pending.wallet, pending.horizon_ms)].update(markout)
            self.latest_score_update[symbol] = max(self.latest_score_update[symbol], int(asof_ns))

    def row(self, asof_ns: int, symbol: str) -> Dict[str, object]:
        symbol = str(symbol).upper()
        out: Dict[str, object] = {"asof_ns": int(asof_ns), "symbol": symbol}
        latest_trade = self.latest_trade_receive[symbol]
        latest_score = self.latest_score_update[symbol]
        out["wallet__available_ts_ns"] = int(latest_trade) if latest_trade else np.nan
        out["wallet__score_available_ts_ns"] = int(latest_score) if latest_score else np.nan
        q = self.flow[symbol]
        max_window_ns = max(self.flow_windows_ms) * 1_000_000
        cutoff_all = int(asof_ns) - max_window_ns
        while q and q[0].ts_ns <= cutoff_all:
            q.popleft()
        rows = list(q)
        for window_ms in self.flow_windows_ms:
            cutoff = int(asof_ns) - int(window_ms) * 1_000_000
            selected = [x for x in rows if cutoff < x.ts_ns <= int(asof_ns)]
            gross = float(sum(x.notional for x in selected))
            scored_gross = float(sum(x.notional for x in selected if x.scored))
            weighted = float(sum(x.signed_notional * x.score_bps for x in selected if x.scored))
            signed = float(sum(x.signed_notional for x in selected))
            out["wallet__identity_flow_notional_%sms" % window_ms] = gross if selected else np.nan
            out["wallet__scored_flow_coverage_%sms" % window_ms] = scored_gross / gross if gross > 0 else np.nan
            out["wallet__score_weighted_flow_bps_%sms" % window_ms] = weighted / scored_gross if scored_gross > 0 else np.nan
            out["wallet__signed_notional_%sms" % window_ms] = signed if selected else np.nan
            out["wallet__unique_aggressors_%sms" % window_ms] = float(len({x.wallet for x in selected})) if selected else np.nan
        total = self.total_trades[symbol]
        out["wallet__identity_trade_fraction"] = float(self.identity_trades[symbol] / total) if total else np.nan
        return out


def _base_rows(base_tape: str) -> Iterator[Tuple[int, str, float]]:
    for path in base_part_paths(base_tape):
        frame = pd.read_parquet(path)
        price_col = "price_fair_value" if "price_fair_value" in frame.columns else "fair_value"
        for asof_ns, symbol, fair_value in frame[["asof_ns", "symbol", price_col]].itertuples(index=False, name=None):
            yield int(asof_ns), str(symbol), float(fair_value)


def _next_or_none(iterator: Iterator[Mapping[str, object]]):
    try:
        return next(iterator)
    except StopIteration:
        return None


def build_wallet_plane(base_tape: str, raw_root: str, out_dir: str, symbols: Sequence[str], flow_windows_ms: Sequence[int] = DEFAULT_FLOW_WINDOWS_MS, score_horizons_ms: Sequence[int] = DEFAULT_SCORE_HORIZONS_MS, chunk_rows: int = 50000) -> Mapping[str, object]:
    start_ns, stop_ns = infer_run_window(base_tape)
    trades = iter_causal_records(raw_root, "trades", start_ns, stop_ns, ["hyperliquid"], symbols)
    trade = _next_or_none(trades)
    state = WalletIntelligenceState(symbols, flow_windows_ms, score_horizons_ms)
    writer = ChunkedPlaneWriter(out_dir, chunk_rows=chunk_rows)
    last_asof = -1
    for asof_ns, symbol, fair_value in _base_rows(base_tape):
        if asof_ns < last_asof:
            raise ValueError("base tape must be globally sorted by asof_ns")
        last_asof = asof_ns
        while trade is not None and int(trade.get("receive_ts_ns", 0) or 0) <= asof_ns:
            state.ingest_trade(trade)
            trade = _next_or_none(trades)
        state.mature(asof_ns, symbol, fair_value)
        writer.append(state.row(asof_ns, symbol))
    identity_total = int(sum(state.identity_trades.values()))
    trade_total = int(sum(state.total_trades.values()))
    return writer.close({"plane": "wallet", "start_ns": start_ns, "stop_ns": stop_ns, "score_horizons_ms": list(state.score_horizons_ms), "flow_windows_ms": list(state.flow_windows_ms), "hyperliquid_trade_records": trade_total, "identified_aggressor_records": identity_total, "identity_fraction": float(identity_total / trade_total) if trade_total else 0.0})
