# W3 — Funding / Premium / Basis (Round 2) — Alpha Hunt 2026-08-30

Scope: perp-level funding/premium/basis dynamics on Binance, frozen 50-symbol universe
(`configs/portfolio_v1_1_parallel_50.yaml`, 48/50 symbols actually present in the source panel —
`PEPEUSDT`/`PYTHUSDT` missing under those exact tickers, likely a `1000PEPEUSDT`-style naming
mismatch in `data_v2/normalized/event_feature_panel`; not fixed, read-only). Data:
`/home/qbee/futur-data-v2/data_v2/normalized/event_feature_panel/venue=binance/` (PIT-safe via
`research_available_at`, 2020-2026, 5m grid, 24.7M rows for the frozen-50 slice). Sealed
`FUNDING_XVENUE_PROTOCOL.md` / `scripts/test_funding_xvenue_v0.py` **not touched**. A2, A2-RV-v1,
A13-H, TRM Fleet **not touched**. Quarterly-futures calendar-basis territory (W4's turf) not
duplicated beyond one already-known cross-check.

**14 distinct mechanisms tried** (M1-M14) plus one deliberate cross-check against yesterday's
"already exhausted" basis-level benchmark (not counted as new) and one sign-split refinement of
M1. All work is READ-ONLY; nothing here was fit on data outside a `train`/OOS split where an OOS
split was warranted for a promising candidate — everything below is fast-triage-level (pooled +
year-by-year stability, deciles, regime splits), consistent with how yesterday's W3 triaged before
deeper validation.

## Headline finding: a market-wide efficiency-compression signature, not isolated to one mechanism

Every basis/premium mean-reversion construction tested — single-symbol level fade (the known
"exhausted" A9 benchmark), cross-sectional basis ranking (M4), cross-sectional funding dispersion
(M3), vol-regime-conditioned fade (M7), convergence-speed-conditioned fade (M8), funding-surprise
fade (M12), even the multi-settlement cumulative-drift test (M11) — shows the **same shape**: large,
often highly significant edges in 2020-2021 (and a second hot patch around 2023-2024), collapsing
toward zero or flipping negative in **2025-2026**, the two years that matter most for any live
decision. This isn't one flaky mechanism; it's the same signature repeating across ~7 independently
constructed tests. Read as one thing: **crypto perp funding/basis mean-reversion has been
progressively arbitraged away**, most visibly in the last two years. No mechanism below should be
sized off its pooled 2020-2026 average — only the 2025-2026 sub-column is representative of what's
left to capture today, and in most cases that sub-column is thin-to-negative.

## Ranked table

Cost model: 5bps taker one-way, 10bps round-trip taker-taker (project default). All "net" figures
use a single 10bps round trip unless noted (cross-sectional long-only legs) or 20bps for genuinely
2-leg hedged constructions. PF/hit-rate computed directly on returns where noted; "—" = not computed
(mechanism already dead on t-stat/sign-instability grounds, further precision not warranted).

| rank | mechanism | dataset | horizon | events (n) | gross bps | est. net bps | PF / hit-rate | stability | capacity | confidence | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | M7 — basis-extreme fade x vol-regime (high-vol tercile) | event_feature_panel, 48 sym | 1d | 128,990 | +27.58 (pooled) | +17.58 pooled; **approx -13 to +5 in 2025-26** | PF 1.12, hit 52.2% | monotonic vol-scaling historically (low 8.6, mid 11.1, high 27.6bps), but high-vol edge itself decayed to ~0 by 2025-26 | full 48-symbol universe, thinner on small alts | genuine non-linearity found, but not currently deployable | **WEAK - real historical structure, decayed** |
| 2 | M4 — basis cross-sectional ranking, long cheap-basis decile | event_feature_panel, 48 sym | 1d | 218,183 (long leg) | +24.5 pooled (t=17.5) | +14.5 pooled; **-27.3 (2025) / -21.1 (2026) net** | PF 1.13, hit 48.5% | strong 2020-24, **sign-reversed both 2025 and 2026** | large - long-only, no shorting | genuinely new construction (cross-sectional, not single-symbol time-series) vs known-exhausted level fade | **WEAK - decayed/reversed, not deployable now** |
| 3 | M3 — cross-sectional funding dispersion, long low-funding decile | event_feature_panel, 48 sym | 1d | 42,306 | +21.46 pooled (t=6.8) | +11.46 pooled; **-19.7 (2025) / -34.3 (2026) net** | PF 1.11, hit 49.4% | strong 2021/2023, negative 2022/2025/2026 | large, long-only | requested angle ("cross-sectional funding dispersion, same venue, many symbols") - built as specified | **WEAK - same decay pattern, negative in both most-recent years** |
| 4 | M8 — premium convergence-speed conditioning ("already turning" subset) | event_feature_panel, 48 sym | 1d | 158,581 | +21.59 pooled | +11.59 pooled; **-2.0 (2025) / +5.35 (2026) net** | not computed | modest, doesn't clearly beat the plain baseline fade (+17.6 pooled net) | same as baseline | marginal incremental info over the already-known level fade | **WEAK - thin differentiation vs known effect** |
| 5 | M12 — funding-surprise (change, not level) fade | event_feature_panel, 48 sym | 15m | 55,101 | +14.04 pooled (t=4.7) | +4.04 pooled; **-13.0 (2025) / -6.0 (2026) net** | PF 1.07, hit 51.8% | positive 5/7 years but shrinking; 4h horizon not significant (t=1.66) at all | large | genuinely distinct from A10 (surprise not level) - real but thin even before decay | **WEAK - barely clears cost pooled, negative net in both recent years** |
| 6 | M5 — basis extreme x OI-confirmation ("genuine new positioning") | event_feature_panel, 48 sym | 1d | 164,717 (confirmed) | +13.18 pooled | +3.18 pooled | not computed | both confirmed (+13.18) and unconfirmed (+9.42) legs fade about equally | full universe | OI confirmation adds little beyond the base fade | **WEAK - minimal incremental value** |
| 7 | M6 — basis extreme x deleveraging-eligible flag | event_feature_panel, 48 sym | 1d | 301,066 (eligible) | +11.71 pooled (stable, positive every year) | +1.71 pooled | not computed | eligible leg stable-but-shrinking every year 2020-2026 (49 to 8.9bps); non-eligible leg noisy, driven by a collapsing sample (n=78 by 2026) | full universe | contradicts naive hypothesis (flagged-eligible is the *more* stable leg, not stronger) | **WEAK/inconclusive** |
| 8 | M1 — funding regime persistence (>=3 consecutive settlements in extreme decile), sign-split | event_feature_panel, 48 sym | 3d | 33,604 (high-funding leg) | momentum (ride-the-crowd LONG) +68.53 pooled (t=11.4); fade -60.06 (loses) | net momentum +58.53 pooled; **-52.0 (2025) / +112.8 (2026)** - wildly unstable | PF 1.21, hit 48.6% | **sign flips hard by year** (2022 -146, 2024 +220, 2025 -42, 2026 +123) - textbook regime-dependence | large in principle | large pooled effect, but this is the A10 pattern repeated at regime scale, not a stable edge | **WEAK/DEAD - fails year-stability bar despite huge \|t\|** |
| 9 | M2 — funding rate's own 2nd derivative (acceleration), level-controlled | event_feature_panel, 48 sym | 4h | 9,866 | -3.95 pooled (t=-1.31, not significant) | n/a | — | one year (2024, t=-4.0) drives most of it; otherwise flat | — | genuinely distinct construction from A9's basis-acceleration (also weak) - confirms the "2nd derivative" family doesn't hold up for funding either | **DEAD/WEAK - not significant pooled** |
| 10 | M14 — funding-harvest short, net of realistic funding income & cost | event_feature_panel, 48 sym | 3d hold | 33,604 | gross -58.87 (loses, t=-9.8) | **net -68.87 (t=-11.5)** | PF <1 (loses) | negative in 5/7 years | n/a - SHORT-shaped | **SHORT-shaped: NOT deployable, standing SHORT_REJECTED rule.** Clean quantification: this specific "short high funding, collect the funding" idea loses money even before institutional rejection - price momentum against the short exceeds the funding income captured | **DEAD (and SHORT_REJECTED)** |
| 11 | M13 — funding regime transition (extreme to normal exit bar) vs stayed-extreme | event_feature_panel, 48 sym | 1d | 32,993 (transition) | -15.44 pooled (fade loses, t=-4.9) | n/a | — | sign flips (2022 stayed-extreme +46.65 vs everywhere else negative) | — | no differentiation found between "transition" and "persistence" bars - both show the same momentum-wins signature | **DEAD** |
| 12 | M11 — multi-settlement cumulative drift after single extreme event (8h-40h) | event_feature_panel, 48 sym | 8h-40h | 90,463-90,576 | -2.17 to -17.64 (fade increasingly loses as horizon extends) | n/a | — | sign flips hard by year (2022 positive, 2021/2024 strongly negative); 2025/2026 collapse to ~0 across all horizons | — | extends A10's single-bar DEAD verdict cleanly to cumulative multi-settlement horizons | **DEAD** |
| 13 | M9 — premium momentum, multi-day horizon (trend continuation, not fade) | event_feature_panel, 48 sym | 7d | 2,020,146 | -21.19 pooled (t=-18.5); strong-trend quintile -50.37 (t=-19.3) | n/a | — | negative 2020-2024, weak-positive 2025-2026 | — | momentum loses badly at this horizon too - confirms fade/reversion dominates even at 7d, not new info | **DEAD as momentum (fade already known to dominate)** |
| 14 | M10 — cross-sectional basis dispersion (market-wide) as regime/vol signal | event_feature_panel, 48 sym | 1d | 52,244 hourly snapshots | corr(dispersion, fwd \|ret\|) = 0.024 | n/a - not a direct trade, descriptive only | — | top dispersion quintile shows some tail lift (490 vs ~365-380bps avg \|ret\|) but overall correlation negligible | — | no meaningful predictive power as tested; would need an options/vol vehicle to exploit even the tail effect (see existing A14 VRP work) | **DEAD/WEAK - no clear signal** |

*(BASELINE cross-check, not counted as new: pooled single-symbol basis-extreme-decile fade using
the same `basis` column as yesterday's exhausted A9 level effect — reproduces it exactly, +46/+47
to +7/+9 bps 2020 to 2025-26, monotonically shrinking, all years significant. Confirms the underlying
level effect is real-but-decaying rather than a fluke; not re-counted toward the 14.)*

## Mechanism detail

### M1 — Funding regime persistence (>=3 consecutive settlements in extreme funding-percentile decile)
**HYPOTHESIS**: being in a *persistent* (>=24h) extreme funding-percentile regime, not just a single
settlement event (A10), predicts fade-direction drift. **RESULT**: it predicts the *opposite* —
persistent high funding keeps *going with* the crowd (momentum), not against it, with huge pooled
significance (fade -60.06bps t=-10.5 at 3d, -147.9bps t=-17.3 at 7d). But split by year the sign is
unstable: 2022 and 2025 flip against the pooled direction, and by symbol-leg the low-funding "long"
side (n=2,581, weaker) is noisy. This is structurally the same failure mode as A10 (DEAD, sign
flips year to year) just measured at regime rather than single-event granularity, and with a much
bigger, more misleadingly-significant pooled number — a good example of why year-stability, not
pooled \|t\|, is the real bar. High-funding leg reframed as momentum-LONG (not the SHORT-shaped fade)
is the only piece that isn't outright SHORT_REJECTED-shaped, and it still fails stability (2025
-42bps vs 2026 +123bps net).

