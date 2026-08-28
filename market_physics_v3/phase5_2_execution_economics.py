from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from alpha_foundry_v5.validation import max_drawdown as _max_drawdown
from alpha_foundry_v5.validation import profit_factor as _profit_factor

from .phase5_mechanism import _loo_fair_value

TOP_CONTRIBUTOR_TRIM = 0.05  # remove the best 5% of trades by net PnL


def summarize_trades(trades: Sequence[Trade]) -> dict[str, object]:
    if not trades:
        raise ValueError("no trades produced -- cannot summarize")

    gross = np.array([t.gross_bps for t in trades], dtype=float)
    net = np.array([t.net_bps for t in trades], dtype=float)
    net_x2 = np.array([t.net_cost_x2_bps for t in trades], dtype=float)
    capacity = np.array([t.capacity_usd for t in trades], dtype=float)
    delayed_net = np.array([t.delayed_net_bps for t in trades], dtype=float)
    delayed_net = delayed_net[np.isfinite(delayed_net)]

    order = np.argsort(net)[::-1]
    n_trim = max(1, round(len(net) * TOP_CONTRIBUTOR_TRIM))
    kept = order[n_trim:]
    top_contributors_removed_net_bps = float(np.mean(net[kept])) if len(kept) else float("nan")

    by_time = sorted(trades, key=lambda t: t.entry_asof_ns)
    half = len(by_time) // 2
    recent_net = np.array([t.net_bps for t in by_time[half:]], dtype=float)
    recent_period_net_bps = float(np.mean(recent_net)) if len(recent_net) else float("nan")

    entry_cost = np.array([t.entry_cost_bps for t in trades], dtype=float)
    exit_cost = np.array([t.exit_cost_bps for t in trades], dtype=float)

    return {
        "n_trades": len(trades),
        "gross_edge_bps": float(np.mean(gross)),
        "net_edge_bps": float(np.mean(net)),
        "net_edge_cost_x2_bps": float(np.mean(net_x2)),
        "delayed_entry_net_bps": float(np.mean(delayed_net)) if len(delayed_net) else float("nan"),
        "profit_factor": _profit_factor(net),
        "max_drawdown": _max_drawdown(net / 10_000.0),
        "capacity_usd": float(np.median(capacity)),
        "top_contributors_removed_net_bps": top_contributors_removed_net_bps,
        "recent_period_net_bps": recent_period_net_bps,
        "paper_live_net_bps": float("nan"),
        "fill_rate": 1.0,
        "realized_slippage_bps": float(np.mean(entry_cost + exit_cost) - np.mean(
            [sum(w * TAKER_FEE_BPS[v] for v, w in t.weights.items()) * 2 for t in trades]
        )),
    }



# Public, standard-tier taker fees (bps of notional). Not account-specific --
# real VIP/maker-rebate tiers can only lower these. Binance matches the
# constant already used by src/institutional/execution/execution_simulator.py.
TAKER_FEE_BPS: dict[str, float] = {
    "binance": 5.0,
    "bybit": 5.5,
    "hyperliquid": 3.5,
}

FEATURE_COL = "okx__queue_imbalance_l5"
EXCLUDED_VENUE = "okx"
EXECUTION_VENUES: tuple[str, ...] = ("binance", "bybit", "hyperliquid")
HORIZON_MS = 30_000
DELAYED_ENTRY_MS = 300  # signal-to-order latency assumption for the sensitivity check


@dataclass
class Trade:
    symbol: str
    entry_idx: int
    exit_idx: int
    direction: int
    entry_asof_ns: int
    weights: dict[str, float]
    gross_bps: float
    entry_cost_bps: float
    exit_cost_bps: float
    capacity_usd: float
    delayed_gross_bps: float
    delayed_cost_bps: float

    @property
    def net_bps(self) -> float:
        return self.gross_bps - self.entry_cost_bps - self.exit_cost_bps

    @property
    def net_cost_x2_bps(self) -> float:
        return self.gross_bps - 2.0 * (self.entry_cost_bps + self.exit_cost_bps)

    @property
    def delayed_net_bps(self) -> float:
        return self.delayed_gross_bps - self.delayed_cost_bps


def _venue_weights(row: pd.Series, venues: Sequence[str]) -> dict[str, float]:
    raw = {}
    for v in venues:
        w = row.get(v + "__price_weight")
        mid = row.get(v + "__price_mid")
        if pd.notna(w) and pd.notna(mid) and w > 0 and mid > 0:
            raw[v] = float(w)
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {v: w / total for v, w in raw.items()}


