"""
src/institutional/engines/amihud_illiquidity_live/signal.py
─────────────────────────────────────────────────────────────────────────────
Pure functions (NO I/O) for AMIHUD_ILLIQUIDITY_PREMIUM_V1. Reconstructs the
mechanism confirmed by independent validation (reports/edge_discovery/
validation_2026-09/AMIHUD_ILLIQUIDITY_PREMIUM/REPORT.md, PRIMARY_SPEC):
causal 30-day trailing Amihud illiquidity (mean |daily return| / dollar
volume), long the most-illiquid-eligible quintile, short the most-liquid
quintile, non-overlapping weekly rebalance, 7-calendar-day horizon.

Constants below are VERBATIM the validation report's PRIMARY_SPEC (chosen
before any of that worker's results were inspected) -- not reconstructed or
grid-searched here.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

ILLIQ_WINDOW_DAYS = 30      # validation report PRIMARY_SPEC: W=30 (classic Amihud ~1-month window)
LIQUIDITY_WINDOW_DAYS = 30  # validation report: causal rolling median 30d dollar volume
MIN_LIQUIDITY_USD = 1_000_000.0   # validation report PRIMARY_SPEC liquidity floor
MIN_VALID_DAYS = 20         # validation report: illiq_avg computable from >=20 valid days in its window
TOP_FRACTION = 0.20         # validation report: quintile (long most-illiquid, short most-liquid)
HORIZON_DAYS = 7            # validation report PRIMARY_SPEC: H=7 (== rebalance spacing, non-overlapping)
REBALANCE_WEEKDAY = 2       # Wednesday (pd.Timestamp.weekday()==2) -- validation report's anchor=0
                            # series starts 2020-03-18, a Wednesday; matched here so this live
                            # reconstruction's rebalance phase is the SAME one the validation's
                            # primary (not just anchor-pooled) result was measured on.


def daily_return(close: pd.Series) -> pd.Series:
    """Causal 1-day return: close[t]/close[t-1]-1. Empty-input-safe."""
    if close.empty:
        return close.astype("float64")
    return close / close.shift(1) - 1.0


def trailing_illiquidity(close: pd.Series, quote_volume: pd.Series,
                         window: int = ILLIQ_WINDOW_DAYS,
                         min_valid_days: int = MIN_VALID_DAYS) -> pd.Series:
    """Causal Amihud illiquidity, trailing `window` days ending STRICTLY
    before the current row: mean(|r(s,t)|/dollar_volume(s,t)) over
    [d-window, d-1]. `.shift(1)` before the rolling window means day `d`
    itself never enters its own signal -- day `d`'s return/volume are not
    yet known causally at decision time. min_periods=min_valid_days (not
    the full window) per the validation report: "computable from >=20 valid
    days", NaN (excluded) otherwise -- never imputed. Empty-input-safe."""
    if close.empty:
        return close.astype("float64")
    r = daily_return(close)
    daily_illiq = r.abs() / quote_volume.replace(0.0, np.nan)
    return daily_illiq.shift(1).rolling(window=window, min_periods=min_valid_days).mean()


def trailing_liquidity_usd(quote_volume: pd.Series, window: int = LIQUIDITY_WINDOW_DAYS) -> pd.Series:
    """Causal trailing `window`-day MEDIAN daily quote-volume -- same
    construction as cross_sectional_momentum_live_v2.signal's function of
    the same name, reimplemented here (not imported) so this alpha's frozen
    spec never silently drifts if that module is touched."""
    if quote_volume.empty:
        return quote_volume.astype("float64")
    return quote_volume.rolling(window=window, min_periods=window).median()


def weekly_rebalance_dates(dates, weekday: int = REBALANCE_WEEKDAY) -> List[pd.Timestamp]:
    """All calendar dates in `dates` falling on `weekday` -- non-overlapping,
    7-day-spaced rebalance points. Sorted, deduplicated. Empty-input-safe."""
    idx = pd.DatetimeIndex(dates)
    if len(idx) == 0:
        return []
    normalized = idx.normalize().unique()
    return sorted(d for d in normalized if d.weekday() == weekday)