### M2 — Funding rate's own acceleration (2nd derivative), not basis acceleration
**HYPOTHESIS**: distinct from A9's already-weak basis-acceleration, does the funding *rate's own*
2nd derivative carry information once orthogonalized against level? **RESULT**: no — pooled t=-0.96
raw, t=-1.31 even after restricting to near-median funding-percentile rows to isolate pure
acceleration. One year (2024, t=-4.0) looks real in isolation but doesn't replicate elsewhere.
Confirms the "2nd derivative" family of constructions doesn't hold up for the funding leg either,
same verdict as the basis leg got yesterday.

### M3 — Cross-sectional funding dispersion, same venue, many symbols
**HYPOTHESIS** (explicitly requested angle, distinct from the sealed cross-venue protocol): at each
Binance settlement, rank all 48 symbols by funding rate; long the cheapest decile (shorts pay,
biased to get paid to be long), short the richest decile. **RESULT**: the long leg alone shows a
real, if decaying, historical edge (+21.46bps pooled, t=6.8, PF 1.11, hit-rate 49.4%) but reverses
sign in both 2025 (-9.67) and 2026 (-24.29). The short leg is SHORT-shaped and **loses money**
outright (-36.39bps pooled, t=-4.6) — a clean, unprompted confirmation that this flavor of
funding-harvest short doesn't work even setting aside the standing rejection. Combined market-
neutral spread nets a thin +10.29bps pooled, also decaying (2025 -7.46, 2026 -22.69).

