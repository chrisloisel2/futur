"""SHORT_COVERING_CONTINUATION_V1 — state-conditioned continuation engine.

price up + OI down ("short covering") -> LONG continuation, vs baseline
non-conditionné. See reports/edge_discovery/alpha_hunt_2026-08-30/
w2_liquidation_leverage/REPORT.md (rank 2) for the original discovery
(measured on data_v2/normalized/event_feature_panel, a static backfill in
the separate futur-data-v2 worktree) and
reports/live_alpha_lab/SHORT_COVERING_CONTINUATION_V1/freeze_spec.json for
the honest accounting of how this live reconstruction differs.
"""
from src.institutional.engines.short_covering_continuation.state import (  # noqa: F401
    OI_PCTILE_LO, OTHER, PCTILE_LOOKBACK_HOURS, PRICE_PCTILE_HI, SHORT_COVERING,
    classify_state, classify_state_df, rolling_causal_percentile, score_short_covering,
)
from src.institutional.engines.short_covering_continuation.infer import (  # noqa: F401
    ShortCoveringContinuationEngine,
)