def select_long_short_buckets(
    illiq: pd.Series,
    liquidity_usd: pd.Series,
    min_liquidity_usd: float = MIN_LIQUIDITY_USD,
    top_fraction: float = TOP_FRACTION,
) -> pd.DataFrame:
    """Single-rebalance-date cross-section: `illiq` / `liquidity_usd`
    indexed by symbol. Filters to the liquidity-eligible cohort
    (`liquidity_usd >= min_liquidity_usd` AND `illiq` finite), ranks by
    `illiq` DESCENDING (most illiquid first). LONG = top `top_fraction`
    (most illiquid, subject to the liquidity floor so never literally
    untradeable), SHORT = bottom `top_fraction` (most liquid) -- see
    __init__.py for why this alpha carries a genuine short leg.

    Bucket size = ceil(top_fraction * n_eligible), minimum 1 per side
    whenever the eligible cohort is large enough for both (no overlap
    between the two buckets by construction -- n_eligible must be >=2 for
    both sides to be non-empty).

    Returns columns [symbol, direction, illiq_avg, liquidity_usd_30d,
    pct_rank, n_eligible] -- empty (correctly typed) if nothing is
    eligible."""
    cols = ["symbol", "direction", "illiq_avg", "liquidity_usd_30d", "pct_rank", "n_eligible"]
    empty = pd.DataFrame(columns=cols)
    if illiq is None or liquidity_usd is None or len(illiq) == 0:
        return empty

    df = pd.DataFrame({"illiq_avg": illiq, "liquidity_usd_30d": liquidity_usd})
    df = df[(df["liquidity_usd_30d"] >= min_liquidity_usd) & df["illiq_avg"].notna()
           & np.isfinite(df["illiq_avg"])]
    n_eligible = len(df)
    if n_eligible < 2:
        return empty

    df = df.sort_values("illiq_avg", ascending=False)
    df["pct_rank"] = np.arange(1, n_eligible + 1) / n_eligible
    df["n_eligible"] = n_eligible

    bucket_size = max(1, int(np.ceil(top_fraction * n_eligible)))
    bucket_size = min(bucket_size, n_eligible // 2)   # no overlap between long/short legs
    if bucket_size < 1:
        return empty

    long_leg = df.iloc[:bucket_size].copy()
    long_leg["direction"] = "LONG"
    short_leg = df.iloc[-bucket_size:].copy()
    short_leg["direction"] = "SHORT"

    out = pd.concat([long_leg, short_leg]).reset_index().rename(columns={"index": "symbol"})
    return out[cols]


def build_weekly_decisions(
    panel_close: pd.DataFrame, panel_vol: pd.DataFrame,
    min_liquidity_usd: float = MIN_LIQUIDITY_USD, top_fraction: float = TOP_FRACTION,
    illiq_window: int = ILLIQ_WINDOW_DAYS, liquidity_window: int = LIQUIDITY_WINDOW_DAYS,
    rebalance_weekday: int = REBALANCE_WEEKDAY,
) -> pd.DataFrame:
    """Full decision panel: for every non-overlapping weekly rebalance date
    in `panel_close.index`, compute the causal illiquidity/liquidity
    cross-section and select the long/short quintile buckets. `panel_close`/
    `panel_vol` must already be masked for PIT eligibility (pre-listing
    cells NaN'd out) by the caller -- this function does no universe
    filtering of its own beyond the liquidity floor.

    Returns columns [event_time, symbol, direction, illiq_avg,
    liquidity_usd_30d, pct_rank, n_eligible] -- one row per (rebalance,
    selected symbol), both LONG and SHORT legs. Empty (correctly typed) if
    no rebalance dates or no eligible symbols anywhere."""
    cols = ["event_time", "symbol", "direction", "illiq_avg", "liquidity_usd_30d", "pct_rank", "n_eligible"]
    if panel_close.empty:
        return pd.DataFrame(columns=cols)

    illiq_panel = panel_close.apply(
        lambda s: trailing_illiquidity(s, panel_vol[s.name], window=illiq_window), axis=0)
    liq_panel = panel_vol.apply(lambda s: trailing_liquidity_usd(s, window=liquidity_window), axis=0)

    rebal_dates = weekly_rebalance_dates(panel_close.index, rebalance_weekday)
    rows = []
    for d in rebal_dates:
        if d not in illiq_panel.index:
            continue
        bucket = select_long_short_buckets(
            illiq_panel.loc[d], liq_panel.loc[d],
            min_liquidity_usd=min_liquidity_usd, top_fraction=top_fraction,
        )
        if bucket.empty:
            continue
        bucket = bucket.copy()
        bucket["event_time"] = d
        rows.append(bucket[cols])

    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.concat(rows, ignore_index=True)
