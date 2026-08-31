"""
src/institutional/engines/short_covering_continuation/state.py
─────────────────────────────────────────────────────────────────────────────
Pure classification functions (NO I/O) for SHORT_COVERING_CONTINUATION_V1.

Reconstructs the "price up + OI down (short covering)" state from
reports/edge_discovery/alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md
rank 2, whose exact numbers come from evidence/leverage_oi_O1_O9_full.json
key `O1_price_oi_quadrant_tail_deciles` -> `"price_up_oi_down (short
covering)"` (n=23,422 full / 7,217 OOS out of a baseline population of
2,055,173 symbol-hours -- i.e. ~1.14% of all bars, NOT the ~25% a plain
sign(price_ret)>0 AND sign(oi_delta)<0 split would flag). The evidence key's
own name ("quadrant_tail_deciles") and that population ratio both confirm
this was a TAIL-DECILE-conditioned state, not a broad sign split.

HONESTY NOTE (see freeze_spec.json `data_reconstruction_notes` for the full
version): the research-sweep worker's analysis script was ephemeral (a
subagent run, per the W2 REPORT.md header) -- only REPORT.md + the evidence
JSON survive, not the code that produced them. The exact decile boundary and
the lookback window used to define "own recent history" for each symbol's
percentile were NOT persisted anywhere and could not be recovered. The
constants below (PRICE_PCTILE_HI=0.90, OI_PCTILE_LO=0.10,
PCTILE_LOOKBACK_HOURS=720) are this module's best-effort, DOCUMENTED
reconstruction -- the most literal reading of "tail deciles" (top/bottom 10%)
computed as a causal trailing per-symbol time-series percentile (the
convention used everywhere else in this codebase for this kind of feature,
e.g. data_v2's funding_rate_percentile_90d -- see build_event_feature_panel.py
in the futur-data-v2 worktree), over a 30-day trailing window (no window was
specified in the source materials; 30d is a reasonable middle ground matching
other window conventions in this repo). This is NOT verified byte-for-byte
equivalent to the original panel's classification -- only order-of-magnitude
sanity-checked (see the runner script's live sanity-check output).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# "Tail decile" reconstruction of the O1 quadrant split (see module docstring
# for exactly why these two values, and their unverified status).
PRICE_PCTILE_HI = 0.90   # top decile of trailing 1h price return -> "price up" tail
OI_PCTILE_LO = 0.10      # bottom decile of trailing 1h OI % change -> "OI down" tail
PCTILE_LOOKBACK_HOURS = 720   # 30d trailing, causal, current bar excluded from its own population

SHORT_COVERING = "SHORT_COVERING"
OTHER = "OTHER"


def classify_state(
    price_pctile: float,
    oi_pctile: float,
    tau_price_hi: float = PRICE_PCTILE_HI,
    tau_oi_lo: float = OI_PCTILE_LO,
) -> str:
    """Classify a single (price_pctile, oi_pctile) pair -- each in [0, 1],
    a causal trailing percentile rank of that bar's 1h price return / OI %
    change against its own symbol's strictly-prior history (see
    `rolling_causal_percentile`).

    Returns SHORT_COVERING iff price is in the top tail decile AND OI is in
    the bottom tail decile simultaneously (an AND of two conditions -- this
    is the faithful reconstruction of the "quadrant" in the evidence key
    name: one dimension being extreme never compensates for the other being
    unremarkable). NaN-safe: any non-finite input -> OTHER (fail closed, no
    signal rather than a fabricated one).
    """
    if price_pctile is None or oi_pctile is None:
        return OTHER
    if not np.isfinite(price_pctile) or not np.isfinite(oi_pctile):
        return OTHER
    if price_pctile >= tau_price_hi and oi_pctile <= tau_oi_lo:
        return SHORT_COVERING
    return OTHER


def score_short_covering(price_pctile: float, oi_pctile: float) -> float:
    """Continuous [0, 1] conviction score feeding `classify_zone` (A/B/C),
    same role as CrossSectionalLongEngine's cross-sectional percentile.

    Deliberately a MIN-combinator, not an average: min(price_pctile,
    1 - oi_pctile). This keeps the score consistent with `classify_state`'s
    strict AND rule -- with the symmetric default thresholds above
    (OI_PCTILE_LO == 1 - PRICE_PCTILE_HI), `score >= PRICE_PCTILE_HI` is
    EXACTLY equivalent to `classify_state(...) == SHORT_COVERING`. An
    averaging combinator would let one very extreme dimension compensate for
    a mediocre other one (e.g. price_pctile=1.0, oi_pctile=0.50 would score
    0.75 under an average -- misleadingly close to the 0.90 threshold even
    though OI isn't remotely in its tail); min() correctly scores that case
    at 0.50, far from the threshold.
    """
    if price_pctile is None or oi_pctile is None:
        return 0.0
    if not np.isfinite(price_pctile) or not np.isfinite(oi_pctile):
        return 0.0
    return float(np.clip(min(float(price_pctile), 1.0 - float(oi_pctile)), 0.0, 1.0))


def rolling_causal_percentile(s: pd.Series, window: int = PCTILE_LOOKBACK_HOURS) -> pd.Series:
    """Percentile rank in [0, 1] of s[i] against the STRICTLY PRIOR `window`
    values s[i-window:i] -- s[i] itself is excluded from its own reference
    population (same discipline as the data_v2 pipeline's
    `_settlement_percentile_rank`: never judge a value against a window
    containing itself). NaN wherever fewer than 1 finite prior observation
    exists, or s[i] itself is non-finite. Empty-input-safe."""
    if s.empty:
        return s.astype("float64")

    def _pct(arr: np.ndarray) -> float:
        hist, cur = arr[:-1], arr[-1]
        if not np.isfinite(cur):
            return np.nan
        hist = hist[np.isfinite(hist)]
        if len(hist) == 0:
            return np.nan
        return float((hist <= cur).mean())

    return s.rolling(window=window + 1, min_periods=2).apply(_pct, raw=True)


def classify_state_df(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized `classify_state` + `score_short_covering` over a DataFrame
    carrying `price_ret_pctile`/`oi_delta_pctile` columns. Adds `state` and
    `score` columns. Empty-input-safe (returns df unchanged plus the two
    empty typed columns, matching liq_cascade/repeat_variant.py's
    empty-DataFrame convention)."""
    out = df.copy()
    if out.empty:
        out["state"] = pd.Series(dtype="object")
        out["score"] = pd.Series(dtype="float64")
        return out
    out["state"] = [
        classify_state(p, o) for p, o in zip(out["price_ret_pctile"], out["oi_delta_pctile"])
    ]
    out["score"] = [
        score_short_covering(p, o) for p, o in zip(out["price_ret_pctile"], out["oi_delta_pctile"])
    ]
    return out
