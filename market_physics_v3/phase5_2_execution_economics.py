from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from alpha_foundry_v5.validation import max_drawdown as _max_drawdown
from alpha_foundry_v5.validation import profit_factor as _profit_factor

from .phase5_audit import load_parquet_dataset

TOP_CONTRIBUTOR_TRIM = 0.05  # remove the best 5% of trades by net PnL

# Public, standard-tier taker fees (bps of notional). Not account-specific --
# real VIP/maker-rebate tiers can only lower these. Binance matches the
# constant already used by src/institutional/execution/execution_simulator.py.
# okx added for a2rv_execution.py, which (unlike this module, where okx is
# always EXCLUDED_VENUE and never itself traded) treats all four venues
# symmetrically -- okx's public regular-tier USDT-margined perp taker fee is
# also 5.0bps, same tier as Binance's.
TAKER_FEE_BPS: dict[str, float] = {
    "binance": 5.0,
    "bybit": 5.5,
    "hyperliquid": 3.5,
    "okx": 5.0,
}

FEATURE_COL = "okx__queue_imbalance_l5"
EXCLUDED_VENUE = "okx"
EXECUTION_VENUES: tuple[str, ...] = ("binance", "bybit", "hyperliquid")
HORIZON_MS = 30_000
DELAYED_ENTRY_MS = 300  # signal-to-order latency assumption for the sensitivity check
# Reference clip size for capacity/fill-rate reporting -- matches GatePolicy's own
# execution_min_capacity_usd, so "does this pass the capacity gate" and "what's the fill
# rate at gate-relevant size" use the same number.
REFERENCE_NOTIONAL_USD = 200_000.0


@dataclass(frozen=True)
class FrozenThresholds:
    lo: float
    hi: float


def freeze_thresholds(dev_pilot_tape_path: str, symbol: str) -> FrozenThresholds:
    """10th/90th percentile of FEATURE_COL on the DEV_PILOT window ONLY, per symbol.

    Must never be recomputed on the confirmation tape being scored -- doing so lets each
    entry threshold see the full confirmation window's own distribution (including its
    future, relative to earlier entries), which is exactly the in-sample circularity P0-6
    flagged. The DEV_PILOT window ends before the confirmation window starts, so this is
    causally clean.
    """
    frame = load_parquet_dataset(dev_pilot_tape_path)
    group = frame[frame["symbol"] == symbol]
    fresh = group.get(EXCLUDED_VENUE + "__depth_fresh", pd.Series(False, index=group.index)).fillna(False).astype(bool)
    feature = pd.to_numeric(group.get(FEATURE_COL), errors="coerce").where(fresh)
    return FrozenThresholds(lo=float(feature.quantile(0.10)), hi=float(feature.quantile(0.90)))


@dataclass
class Trade:
    symbol: str
    entry_idx: int
    exit_idx: int
    direction: int
    entry_asof_ns: int
    weights: dict[str, float]
    gross_bps: float
    gross_mid_bps: float  # frictionless (mid-to-mid) gross, for the spread-cost diagnostic
    one_way_fee_bps: float  # weighted-average taker fee for ONE fill -- a real round trip pays this twice
    capacity_usd: float
    delayed_gross_bps: float
    delayed_one_way_fee_bps: float

    @property
    def net_bps(self) -> float:
        # gross_bps already reflects two real transactions (buy the ask at entry,
        # sell the bid at exit -- see _gross_bps), so the fee side must match: a
        # taker fee is charged on every fill, not once per position. This used to
        # subtract one_way_fee_bps a single time (an earlier bug -- roughly halved
        # the true round-trip cost; the -4.44bps headline this mechanism was
        # reported at was actually closer to -9.3bps).
        return self.gross_bps - 2.0 * self.one_way_fee_bps

    @property
    def delayed_net_bps(self) -> float:
        return self.delayed_gross_bps - 2.0 * self.delayed_one_way_fee_bps

    @property
    def spread_cost_bps(self) -> float:
        return self.gross_mid_bps - self.gross_bps


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


