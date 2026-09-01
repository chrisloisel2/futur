"""
src/institutional/engines/cross_sectional_momentum_live_v2/signal.py
─────────────────────────────────────────────────────────────────────────────
Pure functions (NO I/O) for CROSS_SECTIONAL_MOMENTUM_LIVE_V2.

Reconstructs the SAME mechanism as CROSS_SECTIONAL_MOMENTUM_LIVE_V1 and the
true PIT original (reports/edge_discovery/alpha_hunt_2026-08-30/
w1_cross_sectional/REPORT.md, rank 1 / M1): cross-sectional 7-day trailing
return momentum, long-only, top-quintile selection at a non-overlapping
weekly rebalance. The causal math below is IDENTICAL to V1's signal.py by
design (this is a universe challenger, not a new mechanism) -- deliberately
NOT imported from that module, so this alpha's frozen spec (in particular
its own liquidity threshold, see MIN_LIQUIDITY_USD below) stands as an
independently auditable, independently frozen artifact that can never
silently drift if V1's module is ever touched.

LOOKBACK_DAYS / LIQUIDITY_WINDOW_DAYS / TOP_FRACTION / REBALANCE_WEEKDAY are
UNCHANGED from V1 and from the source report -- these are the mechanism's
own causal design constants (lookback, non-overlap window, the source
report's own verbatim top-quintile recommendation), not universe-related,
so there is no reason to touch them here. See freeze_spec.json for the full
threshold provenance.

MIN_LIQUIDITY_USD is the ONE constant that differs from V1 -- see its
docstring below for the full reasoning (this is a genuine judgment call
documented honestly, not a verbatim source number).
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
import pandas as pd

LOOKBACK_DAYS = 7                  # source report: "tret_7d = close[t]/close[t-7]-1", VERBATIM, unchanged from V1
LIQUIDITY_WINDOW_DAYS = 30         # source report: "trailing-30d median daily quote-volume", VERBATIM, unchanged from V1

# Source report's own base "liquid" cohort filter is >=$1M trailing-30d
# median daily quote-volume (VERBATIM, same number V1 uses). V2's candidate
# universe BEFORE this filter is dramatically larger and dramatically
# noisier than V1's: ~523 currently-TRADING USDT perpetuals (see universe.py)
# vs V1's curated 50-name list, and includes many symbols that only listed
# very recently (2026-vintage meme/micro-cap perpetuals with prefixes like
# "1000000BOB"/"1000000MOG"/"4"/"2Z", verified present in exchangeInfo at
# build time) that a human-curated frozen-50 list would never include.
#
# The source report's OWN robustness check found the effect works in the
# "mid/liquid" liquidity terciles but NOT in the "illiquid" tercile
# (t=0.19) -- but the report does not publish the numeric tercile
# boundaries, so they cannot be reproduced exactly (same "not a published
# constant" situation V1's freeze_spec.json documents for other thresholds
# in this registry). Rather than reuse the $1M floor verbatim -- which, on
# V2's much larger and much younger raw candidate set, would likely let a
# meaningful share of "illiquid tercile"-like names into the ranked cohort
# -- MIN_LIQUIDITY_USD is set to $2,000,000 (2x V1's floor) as a documented,
# CONSERVATIVE proxy for "closer to the mid/liquid terciles the source
# found significant, further from the illiquid tercile it found
# insignificant". This is an explicit judgment call, NOT a verbatim source
# number and NOT a grid-searched/data-snooped threshold (no historical
# panel was consulted to pick $2M specifically) -- it trades some universe
# breadth for a documented attempt to reduce contamination from the
# tercile the source report itself flagged as not working, given the exact
# tercile cutoffs are unavailable. See freeze_spec.json.universe section
# for the full accounting, including the honest caveat that this choice is
# still an approximation, not a reproduction, of the source's tercile split.
MIN_LIQUIDITY_USD = 2_000_000.0

TOP_FRACTION = 0.20                # source report's own verdict bucket, VERBATIM, unchanged from V1:
                                    # "a long-only top-quintile overlay captures most of the edge"
                                    # (decile table: q4=106.6bps vs q0=8.6bps) -- NOT touched by the
                                    # universe change; broadening the eligible cohort simply means
                                    # more names compete for the same top-20%-of-eligible slice.
REBALANCE_WEEKDAY = 0              # Monday (pd.Timestamp.weekday()==0), unchanged from V1 -- same
                                    # arbitrary-but-fixed, documented anchor.


def trailing_return(close: pd.Series, lookback: int = LOOKBACK_DAYS) -> pd.Series:
    """Causal trailing `lookback`-period return: close[t]/close[t-lookback]-1.

    Uses only `close[t]` and `close[t-lookback]` -- never a value later than
    `t` -- so this is causal at every index by construction (no lookahead
    possible via .shift() with a positive argument). `.shift()` is POSITION-
    based (rows back, not calendar days back): callers must pass a `close`
    series already reindexed onto a gap-free daily calendar (see the runner
    script) for `lookback` rows to equal `lookback` calendar days.
    Empty-input-safe."""
    if close.empty:
        return close.astype("float64")
    return close / close.shift(lookback) - 1.0


def trailing_liquidity_usd(quote_volume: pd.Series, window: int = LIQUIDITY_WINDOW_DAYS) -> pd.Series:
    """Causal trailing `window`-day MEDIAN of daily quote-asset volume (USD
    for a *USDT pair). pandas `.rolling()` is inherently causal (its window
    for row `t` is [t-window+1, t], never t+1..); `min_periods=window`
    requires the FULL window before returning a value, so a newly-listed
    symbol correctly reads NaN (ineligible) rather than a median computed
    from a handful of early days -- this is what keeps very freshly-listed
    2026-vintage perpetuals out of the ranking until they have genuine
    trading history, on top of the MIN_LIQUIDITY_USD floor itself.
    Empty-input-safe."""
    if quote_volume.empty:
        return quote_volume.astype("float64")
    return quote_volume.rolling(window=window, min_periods=window).median()


def weekly_rebalance_dates(dates, weekday: int = REBALANCE_WEEKDAY) -> List[pd.Timestamp]:
    """All calendar dates in `dates` falling on `weekday` (Monday=0 default)
    -- non-overlapping, 7-day-spaced rebalance points. Sorted, deduplicated.
    Empty-input-safe."""
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
    `top_fraction` of that eligible cohort -- long-only.

    Bucket size = ceil(top_fraction * n_eligible), minimum 1 whenever the
    eligible cohort is non-empty.

    Returns columns [symbol, tret_7d, liquidity_usd_30d, pct_rank,
    rank_in_bucket, n_eligible] -- empty (correctly typed) if nothing is
    eligible. `pct_rank` is computed over the WHOLE eligible cohort (not just
    the selected bucket)."""
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
    never appear. Columns: [event_time, symbol, tret_7d, liquidity_usd_30d,
    pct_rank, rank_in_bucket, n_eligible_universe, direction]. `direction`
    is always "LONG" (SHORT_REJECTED; this mechanism has no short leg by
    construction). Empty-input-safe."""
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
