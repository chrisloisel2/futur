"""A2-RV-v1: trade the A2 dislocation-convergence mechanism, per
docs/A2RV_PREREGISTRATION.md. Reuses Phase 5.2's fixed cost/threshold-freezing
discipline (market_physics_v3/phase5_2_execution_economics.py); a new module
rather than extending that one because the trade structure is genuinely
different -- one long leg (the dislocated venue) against a short *basket* of
every other venue, weighted like leave_one_venue_out_fair_value's own
denominator, instead of Phase 5.2's single-direction multi-venue split.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from alpha_foundry_v5.validation import max_drawdown as _max_drawdown
from alpha_foundry_v5.validation import profit_factor as _profit_factor

from .phase5_2_execution_economics import TAKER_FEE_BPS
from .phase5_audit import load_parquet_dataset

HORIZON_MS = 2_000  # A2's own strongest sealed horizon, locked -- not re-scanned
LATENCY_GRID_MS: tuple[int, ...] = (0, 50, 100, 250, 500)
REFERENCE_NOTIONAL_USD = 200_000.0


@dataclass(frozen=True)
class FrozenThresholds:
    lo: float
    hi: float


def freeze_thresholds(dev_pilot_tape_path: str, symbol: str, venue: str) -> FrozenThresholds:
    """10th/90th percentile of venue__price_dislocation_bps on the DEV_PILOT window
    ONLY, per (symbol, venue) -- same discipline as Phase 5.2's freeze_thresholds,
    never recomputed on the tape being scored."""
    frame = load_parquet_dataset(dev_pilot_tape_path)
    group = frame[frame["symbol"] == symbol]
    feature = pd.to_numeric(group.get(f"{venue}__price_dislocation_bps"), errors="coerce")
    return FrozenThresholds(lo=float(feature.quantile(0.10)), hi=float(feature.quantile(0.90)))


def _leg_bps(entry_row: pd.Series, exit_row: pd.Series, venue: str, weight: float) -> float:
    # weight > 0: long (buy the ask at entry, sell the bid at exit).
    # weight < 0: short (sell the bid at entry, buy the ask back at exit).
    entry_col = "price_best_ask" if weight > 0 else "price_best_bid"
    exit_col = "price_best_bid" if weight > 0 else "price_best_ask"
    entry_price = entry_row.get(f"{venue}__{entry_col}")
    exit_price = exit_row.get(f"{venue}__{exit_col}")
    if pd.isna(entry_price) or pd.isna(exit_price) or float(entry_price) <= 0:
        return float("nan")
    return float(weight * 1e4 * np.log(float(exit_price) / float(entry_price)))


def basket_weights(entry_row: pd.Series, venue: str, venues: Sequence[str]) -> dict[str, float]:
    """Weight every OTHER venue exactly like leave_one_venue_out_fair_value's own
    denominator (price_weight, normalized among the non-`venue` members) -- the short
    leg literally IS the anchor A2's target is measured against, not an approximation."""
    raw = {}
    for v in venues:
        v = str(v).lower()
        if v == venue:
            continue
        w = entry_row.get(f"{v}__price_weight")
        mid = entry_row.get(f"{v}__price_mid")
        if pd.notna(w) and pd.notna(mid) and float(w) > 0 and float(mid) > 0:
            raw[v] = float(w)
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {v: w / total for v, w in raw.items()}


@dataclass
class Trade:
    symbol: str
    trigger_venue: str
    entry_idx: int
    exit_idx: int
    trigger_direction: int  # +1 = trigger venue was cheap (long it); -1 = rich (short it)
    entry_asof_ns: int
    weights: dict[str, float]  # trigger_venue: +-0.5, basket members: opposite sign, sum(|w|)==1.0
    gross_bps: float
    one_way_fee_bps: float
    capacity_usd: float
    delayed_gross_bps: dict[int, float]  # latency_ms -> gross_bps
    delayed_one_way_fee_bps: dict[int, float]

    @property
    def net_bps(self) -> float:
        return self.gross_bps - 2.0 * self.one_way_fee_bps

    def delayed_net_bps(self, latency_ms: int) -> float:
        gross = self.delayed_gross_bps.get(latency_ms, float("nan"))
        fee = self.delayed_one_way_fee_bps.get(latency_ms, float("nan"))
        return gross - 2.0 * fee


def _all_leg_weights(trigger_venue: str, trigger_direction: int, basket: dict[str, float]) -> dict[str, float]:
    """50% capital long the trigger venue, 50% short the basket (sum(|w|) == 1.0
    total) -- same "gross=100%, half each side" convention docs/
    A13H_PREREGISTRATION.md's dollar-neutral portfolio already used, not a new one
    invented here."""
    weights = {trigger_venue: 0.5 * float(trigger_direction)}
    for v, w in basket.items():
        weights[v] = -0.5 * float(trigger_direction) * float(w)
    return weights


def _gross_bps(entry_row: pd.Series, exit_row: pd.Series, weights: dict[str, float]) -> float:
    total = 0.0
    for v, w in weights.items():
        leg = _leg_bps(entry_row, exit_row, v, w)
        if not np.isfinite(leg):
            return float("nan")
        total += leg
    return total


def _one_way_fee_bps(weights: dict[str, float]) -> float:
    # weighted average across all legs -- sum(|w|) == 1.0 by _all_leg_weights'
    # construction, so this needs no extra normalization (same shape as Phase
    # 5.2's _one_way_fee_bps, just over a mixed-sign weight set).
    return float(sum(abs(w) * TAKER_FEE_BPS.get(v, float("nan")) for v, w in weights.items()))