### M4 — Basis cross-sectional ranking (long cheap-basis / short rich-basis, across symbols)
**HYPOTHESIS**: genuinely different from the already-exhausted single-symbol basis-level
time-series fade — rank all 48 symbols' basis at each hourly snapshot, trade the cross-sectional
extremes. **RESULT**: the strongest pooled number in this sweep (long leg +24.5bps, t=17.5,
n=218,183) but the clearest case of the decay pattern: strong every year 2020-2024 (up to +136.69bps
in 2021), then **flips negative in both 2025 (-17.3) and 2026 (-11.06)** — not noise, a real trend
reversal with large n and t=-6.2/-3.2 in those two years individually. Short leg loses money
overall (-10.23bps, SHORT-shaped anyway).

### M5 — Basis extreme x OI confirmation
**HYPOTHESIS**: does OI *expanding* in the same direction as a crowded basis extreme (genuine new
positioning) fade better/worse than OI flat/falling (stale, likely short-covering-driven basis)?
**RESULT**: minimal differentiation — confirmed +13.18bps vs unconfirmed +9.42bps, both fade in the
same direction with the same decay shape as everything else. OI confirmation doesn't meaningfully
sharpen the signal.

### M6 — Basis extreme x deleveraging-eligible flag
**HYPOTHESIS**: does the pre-existing `eligible_deleveraging` engineered flag (proxy for recent
liquidation pressure) identify a stronger fade? **RESULT**: counter-intuitively, the *eligible*
subset is the **more stable** one (positive every single year 2020-2026, though shrinking 49 to
8.9bps) while the *not-eligible* subset is noisier and its apparent pooled strength (+34.35bps) is
driven by a shrinking, less meaningful recent sample (n=78 in 2026). No exploitable differentiation
found.

