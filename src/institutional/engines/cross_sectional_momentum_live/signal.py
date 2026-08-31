"""
src/institutional/engines/cross_sectional_momentum_live/signal.py
─────────────────────────────────────────────────────────────────────────────
Pure functions (NO I/O) for CROSS_SECTIONAL_MOMENTUM_LIVE_V1.

Reconstructs the mechanism described in reports/edge_discovery/
alpha_hunt_2026-08-30/w1_cross_sectional/REPORT.md, rank 1 (M1): cross-
sectional 7-day trailing return momentum, long-only, top-quintile selection
at a non-overlapping weekly rebalance. See freeze_spec.json for the full
threshold provenance -- every constant below is either VERBATIM from the
source report (lookback, liquidity filter) or explicitly the source's own
stated verdict (top-quintile long-only bucket), never an independently
invented/grid-searched value.
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
import pandas as pd

LOOKBACK_DAYS = 7                  # source report: "tret_7d = close[t]/close[t-7]-1", VERBATIM
LIQUIDITY_WINDOW_DAYS = 30         # source report: "trailing-30d median daily quote-volume", VERBATIM
MIN_LIQUIDITY_USD = 1_000_000.0    # source report: "liq>=$1M" universe filter, VERBATIM
TOP_FRACTION = 0.20                # source report's own verdict bucket: "a long-only top-quintile
                                    # overlay captures most of the edge" (decile table: q4=106.6bps
                                    # vs q0=8.6bps) -- NOT an independently invented/grid-searched
                                    # threshold, this is literally what the source report recommends.
REBALANCE_WEEKDAY = 0              # Monday (pd.Timestamp.weekday()==0). ARBITRARY but FIXED anchor:
                                    # the source report specifies "non-overlapping 7-day rebalance"
                                    # but does not name a weekday anchor -- Monday chosen as a
                                    # conventional week-start, documented here rather than left
                                    # implicit.


def trailing_return(close: pd.Series, lookback: int = LOOKBACK_DAYS) -> pd.Series:
    """Causal trailing `lookback`-period return: close[t]/close[t-lookback]-1.

    Uses only `close[t]` and `close[t-lookback]` -- never a value later than
    `t` -- so this is causal at every index by construction (no lookahead
    possible via .shift() with a positive argument). `.shift()` is POSITION-
    based (rows back, not calendar days back): callers must pass a `close`
    series already reindexed onto a gap-free daily calendar (see the runner
    script, which reindexes the full symbol x date panel to a complete daily
    `date_range` before calling this) for `lookback` rows to equal `lookback`
    calendar days. Empty-input-safe."""
    if close.empty:
        return close.astype("float64")
    return close / close.shift(lookback) - 1.0


def trailing_liquidity_usd(quote_volume: pd.Series, window: int = LIQUIDITY_WINDOW_DAYS) -> pd.Series:
    """Causal trailing `window`-day MEDIAN of daily quote-asset volume (USD
    for a *USDT pair) -- matches the source report's own liquidity-filter
    definition verbatim ("trailing-30d median daily quote-volume").
    pandas `.rolling()` is inherently causal (its window for row `t` is
    [t-window+1, t], never t+1..); `min_periods=window` requires the FULL
    window before returning a value, so a newly-listed symbol correctly
    reads NaN (ineligible) rather than a median computed from a handful of
    early days. Empty-input-safe."""
    if quote_volume.empty:
        return quote_volume.astype("float64")
    return quote_volume.rolling(window=window, min_periods=window).median()


def weekly_rebalance_dates(dates, weekday: int = REBALANCE_WEEKDAY) -> List[pd.Timestamp]:
    """All calendar dates in `dates` falling on `weekday` (Monday=0 default)
    -- non-overlapping, 7-day-spaced rebalance points, matching the source
    report's explicit non-overlapping-period design (it flags overlapping
    windows as an autocorrelation problem other reports ran into). Sorted,
    deduplicated. Empty-input-safe."""
    idx = pd.DatetimeIndex(dates)
    if len(idx) == 0:
        return []
    normalized = idx.normalize().unique()
    return sorted(d for d in normalized if d.weekday() == weekday)


def select_top_bucket(
    tret: pd.Series,
    liquidity_usd: pd.Series,
    min_liquidity_usd: float = MIN_LIQUIDITY_USD,
    top_fraction: float = TOP_FRACTION,
) -> pd.DataFrame:
    """Single-rebalance-date cross-section: `tret` / `liquidity_usd` indexed
    by symbol (e.g. one row of a wide date x symbol panel). Filters to the
    liquid-eligible cohort (`liquidity_usd >= min_liquidity_usd` AND `tret`
    finite), ranks by `tret` DESCENDING, and returns ONLY the top
    `top_fraction` of that eligible cohort -- long-only, matching the source
    mechanism's decile pattern (edge concentrated almost entirely in the top
    bucket, "winners keep winning", not a symmetric long-short spread).

    Bucket size = ceil(top_fraction * n_eligible), minimum 1 whenever the
    eligible cohort is non-empty (a strict floor of the fraction would round
    a small eligible cohort down to 0 and silently emit nothing even though
    a top performer clearly exists).

    Returns columns [symbol, tret_7d, liquidity_usd_30d, pct_rank,
    rank_in_bucket, n_eligible] -- empty (correctly typed) if nothing is
    eligible. `pct_rank` is computed over the WHOLE eligible cohort (not just
    the selected bucket), consistent with the source report's decile-table
    convention."""
    cols = ["symbol", "tret_7d", "liquidity_usd_30d", "pct_rank", "rank_in_bucket", "n_eligible"]
    empty = pd.DataFrame(columns=cols)
    if tret is None or liquidity_usd is None or len(tret) == 0:
        return empty

    df = pd.DataFrame({"tret_7d": tret, "liquidity_usd_30d": liquidity_usd})
    eligible = df[
        np.isfinite(df["tret_7d"]) & np.isfinite(df["liquidity_usd_30d"])
        & (df["liquidity_usd_30d"] >= min_liquidity_usd)
    ].copy()
    n_eligible = len(eligible)
    if n_eligible == 0:
        return empty

    eligible["pct_rank"] = eligible["tret_7d"].rank(pct=True, ascending=True)
    eligible = eligible.sort_values("tret_7d", ascending=False)
    n_bucket = max(1, math.ceil(top_fraction * n_eligible))
    top = eligible.iloc[:n_bucket].copy()
    top["rank_in_bucket"] = range(1, len(top) + 1)
    top["n_eligible"] = n_eligible
    top = top.reset_index().rename(columns={"index": "symbol"})
    return top[cols]


def build_weekly_decisions(
    panel_close: pd.DataFrame,
    panel_quote_volume: pd.DataFrame,
    min_liquidity_usd: float = MIN_LIQUIDITY_USD,
    top_fraction: float = TOP_FRACTION,
    lookback: int = LOOKBACK_DAYS,
    liquidity_window: int = LIQUIDITY_WINDOW_DAYS,
    rebalance_weekday: int = REBALANCE_WEEKDAY,
) -> pd.DataFrame:
    """Full causal pipeline over a wide date x symbol panel (`panel_close`,
    `panel_quote_volume` -- same shape/index/columns, index = a gap-free
    daily DatetimeIndex, columns = symbols): trailing return, trailing
    liquidity, weekly rebalance dates, top-bucket selection at each
    rebalance date.

    Returns one row per (event_time, symbol) SELECTED long decision --
    symbols NOT selected (illiquid, or outside the top bucket that week)
    never appear, matching the reference Mode A runners' only-tradeable-rows
    ledger convention (e.g. scripts/run_liq_cascade_repeat_shadow.py only
    writes `select_tradeable()` output, not every raw event). Columns:
    [event_time, symbol, tret_7d, liquidity_usd_30d, pct_rank,
    rank_in_bucket, n_eligible_universe, direction]. `direction` is always
    "LONG" (SHORT_REJECTED; this mechanism has no short leg by construction).
    Empty-input-safe."""
    cols = ["event_time", "symbol", "tret_7d", "liquidity_usd_30d", "pct_rank",
            "rank_in_bucket", "n_eligible_universe", "direction"]
    empty = pd.DataFrame(columns=cols)
    if panel_close is None or panel_close.empty:
        return empty

    ret_panel = panel_close.apply(lambda s: trailing_return(s, lookback))
    liq_panel = panel_quote_volume.apply(lambda s: trailing_liquidity_usd(s, liquidity_window))

    rebal_dates = weekly_rebalance_dates(panel_close.index, rebalance_weekday)
    frames = []
    for d in rebal_dates:
        if d not in ret_panel.index:
            continue
        picked = select_top_bucket(ret_panel.loc[d], liq_panel.loc[d], min_liquidity_usd, top_fraction)
        if picked.empty:
            continue
        picked = picked.rename(columns={"n_eligible": "n_eligible_universe"})
        picked.insert(0, "event_time", d)
        picked["direction"] = "LONG"
        frames.append(picked)

    if not frames:
        return empty
    out = pd.concat(frames, ignore_index=True)
    return out[cols]
