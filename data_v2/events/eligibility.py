"""
data_v2/events/eligibility.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, sections 6-11: SOURCE-QUALIFIED event eligibility, per
family, per (symbol, timestamp) row. Pre-registered BEFORE any economic
result -- these functions must never read a return, PnL, PF, Sharpe, label,
or MFE/MAE column, and never will (none of that exists in the panel this
module reads from -- see data_v2.events.schema.REQUIRED_COLUMNS).

Eligibility depends ONLY on:
  - existence of the required source columns (non-null at that row)
  - causal warmup (enough STRICTLY PRIOR history for a rolling stat used
    by that family's detector to be a real, full-window value -- not a
    partial/short-window stand-in)
  - cross-sectional population size (RVD only)

A row failing eligibility is NOT a negative signal, NOT "no event", and
NOT a zero -- it is NOT OBSERVABLE. Every mask here is a plain boolean
column; the panel's underlying feature columns are never mutated,
fillna'd, or clipped to make a row "pass".

Contracts (mission sections 8-11):
  DELEVERAGING : perp OHLCV, residual_return_1h, rolling residual std 30d,
                 OI, oi_delta_pct_1h, aggressive_sell_usd, volume,
                 research_available_at, full warmup. Liquidation stays
                 OPTIONAL (per protocol) -- never required for eligibility.
  CROWDING     : funding_rate, funding_rate_percentile_90d, full funding
                 90d settlement warmup, basis_z_1d, OI, oi_delta_pct_1h,
                 aggressive_buy_usd, aggressive_sell_usd, causal
                 availability.
  RVD          : residual_return_1h, strict-prior residual std 30d,
                 basis_z, signed_volume, research_available_at, PLUS a
                 cross-sectional minimum population (MIN_CROSS_SECTION_
                 SIZE, pre-registered below, structural -- not derived
                 from any observed PnL).
  FFR          : residual_return_15m, OI, oi_delta_pct_1h, flow, PLUS at
                 least one of {liq_feed_available, signed_volume present}
                 -- the liquidation-vs-flow-fallback split is a DATA
                 AVAILABILITY question here, not the P95 DETECTION
                 threshold (that lives in detectors.py, reads PnL-blind
                 too, but is a different question: "is this bar an event"
                 vs "can this bar be observed at all").
"""
from __future__ import annotations

import pandas as pd

BARS_PER_DAY = 288
RESIDUAL_STD_WINDOW_DAYS = 30
RESIDUAL_STD_WINDOW_BARS = RESIDUAL_STD_WINDOW_DAYS * BARS_PER_DAY
FUNDING_WARMUP_DAYS = 90

# Pre-registered BEFORE any scan, structural (not PnL-derived): the
# classic large-sample rule of thumb (Central Limit Theorem convention)
# for a cross-sectional mean/std/median to be a stable statistic rather
# than dominated by a handful of symbols. The protocol's own detector code
# (data_v2/events/detectors.py::detect_relative_value_dislocation) had no
# existing minimum to inherit (checked first, per mission section 10).
MIN_CROSS_SECTION_SIZE = 30


def residual_std_30d(residual_return_1h: pd.Series) -> pd.Series:
    """Strict-prior (shift(1)), full-window (min_periods=full window)
    rolling std of residual_return_1h over the trailing 30 days -- shared
    by DELEVERAGING and RVD, same discipline as detectors.py's
    _rolling_std (never judge/include a bar using its own value)."""
    return residual_return_1h.shift(1).rolling(RESIDUAL_STD_WINDOW_BARS, min_periods=RESIDUAL_STD_WINDOW_BARS).std()


def funding_settlement_warmup(funding_is_settlement: pd.Series, timestamp: pd.Series) -> pd.Series:
    """True once >= FUNDING_WARMUP_DAYS have elapsed since this symbol's
    OWN first real settlement -- funding_rate_percentile_90d itself
    becomes non-NaN as soon as there is >=1 PRIOR settlement (build_event_
    feature_panel.py's _settlement_percentile_rank ranks against whatever
    prior settlements exist, however few), which is NOT the same claim as
    "a genuine 90-day settlement history actually backs this percentile" --
    exactly the distinction mission section 9 requires ("full funding 90d
    settlement warmup"). Forward-filled the same causal way funding_rate
    itself is (a fact that becomes true at settlement N stays true for
    every bar after it, never reverts)."""
    settlement_times = timestamp.where(funding_is_settlement)
    # first non-null value, causal (no lookahead needed: a fixed constant
    # once the symbol's very first settlement has occurred)
    first_settlement_ts = settlement_times.dropna().iloc[0] if settlement_times.notna().any() else pd.NaT
    if pd.isna(first_settlement_ts):
        return pd.Series(False, index=timestamp.index)
    elapsed = timestamp - first_settlement_ts
    return (elapsed >= pd.Timedelta(days=FUNDING_WARMUP_DAYS)).fillna(False)


def eligible_deleveraging(panel: pd.DataFrame, residual_std_30d_col: pd.Series) -> pd.Series:
    required = [
        "open", "close", "volume", "oi", "oi_delta_pct_1h",
        "aggressive_sell_usd", "residual_return_1h", "research_available_at",
    ]
    base = pd.Series(True, index=panel.index)
    for col in required:
        base &= panel[col].notna()
    return (base & residual_std_30d_col.notna()).fillna(False)


def eligible_crowding(panel: pd.DataFrame, funding_warmup_col: pd.Series) -> pd.Series:
    required = [
        "funding_rate", "funding_rate_percentile_90d", "basis_z_1d", "oi",
        "oi_delta_pct_1h", "aggressive_buy_usd", "aggressive_sell_usd", "research_available_at",
    ]
    base = pd.Series(True, index=panel.index)
    for col in required:
        base &= panel[col].notna()
    return (base & funding_warmup_col).fillna(False)


def eligible_rvd_base(panel: pd.DataFrame, residual_std_30d_col: pd.Series) -> pd.Series:
    """Per-symbol half of RVD eligibility -- the cross-sectional minimum
    (MIN_CROSS_SECTION_SIZE) is NOT checked here, it needs every symbol's
    panel at once (see build_event_feature_panel.py's second pass) and is
    folded into the family's real eligible_rvd column afterward."""
    required = ["residual_return_1h", "basis_z_1d", "signed_volume", "research_available_at"]
    base = pd.Series(True, index=panel.index)
    for col in required:
        base &= panel[col].notna()
    return (base & residual_std_30d_col.notna()).fillna(False)


def eligible_ffr(panel: pd.DataFrame) -> pd.Series:
    required = ["residual_return_15m", "oi", "oi_delta_pct_1h", "research_available_at"]
    base = pd.Series(True, index=panel.index)
    for col in required:
        base &= panel[col].notna()
    flow_or_liq_available = panel["liq_feed_available"].fillna(False) | panel["signed_volume"].notna()
    return (base & flow_or_liq_available).fillna(False)