def _weighted_log_return(entry_row: pd.Series, exit_row: pd.Series, weights: dict[str, float], direction: int, entry_col: str, exit_col: str) -> float:
    """Real per-venue prices with FIXED entry weights throughout -- not the LOO fair
    value's own per-row weighting (which can differ between entry and exit rows)."""
    total = 0.0
    for v, w in weights.items():
        p_entry = entry_row.get(f"{v}__{entry_col}")
        p_exit = exit_row.get(f"{v}__{exit_col}")
        if pd.isna(p_entry) or pd.isna(p_exit) or float(p_entry) <= 0:
            return float("nan")
        total += w * float(np.log(float(p_exit) / float(p_entry)))
    return float(direction * 1e4 * total)


def _gross_bps(entry_row: pd.Series, exit_row: pd.Series, weights: dict[str, float], direction: int) -> float:
    # LONG buys at entry (pay ask) and sells at exit (receive bid); SHORT is the reverse.
    entry_col = "price_best_ask" if direction > 0 else "price_best_bid"
    exit_col = "price_best_bid" if direction > 0 else "price_best_ask"
    return _weighted_log_return(entry_row, exit_row, weights, direction, entry_col, exit_col)


def _gross_mid_bps(entry_row: pd.Series, exit_row: pd.Series, weights: dict[str, float], direction: int) -> float:
    return _weighted_log_return(entry_row, exit_row, weights, direction, "price_mid", "price_mid")


def _one_way_fee_bps(weights: dict[str, float]) -> float:
    """Weighted-average taker fee for a SINGLE fill. A round trip (this
    mechanism's entry + exit) pays this twice -- callers must not treat it as
    the full trade's fee cost on its own."""
    return float(sum(w * TAKER_FEE_BPS[v] for v, w in weights.items()))


def _leg_capacity_usd(row: pd.Series, weights: dict[str, float], direction: int) -> float:
    """Bottleneck-leg capacity: each leg v, sized at its weight w_v, can absorb
    depth_v/w_v of TOTAL position notional before that leg alone becomes the binding
    constraint. Overall capacity is the MIN across legs, not the sum -- a thin leg caps
    the whole position even if the other legs are deep.
    """
    side = "ask_depth_5bps" if direction > 0 else "bid_depth_5bps"
    price_side = "price_best_ask" if direction > 0 else "price_best_bid"
    implied_caps = []
    for v, w in weights.items():
        qty = row.get(f"{v}__{side}")
        price = row.get(f"{v}__{price_side}")
        if pd.notna(qty) and pd.notna(price) and w > 0:
            implied_caps.append(float(qty) * float(price) / w)
    return min(implied_caps) if implied_caps else float("nan")


