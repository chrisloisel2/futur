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

Pre-unblinding fix (2026-08-10, review round 3):
  1. _trailing_percentile_rank / _rolling_std used to include the CURRENT
     bar in the window used to threshold that same bar -- circular (a bar
     was being judged "extreme" partly against itself). Both now compare
     the current value against a window built ONLY from t-1 and earlier.
  2. FORCED_FLOW_REVERSAL now reads the real residual_return_15m column
     (data_v2.events.residuals) instead of the earlier residual_return_1h/4
     placeholder.
  3. trigger_residual_sign is captured at detection time for
     RELATIVE_VALUE_DISLOCATION and FORCED_FLOW_REVERSAL (CROWDING already
     captured crowded_side) -- direction is NOT a fixed constant per
     family; see data_v2/events/labels.py's per-event direction logic.
  4. research_available_at is carried into the events output -- labels
     must start from a bar's own causal availability, not its raw
     timestamp.
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
    """Percentile rank (0-1) of the current value within the STRICTLY
    PRIOR window (t-window_bars .. t-1) -- current bar never contributes to
    its own threshold. Implemented as one rolling window of window_bars+1
    values ending at t: the last element (raw=True -> w[-1]) is the current
    value, everything before it (w[:-1]) is the history it's ranked
    against."""
    def _rank(w: np.ndarray) -> float:
        hist, current = w[:-1], w[-1]
        return float((hist <= current).mean())

    return series.rolling(window_bars + 1, min_periods=_min_periods(window_bars) + 1).apply(_rank, raw=True)


def _rolling_std(series: pd.Series, window_bars: int) -> pd.Series:
    """Std of the STRICTLY PRIOR window (t-window_bars .. t-1) -- shift(1)
    before rolling so the current bar's own value never enters the std
    it's about to be compared against."""
    return series.shift(1).rolling(window_bars, min_periods=_min_periods(window_bars)).std()


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

    events = df.loc[mask, ["timestamp", "research_available_at"]].copy()
    events["symbol"] = symbol
    events["family"] = "DELEVERAGING"
    # DELEVERAGING is defined as a down-shock fade -- direction is fixed +1
    # (long) by construction of the family itself (protocol section 1),
    # unlike CROWDING/RELATIVE_VALUE/FORCED_FLOW_REVERSAL which can trigger
    # on either side and need a captured sign (see labels.py).
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

    events = df.loc[mask, ["timestamp", "research_available_at"]].copy()
    events["symbol"] = symbol
    events["family"] = "CROWDING"
    events["crowded_side"] = np.where(funding_sign.loc[mask] > 0, "long", "short")
    return EventSet(family="CROWDING", events=events.reset_index(drop=True))


def detect_relative_value_dislocation(
    panel: Dict[str, pd.DataFrame], *, lookback_days: int = 30
) -> EventSet:
    """Needs the full cross-sectional panel (one frame per symbol, aligned
    timestamps) -- relative basis/flow z-scores are computed ACROSS symbols
    at each bar (contemporaneous, not a temporal-lookahead question), while
    residual_extreme (a TIME-SERIES rolling std per symbol) uses the same
    strictly-prior-window discipline as the per-symbol detectors."""
    window = lookback_days * BARS_PER_DAY
    for sym, df in panel.items():
        validate_schema(df)

    symbols = sorted(panel.keys())
    timestamps = panel[symbols[0]]["timestamp"]
    research_available_at = panel[symbols[0]]["research_available_at"]

    residual = pd.DataFrame({s: panel[s].set_index("timestamp")["residual_return_1h"] for s in symbols})
    basis_z = pd.DataFrame({s: panel[s].set_index("timestamp")["basis_z_1d"] for s in symbols})
    flow = pd.DataFrame({s: panel[s].set_index("timestamp")["signed_volume"] for s in symbols})

    residual_std = residual.shift(1).rolling(window, min_periods=_min_periods(window)).std()
    residual_extreme = residual.abs() >= 2.0 * residual_std

    # cross-sectional (same bar, across symbols) -- contemporaneous, not a
    # temporal lookahead: every symbol's information at bar t is used to
    # rank every OTHER symbol at that same bar t, never at t+1.
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
            fired = sym_mask.to_numpy()
            ev = pd.DataFrame({
                "timestamp": timestamps[fired].to_numpy(),
                "research_available_at": research_available_at[fired].to_numpy(),
            })
            ev["symbol"] = sym
            ev["family"] = "RELATIVE_VALUE_DISLOCATION"
            ev["trigger_residual_sign"] = np.sign(residual.loc[sym_mask, sym]).to_numpy()
            rows.append(ev)
    events = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["timestamp", "research_available_at", "symbol", "family", "trigger_residual_sign"]
    )
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

    # real residual_return_15m (data_v2.events.residuals), not the earlier
    # residual_return_1h/4 placeholder.
    price_std_15m = _rolling_std(df["residual_return_15m"], window)
    price_shock = df["residual_return_15m"].abs() >= 2.5 * price_std_15m

    mask = (flow_extreme & oi_collapse & price_shock).fillna(False)
    mask = _apply_cooldown(mask, COOLDOWN_BARS_DEFAULT)

    events = df.loc[mask, ["timestamp", "research_available_at"]].copy()
    events["symbol"] = symbol
    events["family"] = "FORCED_FLOW_REVERSAL"
    events["trigger_residual_sign"] = np.sign(df.loc[mask, "residual_return_15m"]).to_numpy()
    return EventSet(family="FORCED_FLOW_REVERSAL", events=events.reset_index(drop=True))
