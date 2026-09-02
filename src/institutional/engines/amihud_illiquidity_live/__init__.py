"""
src/institutional/engines/amihud_illiquidity_live/
─────────────────────────────────────────────────────────────────────────────
AMIHUD_ILLIQUIDITY_PREMIUM_V1 — live reconstruction of the independently
VALIDATED_FOR_FORWARD candidate (reports/edge_discovery/validation_2026-09/
AMIHUD_ILLIQUIDITY_PREMIUM/REPORT.md). Discovery: reports/edge_discovery/
alpha_hunt_2026-09-01_round3/w2_cross_sectional/REPORT.md, row
XSEC_AMIHUD_ILLIQ_7D.

Same data-source situation as CROSS_SECTIONAL_MOMENTUM_LIVE_V1/V2: the
validated construction used data_v2/normalized (worktree futur-data-v2, no
confirmed continuous live update) -- this live reconstruction instead reuses
this repo's ALREADY-BUILT live infrastructure read-only:
  - src.institutional.engines.cross_sectional_momentum_live.klines_source
    (generic Binance daily-klines cache, not momentum-specific)
  - src.institutional.engines.cross_sectional_momentum_live_v2.universe
    (dynamic live-universe resolution + PIT eligibility gate, not
    momentum-specific -- resolve_dynamic_liquid_universe/
    load_listing_calendar/resolve_onboard_dates/mask_pre_eligibility/
    build_pit_eligibility_log are all pure universe-membership utilities)
Neither module is modified by this package.

⚠ SHORT LEG (explicit, deliberate deviation from this project's standing
directional-short policy): project_short_audit.md (2026-05-22/23) found
STANDALONE DIRECTIONAL short-alpha (predicting price will fall) is not
viable in crypto (dead-cat-bounce risk, AUC 0.51-0.59) and set
SHORT_ENABLED=False "jusqu'à nouvel ordre" -- every other alpha in this
registry with a short-shaped element (WHALE_LSR_SCREEN_V1) implements it as
a SCREEN (zeroes a LONG, never opens a short) rather than an actual short
position. This alpha's SHORT leg is economically different: it is the
market-beta-hedging leg of a long-short RELATIVE-VALUE factor (long the
illiquid quintile, short the liquid quintile to isolate the illiquidity
premium) -- not a standalone bet that liquid names will fall. The validated
net edge (+105.7bps, independent reimplementation) is for the FULL
long-short spread; a long-only variant has not been separately validated.
This is SHADOW ONLY (ShadowExecutionAdapter, zero real capital) -- flagged
explicitly to the user rather than silently deviating from either the
validated spec (by going long-only unprompted) or the standing short
policy (by shorting without flagging it).
"""