def build_trades(frame: pd.DataFrame, symbol: str, thresholds: FrozenThresholds, cadence_ms: int = 100) -> list[Trade]:
    group = frame[frame["symbol"] == symbol].sort_values("asof_ns").reset_index(drop=True)
    steps = HORIZON_MS // cadence_ms
    delayed_steps = max(1, round(DELAYED_ENTRY_MS / cadence_ms))

    feature = pd.to_numeric(group.get(FEATURE_COL), errors="coerce")
    fresh = group.get(EXCLUDED_VENUE + "__depth_fresh", pd.Series(False, index=group.index)).fillna(False).astype(bool)
    valid_feature = feature.where(fresh)

    lo, hi = thresholds.lo, thresholds.hi

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
            if exit_idx < n and delayed_exit_idx < n:
                entry_row = group.iloc[entry_idx]
                exit_row = group.iloc[exit_idx]
                weights = _venue_weights(entry_row, EXECUTION_VENUES)
                if weights:
                    gross_bps = _gross_bps(entry_row, exit_row, weights, direction)
                    gross_mid_bps = _gross_mid_bps(entry_row, exit_row, weights, direction)
                    if np.isfinite(gross_bps) and np.isfinite(gross_mid_bps):
                        one_way_fee_bps = _one_way_fee_bps(weights)
                        capacity = _leg_capacity_usd(entry_row, weights, direction)

                        delayed_gross = float("nan")
                        delayed_one_way_fee = float("nan")
                        d_entry_row = group.iloc[delayed_entry_idx]
                        d_exit_row = group.iloc[delayed_exit_idx]
                        d_weights = _venue_weights(d_entry_row, EXECUTION_VENUES) or weights
                        d_gross = _gross_bps(d_entry_row, d_exit_row, d_weights, direction)
                        if np.isfinite(d_gross):
                            delayed_gross = d_gross
                            delayed_one_way_fee = _one_way_fee_bps(d_weights)

                        trades.append(
                            Trade(
                                symbol=symbol,
                                entry_idx=entry_idx,
                                exit_idx=exit_idx,
                                direction=direction,
                                entry_asof_ns=int(entry_row["asof_ns"]),
                                weights=weights,
                                gross_bps=gross_bps,
                                gross_mid_bps=gross_mid_bps,
                                one_way_fee_bps=one_way_fee_bps,
                                capacity_usd=capacity,
                                delayed_gross_bps=delayed_gross,
                                delayed_one_way_fee_bps=delayed_one_way_fee,
                            )
                        )
                # non-overlapping: next scan starts only after this trade's hold ends,
                # whether or not it produced a recorded Trade (missing weights etc.)
                i = exit_idx
                continue
        i += 1
    return trades


def summarize_trades(trades: Sequence[Trade]) -> dict[str, object]:
    if not trades:
        raise ValueError("no trades produced -- cannot summarize")

    gross = np.array([t.gross_bps for t in trades], dtype=float)
    net = np.array([t.net_bps for t in trades], dtype=float)
    capacity = np.array([t.capacity_usd for t in trades], dtype=float)
    capacity = capacity[np.isfinite(capacity)]
    delayed_net = np.array([t.delayed_net_bps for t in trades], dtype=float)
    delayed_net = delayed_net[np.isfinite(delayed_net)]
    spread_cost = np.array([t.spread_cost_bps for t in trades], dtype=float)

    order = np.argsort(net)[::-1]
    n_trim = max(1, round(len(net) * TOP_CONTRIBUTOR_TRIM))
    kept = order[n_trim:]
    top_contributors_removed_net_bps = float(np.mean(net[kept])) if len(kept) else float("nan")

    by_time = sorted(trades, key=lambda t: t.entry_asof_ns)
    half = len(by_time) // 2
    recent_net = np.array([t.net_bps for t in by_time[half:]], dtype=float)
    recent_period_net_bps = float(np.mean(recent_net)) if len(recent_net) else float("nan")

    capacity_usd = float(np.median(capacity)) if len(capacity) else float("nan")
    fill_rate = float(min(1.0, capacity_usd / REFERENCE_NOTIONAL_USD)) if np.isfinite(capacity_usd) and capacity_usd > 0 else 0.0

    return {
        "n_trades": len(trades),
        "gross_edge_bps": float(np.mean(gross)),
        "net_edge_bps": float(np.mean(net)),
        "delayed_entry_net_bps": float(np.mean(delayed_net)) if len(delayed_net) else float("nan"),
        "profit_factor": _profit_factor(net),
        "max_drawdown": _max_drawdown(net / 10_000.0),
        "capacity_usd": capacity_usd,
        "top_contributors_removed_net_bps": top_contributors_removed_net_bps,
        "recent_period_net_bps": recent_period_net_bps,
        "paper_live_net_bps": float("nan"),
        "fill_rate": fill_rate,
        "realized_slippage_bps": float(np.mean(spread_cost)),
    }