def _leg_cost_bps(row: pd.Series, weights: dict[str, float]) -> float:
    cost = 0.0
    for v, w in weights.items():
        spread = row.get(v + "__price_spread_bps")
        if pd.isna(spread):
            spread = 0.0
        cost += w * (TAKER_FEE_BPS[v] + float(spread) / 2.0)
    return cost


def _leg_capacity_usd(row: pd.Series, weights: dict[str, float], direction: int) -> float:
    # direction=+1 (long/buy) consumes ask-side depth; -1 (short/sell) consumes bid-side.
    side = "ask_depth_5bps" if direction > 0 else "bid_depth_5bps"
    total = 0.0
    for v, w in weights.items():
        qty = row.get(v + "__" + side)
        price = row.get(v + "__best_bid") if direction > 0 else row.get(v + "__best_ask")
        if price is None or pd.isna(price):
            price = row.get(v + "__price_mid")
        if pd.notna(qty) and pd.notna(price):
            total += w * float(qty) * float(price)
    return total


def build_trades(frame: pd.DataFrame, symbol: str, cadence_ms: int = 100) -> list[Trade]:
    group = frame[frame["symbol"] == symbol].sort_values("asof_ns").reset_index(drop=True)
    steps = HORIZON_MS // cadence_ms
    delayed_steps = max(1, round(DELAYED_ENTRY_MS / cadence_ms))

    fv = _loo_fair_value(group, EXCLUDED_VENUE, ("binance", "bybit", "okx", "hyperliquid"))
    feature = pd.to_numeric(group.get(FEATURE_COL), errors="coerce")
    fresh = group.get(EXCLUDED_VENUE + "__depth_fresh", pd.Series(False, index=group.index)).fillna(False).astype(bool)
    valid_feature = feature.where(fresh)

    lo = float(valid_feature.quantile(0.10))
    hi = float(valid_feature.quantile(0.90))

    trades: list[Trade] = []
    n = len(group)
    i = 0
    while i < n:
        x = valid_feature.iat[i]
        if pd.notna(x) and (x >= hi or x <= lo):
            direction = 1 if x >= hi else -1
            entry_idx = i
            exit_idx = entry_idx + steps
            delayed_entry_idx = entry_idx + delayed_steps
            delayed_exit_idx = delayed_entry_idx + steps
            if exit_idx < n and delayed_exit_idx < n and pd.notna(fv.iat[entry_idx]) and pd.notna(fv.iat[exit_idx]):
                entry_row = group.iloc[entry_idx]
                exit_row = group.iloc[exit_idx]
                weights = _venue_weights(entry_row, EXECUTION_VENUES)
                if weights:
                    gross_bps = direction * 1e4 * float(np.log(fv.iat[exit_idx] / fv.iat[entry_idx]))
                    entry_cost = _leg_cost_bps(entry_row, weights)
                    exit_cost = _leg_cost_bps(exit_row, weights)
                    capacity = _leg_capacity_usd(entry_row, weights, direction)

                    delayed_gross = float("nan")
                    delayed_cost = float("nan")
                    if pd.notna(fv.iat[delayed_entry_idx]) and pd.notna(fv.iat[delayed_exit_idx]):
                        d_entry_row = group.iloc[delayed_entry_idx]
                        d_exit_row = group.iloc[delayed_exit_idx]
                        d_weights = _venue_weights(d_entry_row, EXECUTION_VENUES) or weights
                        delayed_gross = direction * 1e4 * float(
                            np.log(fv.iat[delayed_exit_idx] / fv.iat[delayed_entry_idx])
                        )
                        delayed_cost = _leg_cost_bps(d_entry_row, d_weights) + _leg_cost_bps(d_exit_row, d_weights)

                    trades.append(
                        Trade(
                            symbol=symbol,
                            entry_idx=entry_idx,
                            exit_idx=exit_idx,
                            direction=direction,
                            entry_asof_ns=int(entry_row["asof_ns"]),
                            weights=weights,
                            gross_bps=gross_bps,
                            entry_cost_bps=entry_cost,
                            exit_cost_bps=exit_cost,
                            capacity_usd=capacity,
                            delayed_gross_bps=delayed_gross,
                            delayed_cost_bps=delayed_cost,
                        )
                    )
                # non-overlapping: next scan starts only after this trade's hold ends,
                # whether or not it produced a recorded Trade (missing weights etc.)
                i = exit_idx
                continue
        i += 1
    return trades