### M7 — Basis extreme x volatility regime (residual_std_30d terciles)
**HYPOTHESIS**: does the basis fade work better in calm or turbulent markets? **RESULT**: a genuine,
monotonic non-linearity — low-vol +8.64bps, mid-vol +11.12bps, high-vol +27.58bps (t=12.2,
PF 1.12, hit-rate 52.2%) — economically sensible (bigger basis dislocations in turbulent regimes
mean-revert harder). But the high-vol edge itself has decayed to statistical zero in 2025 (-2.88,
t=-0.65) and stayed weak in 2026 (+3.4, t=0.49). Real historical structure, not currently capturable.

### M8 — Premium convergence-speed conditioning
**HYPOTHESIS**: does an extreme basis that's *already turning* back toward zero (\|basis_t\| <
\|basis_t-1h\|) predict a better fade entry than one still extending (diverging further)?
**RESULT**: "already turning" is modestly stronger (+21.59bps vs +15.05bps pooled) but not a
dramatic differentiator, and both decay the same way over time. Marginal value over the plain
baseline fade.

### M9 — Premium momentum, multi-day horizon (trend continuation)
**HYPOTHESIS**: distinct horizon regime from A9's 15m-1d velocity-fade test — does the basis
*trend* over the past week predict *continued* price direction over the next week? **RESULT**: no —
momentum loses badly (-21.19bps pooled, t=-18.5; strong-trend quintile -50.37bps, t=-19.3),
confirming mean-reversion dominates at this horizon too. Not a new tradeable finding (fade already
known), but a clean negative control.

### M10 — Cross-sectional basis dispersion as a market-wide regime signal
**HYPOTHESIS**: does market-wide basis dispersion (stddev across the 48-symbol cross-section)
predict elevated forward realized volatility — a regime filter, not a symbol-level trade?
**RESULT**: correlation with forward \|return\| is 0.024 — negligible. Top dispersion quintile shows
some tail lift (mean \|fwd_1d\| 490bps vs ~365-380bps in other quintiles) but the overall relationship
is too weak to act on, and there's no cheap vehicle to trade forward realized vol here anyway (the
existing options/VRP work, A14, already covers that ground on BTC).

### M11 — Multi-settlement cumulative drift after a single extreme funding settlement
**HYPOTHESIS**: distinct from A10's single 5m-4h-only horizons — does the effect show up
cumulatively over 1-5 settlements (8h-40h)? **RESULT**: no — if anything the fade result gets
*more* negative as the horizon extends (t=-2.05 at 8h to t=-6.87 at 40h), meaning momentum
strengthens with time, but the sign still flips by year (2022 positive, 2021/2024 strongly
negative) and **both 2025 and 2026 collapse to statistical zero across every horizon** — the
clearest single illustration of the sweep's decay theme.

