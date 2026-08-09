"""
data_v2/events/detectors.py
─────────────────────────────────────────────────────────────────────────────
The four Event Scanner V1 families, exactly as pre-registered in
reports/EVENT_SCANNER_V1_PROTOCOL.md. Detection only -- no labels, no
statistics, no ML. Every threshold here must match the protocol document
verbatim; if you're tempted to tune one, that's a new protocol (V2), not an
edit here (see the protocol's own closing rule).

Each detector takes a single-symbol causal feature frame (data_v2.events.
schema.REQUIRED_COLUMNS) except relative_value_dislocation, which needs a
cross-sectional panel (one frame per symbol, same timestamps) to compute
relative basis/flow/residual z-scores at each bar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np
import pandas as pd

from data_v2.events.schema import validate_schema

BARS_PER_DAY = 288  # 5m bars
COOLDOWN_BARS_DELEVERAGING = 48  # 4h
COOLDOWN_BARS_DEFAULT = 12  # 1h, applied to the other three families


def _min_periods(window_bars: int) -> int:
    # min_periods can never exceed the window itself (pandas raises
    # otherwise) -- 20 observations is the floor in production (years of
    # real 5m data), capped down for short/synthetic windows in tests.
    return min(window_bars, 20)


def _trailing_percentile_rank(series: pd.Series, window_bars: int) -> pd.Series:
    """Percentile rank (0-1) of the current value within the trailing
    window, INCLUDING the current bar but never a future one."""
    return series.rolling(window_bars, min_periods=_min_periods(window_bars)).apply(
        lambda w: (w <= w.iloc[-1]).mean(), raw=False
    )


def _rolling_std(series: pd.Series, window_bars: int) -> pd.Series:
    return series.rolling(window_bars, min_periods=_min_periods(window_bars)).std()


def _apply_cooldown(mask: pd.Series, cooldown_bars: int) -> pd.Series:
    """Keep only the FIRST True in any run of Trues within cooldown_bars of
    each other -- one event per unwind, not one per bar of the same move."""
    out = pd.Series(False, index=mask.index)
    last_fire = -cooldown_bars - 1
    positions = np.flatnonzero(mask.to_numpy())
    for pos in positions:
        if pos - last_fire >= cooldown_bars:
            out.iloc[pos] = True
            last_fire = pos
    return out


@dataclass
class EventSet:
    family: str
    events: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["timestamp", "symbol"]))


def detect_deleveraging(df: pd.DataFrame, *, symbol: str, lookback_days: int = 30) -> EventSet:
    validate_schema(df)
    window = lookback_days * BARS_PER_DAY

    price_residual_std = _rolling_std(df["residual_return_1h"], window)
    price_shock = df["residual_return_1h"] <= -1.5 * price_residual_std

    oi_collapse = df["oi_delta_pct_1h"] <= -0.03

    sell_rank = _trailing_percentile_rank(df["aggressive_sell_usd"], window)
    sell_extreme = sell_rank >= 0.90

    vol_rank = _trailing_percentile_rank(df["volume"], window)
    vol_high = vol_rank >= 0.80

    mask = (price_shock & oi_collapse & sell_extreme & vol_high).fillna(False)
    mask = _apply_cooldown(mask, COOLDOWN_BARS_DELEVERAGING)

    events = df.loc[mask, ["timestamp"]].copy()
    events["symbol"] = symbol
    events["family"] = "DELEVERAGING"
    if "liq_long_usd_5m" in df.columns:
        events["liq_confirmed"] = (df.loc[mask, "liq_long_usd_5m"] > 0).to_numpy()
    return EventSet(family="DELEVERAGING", events=events.reset_index(drop=True))


def detect_crowding(df: pd.DataFrame, *, symbol: str, lookback_days: int = 90) -> EventSet:
    validate_schema(df)
    window = lookback_days * BARS_PER_DAY

    funding_rank = _trailing_percentile_rank(df["funding_rate"].abs(), window)
    funding_extreme = funding_rank >= 0.90

    basis_extreme = df["basis_z_1d"].abs() >= 2.0
    oi_building = df["oi_delta_pct_1h"] >= 0.03

    flow_sign = np.sign(df["aggressive_buy_usd"] - df["aggressive_sell_usd"])
    funding_sign = np.sign(df["funding_rate"])
    same_direction = flow_sign == funding_sign

    mask = (funding_extreme & basis_extreme & oi_building & same_direction).fillna(False)
    mask = _apply_cooldown(mask, COOLDOWN_BARS_DEFAULT)

    events = df.loc[mask, ["timestamp"]].copy()
    events["symbol"] = symbol
    events["family"] = "CROWDING"
    events["crowded_side"] = np.where(funding_sign.loc[mask] > 0, "long", "short")
    return EventSet(family="CROWDING", events=events.reset_index(drop=True))


def detect_relative_value_dislocation(
    panel: Dict[str, pd.DataFrame], *, lookback_days: int = 30
) -> EventSet:
    """Needs the full cross-sectional panel (one frame per symbol, aligned
    timestamps) -- relative basis/flow z-scores are computed ACROSS symbols
    at each bar, not within one symbol's own history."""
    window = lookback_days * BARS_PER_DAY
    for sym, df in panel.items():
        validate_schema(df)

    symbols = sorted(panel.keys())
    timestamps = panel[symbols[0]]["timestamp"]

    residual = pd.DataFrame({s: panel[s].set_index("timestamp")["residual_return_1h"] for s in symbols})
    basis_z = pd.DataFrame({s: panel[s].set_index("timestamp")["basis_z_1d"] for s in symbols})
    flow = pd.DataFrame({s: panel[s].set_index("timestamp")["signed_volume"] for s in symbols})

    residual_std = residual.rolling(window, min_periods=max(20, window // 10)).std()
    residual_extreme = residual.abs() >= 2.0 * residual_std

    relative_basis_z = basis_z.sub(basis_z.median(axis=1), axis=0)
    relative_basis_extreme = relative_basis_z.abs() >= 2.0

    flow_cross_mean = flow.mean(axis=1)
    flow_cross_std = flow.std(axis=1).replace(0, np.nan)
    relative_flow_z = flow.sub(flow_cross_mean, axis=0).div(flow_cross_std, axis=0)
    relative_flow_extreme = relative_flow_z.abs() >= 2.0

    same_sign = (
        (np.sign(relative_basis_z) == np.sign(relative_flow_z))
        & (np.sign(relative_flow_z) == np.sign(residual))
    )

    mask = (residual_extreme & relative_basis_extreme & relative_flow_extreme & same_sign).fillna(False)

    rows = []
    for sym in symbols:
        sym_mask = _apply_cooldown(mask[sym], COOLDOWN_BARS_DEFAULT)
        if sym_mask.any():
            ev = pd.DataFrame({"timestamp": timestamps[sym_mask.to_numpy()].to_numpy()})
            ev["symbol"] = sym
            ev["family"] = "RELATIVE_VALUE_DISLOCATION"
            rows.append(ev)
    events = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["timestamp", "symbol", "family"])
    return EventSet(family="RELATIVE_VALUE_DISLOCATION", events=events)


def detect_forced_flow_reversal(df: pd.DataFrame, *, symbol: str, lookback_days: int = 30) -> EventSet:
    validate_schema(df)
    window = lookback_days * BARS_PER_DAY

    if "liq_long_usd_5m" in df.columns and "liq_short_usd_5m" in df.columns:
        liq_total = df["liq_long_usd_5m"].fillna(0) + df["liq_short_usd_5m"].fillna(0)
        liq_rank = _trailing_percentile_rank(liq_total, window)
        flow_extreme = liq_rank >= 0.95
    else:
        flow_rank = _trailing_percentile_rank(df["signed_volume"].abs(), window)
        flow_extreme = flow_rank >= 0.95

    oi_collapse = df["oi_delta_pct_1h"] <= -0.05

    price_residual_15m = df["residual_return_1h"] / 4.0  # protocol placeholder: real 15m residual
    # column not yet in REQUIRED_COLUMNS -- computed from the 1h residual as
    # a rough proxy ONLY until a genuine residual_return_15m column exists;
    # flagged here rather than silently treated as equivalent.
    price_std_15m = _rolling_std(price_residual_15m, window)
    price_shock = price_residual_15m.abs() >= 2.5 * price_std_15m

    mask = (flow_extreme & oi_collapse & price_shock).fillna(False)
    mask = _apply_cooldown(mask, COOLDOWN_BARS_DEFAULT)

    events = df.loc[mask, ["timestamp"]].copy()
    events["symbol"] = symbol
    events["family"] = "FORCED_FLOW_REVERSAL"
    return EventSet(family="FORCED_FLOW_REVERSAL", events=events.reset_index(drop=True))