def _leg_capacity_usd(row: pd.Series, weights: dict[str, float]) -> float:
    implied_caps = []
    for v, w in weights.items():
        side = "ask_depth_5bps" if w > 0 else "bid_depth_5bps"
        price_side = "price_best_ask" if w > 0 else "price_best_bid"
        qty = row.get(f"{v}__{side}")
        price = row.get(f"{v}__{price_side}")
        if pd.notna(qty) and pd.notna(price) and abs(w) > 0:
            implied_caps.append(float(qty) * float(price) / abs(w))
    return min(implied_caps) if implied_caps else float("nan")


def build_trades(
    frame: pd.DataFrame,
    symbol: str,
    thresholds_by_venue: dict[str, FrozenThresholds],
    venues: Sequence[str],
    cadence_ms: int = 100,
) -> list[Trade]:
    group = frame[frame["symbol"] == symbol].sort_values("asof_ns").reset_index(drop=True)
    steps = HORIZON_MS // cadence_ms
    max_delay_steps = max(1, round(max(LATENCY_GRID_MS) / cadence_ms))

    disloc = {v: pd.to_numeric(group.get(f"{v}__price_dislocation_bps"), errors="coerce") for v in venues}

    trades: list[Trade] = []
    n = len(group)
    i = 0
    while i < n:
        triggered = None
        for v in venues:
            th = thresholds_by_venue.get(v)
            if th is None:
                continue
            x = disloc[v].iat[i]
            if pd.notna(x) and (x >= th.hi or x <= th.lo):
                triggered = (v, 1 if x <= th.lo else -1)  # dislocation<=lo -> venue cheap -> long it
                break
        if triggered is None:
            i += 1
            continue
        trigger_venue, trigger_direction = triggered
        entry_idx = i
        exit_idx = entry_idx + steps
        if exit_idx >= n or entry_idx + max_delay_steps + steps >= n:
            i += 1
            continue

        entry_row = group.iloc[entry_idx]
        exit_row = group.iloc[exit_idx]
        basket = basket_weights(entry_row, trigger_venue, venues)
        if not basket:
            i = exit_idx
            continue
        weights = _all_leg_weights(trigger_venue, trigger_direction, basket)
        gross_bps = _gross_bps(entry_row, exit_row, weights)
        if not np.isfinite(gross_bps):
            i = exit_idx
            continue
        one_way_fee_bps = _one_way_fee_bps(weights)
        capacity = _leg_capacity_usd(entry_row, weights)

        delayed_gross: dict[int, float] = {}
        delayed_fee: dict[int, float] = {}
        for latency_ms in LATENCY_GRID_MS:
            delay_steps = max(0, round(latency_ms / cadence_ms))
            d_entry_idx = entry_idx + delay_steps
            d_exit_idx = d_entry_idx + steps
            if d_exit_idx >= n:
                continue
            d_entry_row = group.iloc[d_entry_idx]
            d_exit_row = group.iloc[d_exit_idx]
            d_basket = basket_weights(d_entry_row, trigger_venue, venues) or basket
            d_weights = _all_leg_weights(trigger_venue, trigger_direction, d_basket)
            d_gross = _gross_bps(d_entry_row, d_exit_row, d_weights)
            if np.isfinite(d_gross):
                delayed_gross[latency_ms] = d_gross
                delayed_fee[latency_ms] = _one_way_fee_bps(d_weights)

        trades.append(
            Trade(
                symbol=symbol,
                trigger_venue=trigger_venue,
                entry_idx=entry_idx,
                exit_idx=exit_idx,
                trigger_direction=trigger_direction,
                entry_asof_ns=int(entry_row["asof_ns"]),
                weights=weights,
                gross_bps=gross_bps,
                one_way_fee_bps=one_way_fee_bps,
                capacity_usd=capacity,
                delayed_gross_bps=delayed_gross,
                delayed_one_way_fee_bps=delayed_fee,
            )
        )
        i = exit_idx
    return trades


def summarize_trades(trades: Sequence[Trade]) -> dict[str, object]:
    if not trades:
        raise ValueError("no trades produced -- cannot summarize")

    gross = np.array([t.gross_bps for t in trades], dtype=float)
    net = np.array([t.net_bps for t in trades], dtype=float)
    capacity = np.array([t.capacity_usd for t in trades], dtype=float)
    capacity = capacity[np.isfinite(capacity)]
    capacity_usd = float(np.median(capacity)) if len(capacity) else float("nan")
    fill_rate = float(min(1.0, capacity_usd / REFERENCE_NOTIONAL_USD)) if np.isfinite(capacity_usd) and capacity_usd > 0 else 0.0

    latency_net = {}
    for latency_ms in LATENCY_GRID_MS:
        values = np.array([t.delayed_net_bps(latency_ms) for t in trades], dtype=float)
        values = values[np.isfinite(values)]
        latency_net[str(latency_ms)] = float(np.mean(values)) if len(values) else float("nan")

    return {
        "n_trades": len(trades),
        "gross_edge_bps": float(np.mean(gross)),
        "net_edge_bps": float(np.mean(net)),
        "profit_factor": _profit_factor(net),
        "max_drawdown": _max_drawdown(net / 10_000.0),
        "capacity_usd": capacity_usd,
        "fill_rate": fill_rate,
        "latency_sensitivity_net_bps": latency_net,
        "trigger_venue_counts": {v: sum(1 for t in trades if t.trigger_venue == v) for v in {t.trigger_venue for t in trades}},
    }