### M12 — Funding surprise (change vs. previous settlement), not level
**HYPOTHESIS**: A10 tested the funding *level* at settlement and found nothing — does the *change*
from the prior settlement (a "surprise") carry information instead? **RESULT**: a real but thin
15m effect (+14.04bps pooled, t=4.7, PF 1.07, hit-rate 51.8%), positive in 5 of 7 years, but it
doesn't clear a realistic round-trip cost by much even pooled, and goes negative in 2025 (-3.02bps)
before a weak partial recovery in 2026 (+4.0bps). The 4h version isn't significant at all (t=1.66).

### M13 — Funding regime transition (extreme to normal exit) vs staying extreme
**HYPOTHESIS**: distinct from both A10 (single event) and M1 (persistence) — does the specific bar
where funding_rate_percentile crosses back from extreme to normal carry information the persistence
bars don't? **RESULT**: no differentiation — both the transition-exit bar (-15.44bps) and the
stayed-extreme bar (-12.03bps) show the same negative-fade/momentum-wins signature as M1 and M11,
with the same year-to-year sign instability (2022 flips positive for stayed-extreme).

### M14 — Funding-harvest short, quantified net of realistic funding income and cost
**HYPOTHESIS**: quantify the raw statistical signature of the classic "short persistently
high-funding coins, collect the funding" idea (flagged in the old audit as an "alternative viable"
idea never validated standalone). **SHORT-shaped — NOT deployable per standing SHORT_REJECTED
rule; quantified for the record only, as instructed.** **RESULT**: it loses money. Price PnL on the
short is -68.53bps over the 3-day hold (i.e. price keeps rising against the short — momentum, matching
M1), funding income only adds back +9.65bps, for a gross of -58.87bps and a net of **-68.87bps
(t=-11.51)** after a 10bps round trip, negative in 5 of 7 years. This is a clean, honest
confirmation that the standing SHORT_REJECTED policy is well-founded for this specific
construction — the idea doesn't just fail an institutional filter, it loses money on its own terms.
*(One bug caught and fixed here during construction — see below.)*

## Bug found and fixed (in this worker's own code, not the underlying data)

First pass of M14 computed `price_pnl_bps = -fwd_3d * 1e4` (correctly converting a return fraction
to bps) but then passed that already-bps column into the shared `report_block()` helper, which
*also* multiplies by 1e4 to do the fraction to bps conversion — a double-scaling bug that produced a
nonsensical "mean -685,313bps" (-6,853% average 3-day return). Investigated before writing it down:
checked the raw `fwd_3d` values behind it and confirmed they're real, bounded, sane numbers (max
+289% — `ORDIUSDT` on 2026-04-13, a genuine mania spike, not corrupted data; next largest are real
DOGE/MANA/GRT/ETC 2021 squeeze events). The bug was purely a units/scaling mistake in this session's
own script (passing pre-scaled bps into a helper that scales again), fixed by keeping all M14
columns as raw fractions and letting `report_block()` do the single conversion — same category of
"easy mistake to repeat" flagged in yesterday's report for the calendar-basis annualized-vs-raw
basis bug. Corrected numbers (gross -58.87bps, net -68.87bps) are what's reported above.

## Infrastructure note: shared scratchpad collision (no repo/report impact)

This session's designated scratchpad directory turned out to be **shared across all 10 parallel
workers** in this sweep (same session-scoped temp path). Two of this worker's intermediate script
files (`build_panel.py`, `run_mechanisms2.py`) were silently overwritten mid-task by another
worker's same-named files before being read back. Caught by noticing an unexpected file-state
change note; verified the actual data artifact (`panel_50.parquet`, 2.1GB, 24.7M rows) was still
intact and correct, then moved all of this worker's scratch files into an isolated
`scratchpad/w3_work/` subdirectory for the remainder of the run. No repo files, reports, or other
workers' outputs were affected — purely a temp-file naming collision in shared scratch space, not a
data or methodology issue.

## Files written

Under `/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-08-30/w3_funding_premium_basis/`:
- `REPORT.md` (this file)
- `evidence/mechanisms_partial1.json` ... `mechanisms_partial6.json` — incremental snapshots (kept
  for audit trail of what was computed in what order)
- `evidence/mechanisms_partial7_FINAL.json` — complete merged results for all 14 mechanisms +
  baseline cross-check + M1 sign-split refinement (the source for every number in this report)
- `evidence/hitrates.json` — hit-rate/profit-factor for the top-ranked candidates (M1, M3, M4, M7,
  M12)

All intermediate large panel caches (`panel_50.parquet`, ~2.1GB) were built and used in the shared
session scratchpad only, never copied into the repo, per the disk-space and no-raw-data-duplication
rules.
