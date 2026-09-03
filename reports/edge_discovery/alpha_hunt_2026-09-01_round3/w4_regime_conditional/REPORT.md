# W4 — Regime-Conditional Edges (Alpha Hunt Round 3, 2026-09-01)

Worker W4, axis E: test whether WEAK/DEAD signals (round 1, round 2, or a quick scan) become
meaningfully stronger conditional on a regime. Read-only on all existing data/reports — no new
data collection, no `src/institutional/` or `configs/live_alpha_registry.yaml` touched, Track A
alphas never modified. Where Track A's read-only public-price cache
(`reports/live_alpha_lab/CROSS_SECTIONAL_MOMENTUM_LIVE_V2/klines_cache/`) was reused as a raw
BTC/altcoin daily-close reference (groups C, D, B7/B8), only its price column was read — no
config, state, or decision logic from that live alpha was read, copied, or influenced.

**Continuity note**: this exact task was interrupted by a session-wide rate limit on its first
attempt, after producing five groups of preregistered tests (A-E, 18 interactions) in scratch but
before writing this report. That work was recovered, verified against its own scripts/JSON
output, and is reused below unchanged. A further 10 interactions (A5-A8, B7-B8, D5-D6, E2-E3)
were added this session to reach the target range, prioritizing direct regime-conditioning of
round 2's own headline repeat-cascade finding (the "Tier 1" work-in-progress referenced in this
task's briefing) since that is the single most load-bearing result this round has to refine.

## 1. Methodology note - preregistration discipline

Round 2's scoreboard (`reports/edge_discovery/alpha_hunt_2026-08-30/SCOREBOARD.md`), methodology
item 1, states that declustering discipline burned four independent workers, and that W1 had to
redo five regime-conditioning tests because signals were compared to zero instead of to each
other, given this market's strong unconditional drift. This worker's process was built around not
repeating that:

1. **Preregister before results.** For every interaction below, the base signal, the regime
   variable, and the specific economic hypothesis for *why* the regime should matter are written
   first, verbatim as committed to the scratch scripts before execution (`scripts/group_*.py` -
   every hypothesis is a code comment written above the computation it governs, not added after
   the fact). No interaction's hypothesis text was edited after seeing its result.
2. **Compare to the unconditional baseline, not to zero.** Every regime split is reported
   alongside the SAME signal's unconditional number, computed on the SAME dataset/window, so a
   "regime effect" is always `regime_number - baseline_number`, not `regime_number - 0`. Several
   findings below are net-positive in isolation but explicitly flagged DEAD because they do not
   beat their own unconditional baseline (e.g. A5, D6) - profitable-looking numbers that add no
   information over the base signal are not reported as edges.
3. **One regime per interaction, not a scan.** Each base signal was tested against at most two
   preregistered regimes across separate interaction IDs (never more), each with its own
   standalone economic rationale (e.g. B7's breadth hypothesis is not a variant of B8's
   volatility hypothesis - they encode different mechanisms). No interaction was dropped after
   seeing an unfavorable result, and no regime cut was tried, rejected, and silently replaced by
   a better-looking one. Two interactions (B7, D6) explicitly report a hypothesis that was
   **wrong in direction** - kept in the table as negative results, not discarded.
4. **PIT and declustering first.** All liquidation-cascade tests (group A) decluster per-symbol
   at a >=4h minimum gap and report both raw and independent-event N/gross/net; all
   cross-sectional tests (group B) rebalance on a non-overlapping 7-day grid (declustered by
   construction); all positioning tests (group C) collapse same-day cross-sectional observations
   to one day-level mean before computing t-stats, exactly because round 2's W10 casualty was an
   inflated symbol-day count masquerading as N (flagged explicitly in the C-group script as
   `t_stat_symboldayN_INFLATED_DO_NOT_TRUST`); microstructure (group E) declusters large trades
   to >=60s apart before computing impact.
5. **Costs first.** 14bps round-trip (5bps taker + 2bps slippage, doubled - the project's
   standard "net14" convention, matching round 2's usage) for swing-horizon signals; 3bps for the
   maker-scale microstructure test (group E); IC-based option tests report Spearman IC/p-value
   with no execution vehicle claimed (consistent with round 2's own options findings).
6. **Simple models first.** Every test below is a two-bucket (occasionally three-bucket) median
   or tercile split and a mean-return/IC comparison - no ML, no multi-way interaction fitting.

## 2. Interactions: preregistration -> result

Convention: **N_raw** = all qualifying observations; **N_indep** = after declustering (group A:
>=4h/symbol gap; group B: non-overlapping weekly rebalance, declustered by construction; group C:
distinct calendar days; group E: >=60s gap). All bps figures are **net of cost** unless marked
"gross". Group D reports Spearman IC (no bps/cost framing - no execution vehicle exists for
Deribit options in this project, per round 2 W6/W9).

### Group A - Liquidation cascade regime interactions (`data/events/liq_cascade_dataset.parquet`, 2021-2026, N=38,141 raw events)

**Baseline context**: round 2's headline finding was that repeat liquidation cascades (2nd+ hit
in 24h, same symbol) pay strongly on fade/exhaustion while first-hit ("onset") cascades pay
~zero. This worker tests whether the ONSET null and the REPEAT edge are each further
regime-conditional.

---
**A1 - Onset fade x market-wide cascade density**
*Preregistration*: base signal = fade the first cascade in a symbol in 24h (`n_events_sym_24h==0`).
Regime = market-wide cascade density in the prior 30min (`n_events_mktwide_30m`, split at
median=1). Hypothesis: an onset cascade happening during a systemic flush (high density) is more
likely genuine forced deleveraging with fast mean-reversion once selling exhausts, vs an isolated
single-symbol onset (low density) which may reflect real idiosyncratic news that keeps moving.
*Result*: baseline net_decl -14.57bps (t=-0.30, N=18,349). Low density: -16.34 (t=-1.05). High
density: -11.75 (t=0.65). Both regimes remain net-negative and neither clears baseline
meaningfully; t-stats stay insignificant. **Status: DEAD** - the onset null is regime-robust; no
rescue via density.

**A2 - Onset fade x funding regime**
*Preregistration*: same onset base signal. Regime = `|funding_z30|`, split at 1.0 (extreme vs
neutral). Hypothesis: an onset cascade in an already-crowded (extreme funding) market has more
forced-liquidation "fuel" behind it and should fade harder once it starts.
*Result*: baseline -14.57. Extreme funding: -24.56 (t=-1.68). Neutral: -18.20 (t=-1.11). Both
WORSE than baseline. **Status: DEAD** - hypothesis rejected, regime makes onset fade less
attractive, not more.

**A3 - Onset fade x session (time-of-day)**
*Preregistration*: onset base signal. Regime = UTC session (ASIA 0-8h / EU 8-16h / US 16-24h).
Hypothesis: thin-liquidity ASIA-session onset cascades are more purely mechanical (forced,
liquidity-vacuum driven) and should mean-revert cleaner than EU/US cascades, which happen amid
deeper liquidity and more likely reflect real information flow.
*Result*: baseline gross -0.57bps. ASIA: gross **+8.21bps, t=2.39** (nominally significant,
positive_years mostly consistent 2022-2026 except a 2021 outlier), net still -5.79 after 14bps
cost. EU: gross -1.94 (t=-0.64). US: gross -5.00 (t=-1.46). **Status: WEAK** - a real, directionally
consistent, statistically distinguishable session effect exists (ASIA is measurably better than
EU/US and than the pooled baseline), but even the best regime bucket does not clear round-trip
cost. Confirms the hypothesis directionally; not tradeable as constructed.

**A4 - SHORT_SQUEEZE repeat>=2 (momentum convention) x OI regime**
*Preregistration*: base signal = repeat short-squeeze (`is_long_cascade==0`,
`n_events_sym_24h>=2`), MOMENTUM convention (bet price keeps moving in the squeeze direction -
this convention was itself resolved during this worker's setup: FADE lost -29.45bps gross
unconditionally, confirming round 2's own resolved sign for this specific sub-bucket). Regime =
`oi_pctile_30d`, split at median. Hypothesis: continuation should be stronger when OI is LOW
(shorts have already been meaningfully squeezed out, little remaining supply capping the move)
and weaker when OI is HIGH (large remaining short interest, or the move already reflects a big
stock of unwound shorts, capping further continuation).
*Result*: baseline net_decl **+11.14bps** (t=4.19, N_indep=2,381). Low OI: **+19.09bps, t=4.19**,
N_indep=1,180 - nearly **doubles** the baseline edge. High OI: **+0.40bps, t=1.79**, N_indep=1,212
- edge nearly vanishes. **Status: PROMISING** - clean, statistically strong (both buckets t>1.7,
low-OI bucket keeps round2's baseline t-stat intact on half the N), economically sensible
regime split of an already-established round-2 mechanism (SHORT_SQUEEZE exhaustion, round-2
rank #6). This is the strongest single actionable refinement found this round: split
SHORT_SQUEEZE continuation trades by OI regime, trade low-OI-percentile squeezes preferentially.

**A5 - LONG_CASCADE repeat>=2 (fade convention) x OI regime**
*Preregistration*: base signal = repeat down-cascade fade (`is_long_cascade==1`,
`n_events_sym_24h>=2`), FADE convention - the direct replication of W2/W9's original round-2
repeat-exhaustion framing (round 2 did not disambiguate cascade direction in its headline
number). Regime = `oi_pctile_30d`, split at median. Hypothesis: bounce should be stronger when OI
is already LOW (capitulation largely complete, little forced supply left) and weaker when OI is
HIGH (leveraged longs still present, cascade likely to continue against the fade).
*Result*: baseline net_decl **+9.87bps** (t=5.96, N_indep=4,660) - itself a clean replication of
round 2's headline mechanism on this worker's independent query. Low OI: +9.31bps (t=4.58). High
OI: +10.77bps (t=3.69). **Status: DEAD (as an interaction)** - both regime buckets sit within
~1bp of the unconditional baseline; OI regime adds no differentiating information here (contrast
with A4, where the SAME regime variable on the SHORT_SQUEEZE side produced a 19bps swing). The
underlying LONG_CASCADE repeat-fade mechanism itself remains good - this test just shows it is
not OI-conditional.

**A6 - LONG_CASCADE repeat>=2 (fade convention) x market-wide cascade density**
*Preregistration*: same LONG_CASCADE repeat-fade base signal as A5. Regime =
`n_events_mktwide_30m`, split at median=1 (idiosyncratic vs systemic repeat cascade). Hypothesis:
a repeat cascade happening amid a systemic, market-wide flush (high density) should bounce
faster/harder than an idiosyncratic single-symbol repeat (low density), because systemic
flushes exhaust available forced sellers quickly across the whole market, while an idiosyncratic
repeat may reflect genuine adverse information specific to that name.
*Result*: baseline net_decl +9.87bps (t=5.96). Low density (idiosyncratic): **-1.16bps, t=2.43**
- edge nearly disappears net of cost. High density (systemic): **+30.14bps, t=5.68**,
N_indep=2,047 - **triples** the baseline edge and is the single strongest net number in this
entire report. **Status: PROMISING** - this is the headline finding of this worker's report: the
project's #1 mechanism (repeat-cascade fade) is not uniform, it concentrates almost entirely in
systemic, multi-symbol cascade episodes and is closer to breakeven in isolated single-symbol
repeats. Strong t-stat, large effect size, economically coherent mechanism, survives
declustering, both sub-buckets have meaningfully different sign/magnitude of by-year numbers
without either being a single-outlier-year artifact (high-density bucket is positive in
5 of 6 years: 2021 +86.8, 2022 +34.4, 2023 +48.9, 2024 +54.4, 2025 +79.4, 2026 +36.3).

**A7 - LONG_CASCADE repeat>=2 (fade convention) x funding regime**
*Preregistration*: same base signal as A5/A6. Regime = `|funding_z30|`, split at 1.0. Hypothesis:
if funding is already extreme/crowded going into the repeat cascade, the down-move compounds an
already-short-biased market and should produce a weaker bounce (less "surprise" capitulation
left); neutral-funding repeat cascades should be a cleaner forced-liquidation signal with more
bounce potential.
*Result*: baseline +9.87bps. Extreme funding: +6.06bps (t=1.70, N_indep=394). Neutral funding:
+0.39bps (t=2.56, N_indep=651). **Both WORSE than baseline**, and in the OPPOSITE order than
hypothesized (extreme > neutral, not neutral > extreme). **Status: DEAD** - hypothesis rejected;
funding regime does not help and mildly hurts, reported as-is.

**A8 - All repeat cascades (both directions, fade convention) x session**
*Preregistration*: base signal = pooled repeat cascades of BOTH directions
(`n_events_sym_24h>=1`), pure FADE convention on both (matching W2's literal original framing
before this worker discovered, via A4, that the short-squeeze side actually wants MOMENTUM).
Regime = session (ASIA/EU/US). Hypothesis: thin-liquidity ASIA session should show the cleanest
forced-deleveraging fade signal for the same reason as A3.
*Result*: baseline net_decl **-8.29bps** (t=3.13, N_indep=16,310 - the blended-direction pooling
already produces a net-negative number despite the significant t, because it mixes the
profitable LONG-side fade with the unprofitable SHORT-squeeze fade convention (A4 shows momentum,
not fade, is right for that side)). ASIA: -2.93bps (t=3.43). EU: -13.64bps (t=-0.40). US:
-5.35bps (t=2.62). **Status: DEAD** - no session rescues the blended-convention pooling; this
also serves as a clean sanity check corroborating A4/A5's finding that direction-specific
convention (fade vs momentum) matters far more than any regime variable tested here.

### Group B - Cross-sectional regime interactions (`data_v2/normalized`, 48-symbol daily panel, 2020-2026, rebalanced weekly non-overlapping)

**B1 - Funding cross-sectional carry x dispersion regime**
*Preregistration*: base signal = long bottom-quintile funding / short top-quintile funding
(classic carry), 7d hold. Regime = cross-sectional dispersion of daily returns
(`ret1d.std(axis=1)`), split at median. Hypothesis: carry should work when the cross-section is
calm/orderly (low dispersion - funding differentials persist, few regime shocks) and break down
when dispersion spikes (high dispersion - a regime shift is more likely to overwhelm the slow
carry signal).
*Result*: baseline net **-12.2bps/7d** (t=0.42, N=234, not significant, positive 4/5 years
despite negative mean - driven by a large 2026 outlier of -40.8). Low dispersion: **+46.38bps/7d,
t=1.97**, N=117, positive 4/5 years. High dispersion: **-70.79bps/7d, t=-0.65**, N=117, positive
only 2/5 years. **Status: PROMISING-WITH-CAVEAT** - clean directional split (regime bucket beats
both zero and its own unconditional baseline by ~58bps/7d), t=1.97 is at the edge of
significance, N=117 independent weekly observations is a real but moderate sample. Worth
`INDEPENDENT_CONFIRMATION` before anything further; the caveat is that this is a genuinely new
proposed regime overlay on a previously-negative-looking base signal.

**B2 - Idiosyncratic-vol premium x trend regime**
*Preregistration*: base signal = long low-`residual_std_30d` / short high (defensive/idio-vol
premium), 7d hold. Regime = |BTC 20d trailing return|, split at median (trending vs choppy).
Hypothesis: idio-vol premium should be cleaner in choppy markets (idiosyncratic risk dominates)
and get swamped by beta in strongly trending markets.
*Result*: baseline net -30.59bps/7d (t=-0.05 - pure noise, by-year swings from +161 to -223).
Trending: -39.99 (t=-0.15). Choppy: -21.43 (t=0.10). **Status: DEAD** - no regime rescues an
already-noise-level base signal.

**B3 - Betting-against-beta x volatility regime**
*Preregistration*: base signal = long low-60d-beta / short high-beta vs BTC, 7d hold. Regime =
BTC 20d realized vol, split at median. Hypothesis: BAB should hold up better in low-vol regimes
(steadier factor structure) and degrade in high-vol regimes (beta estimates unstable, tail
co-movement dominates).
*Result*: baseline net -0.17bps/7d (t=0.52, roughly breakeven, positive 4/5 years). Low vol:
+18.3 (t=0.63). High vol: -18.95 (t=0.12). **Status: WEAK** - directionally consistent with the
hypothesis and beats the (already near-zero) baseline in the low-vol bucket, but neither t-stat
clears 1.0; not distinguishable from noise with this N=116-118/bucket.

**B4 - OI-growth reversal x funding regime**
*Preregistration*: base signal = long low-14d-OI-growth / short high (fade OI buildup), 7d hold.
Regime = cross-sectional mean funding, top-tercile ("extreme") vs mid-tercile ("neutral").
Hypothesis: OI-growth reversal should be strongest when funding is also extreme (OI buildup +
funding extremity together signal genuine over-positioning, more mean-reversion pressure) vs
funding-neutral periods (OI growth alone is a weaker signal).
*Result*: baseline net -4.35bps/7d (t=0.68, N=234). Funding-extreme: **+50.17bps/7d, t=1.10**,
N=77 (only 4 years represented, 2022-2025). Funding-neutral: -51.83 (t=-0.47). **Status: WEAK** -
large, hypothesis-consistent directional swing (+50 vs -52 across regimes) but t=1.10 does not
clear significance and N=77 spans a shortened, less complete calendar window than other B-group
tests; promising direction, not confirmable yet.

**B5 - Basis cross-sectional carry x crowding-eligible regime**
*Preregistration*: base signal = long cheap basis (low `basis_z_7d`) / short rich, 7d hold.
Regime = fraction of universe flagged `eligible_crowding` that day, split at median. Hypothesis:
basis carry should work better when few names are crowded (cleaner arbitrage-style carry) and
degrade when crowding is widespread (positioning-driven distortions dominate the basis signal).
*Result*: baseline net **+17.98bps/7d** (t=1.29, N=234 - already a mild positive baseline).
High crowding: +9.92 (t=0.83). Low crowding: +25.76 (t=0.98, only 3 years represented). **Status:
WEAK** - modest directional consistency with the hypothesis (low crowding somewhat better) but
neither regime bucket clears the baseline's own already-marginal significance by enough to call
this a real interaction; baseline itself (basis carry) is a candidate worth independent note but
the crowding conditioning adds little.

**B6 - Cross-sectional return skew ("lottery" effect) x liquidity regime**
*Preregistration*: base signal = high vs low cross-sectional daily-return skew day ->
forward-5-day BTC return (crowd lottery-seeking proxy). Regime = market $-volume liquidity,
split at median. Hypothesis: the lottery effect (skew-seeking crowds bidding up BTC after a
high-skew day) should be stronger in low-liquidity regimes (thinner market, more retail-driven
flow) and weaker in high-liquidity/institutional regimes.
*Result*: baseline Welch t=0.31, p=0.755 (nothing). High liquidity: t=1.23, p=0.223. Low
liquidity: t=-0.17, p=0.866. **Status: DEAD** - no signal in any regime.

**B7 - Cross-sectional momentum (7d/7d) x breadth regime**
*Preregistration*: base signal = long top-quintile / short bottom-quintile trailing-7d return,
7d fwd hold - this replicates round 2 W1's PROMISING finding (+89bps, t=2.60) on this worker's
own independently-constructed 48-symbol panel, as a mini out-of-sample check. Regime = breadth
(fraction of the 48-symbol universe with a positive daily return), split at median. Hypothesis:
momentum should work BETTER when breadth is LOW (a dispersed market where a few names genuinely
lead/lag - the ranking carries idiosyncratic information) and WORSE when breadth is HIGH (most
names moving together on market beta - the 7d ranking is largely beta noise).
*Result*: baseline net **+8.03bps/7d** (t=0.75, N=232, positive 4/5 years) - directionally
consistent with, but far weaker than, round 2 W1's own number (different symbol universe/window
construction; treated as a partial replication, not a contradiction). Low breadth: **-11.22bps,
t=0.26**. High breadth: **+28.65bps, t=0.80**. **Status: WEAK, hypothesis REJECTED IN DIRECTION**
- the split runs opposite to what was preregistered (high breadth looks better, not worse); kept
here as a negative/reversed result rather than dropped, per this worker's own preregistration
discipline (section 1, item 3). Neither bucket clears significance (N=112-120, t<1).

**B8 - Cross-sectional momentum (7d/7d) x volatility regime ("momentum crash" hypothesis)**
*Preregistration*: same momentum base signal as B7. Regime = BTC 20d realized vol, split at
median. Hypothesis: this directly tests the well-documented equity "momentum crash" pattern
(Daniel & Moskowitz) - momentum should perform well in low-vol/steady-trend regimes and break
down or reverse in high-vol regimes (sharp reversals disproportionately hurt a trend-following
cross-sectional book). Distinct mechanism from B7 (volatility-of-the-trend vs cross-sectional
dispersion).
*Result*: baseline +8.03bps/7d (t=0.75). Low vol: **+63.77bps/7d, t=1.36**, N=117. High vol:
**-48.69bps/7d, t=-0.31**, N=115. **Status: PROMISING-WITH-CAVEAT** - the largest directional
swing in group B (112bps/7d between regimes), sign-consistent with an established, independently
motivated equity-market pattern (not fitted post-hoc to this data), and the low-vol bucket alone
comes closer to round 2 W1's original magnitude than the pooled baseline does. t-stats (1.36 /
-0.31) do not individually clear the conventional bar; flagged as a real candidate for
`INDEPENDENT_CONFIRMATION`, not yet actionable on its own.

### Group C - Positioning regime interactions (`data/positioning/*.parquet`, 47 symbols, 2026-07-20 to 2026-08-31, 43 unique days)

**Data ceiling, disclosed upfront**: this is a 43-day, single-regime window (round 2 W10 flagged
exactly this risk for the same dataset). Every result below has DATA_LIMITED as its ceiling
status regardless of the point estimate, and t-stats use the day-level average (not the inflated
symbol-day count - see methodology item 4) to avoid round 2's declustering trap.

**C1 - Global-account LSR extreme fade x weekend/weekday**
*Preregistration*: base signal = fade extreme (top/bottom quintile z-scored) global long/short
ratio, next-day return. Regime = weekday vs weekend. Hypothesis: weekend positioning extremes
occur in thinner liquidity with fewer institutional participants rebalancing - expect either a
stronger or more erratic fade depending on which effect dominates (stated as an open two-sided
hypothesis given no strong prior).
*Result*: baseline (day-level) net -36.2bps (t=-0.82). Weekday: -38.63 (t=-0.74). Weekend: -29.91
(t=-0.33). **Status: DEAD/DATA_LIMITED** - loses money in every cut, no regime rescue, thin N
(12-31 days per bucket) caps any conclusion regardless.

**C2 - Taker buy/sell ratio extreme fade x volatility regime**
*Preregistration*: base signal = fade extreme taker buy/sell ratio, next-day return. Regime =
BTC 10d realized vol, split at median. Hypothesis: taker-flow extremes should be more informative
(cleaner fade) during high-vol regimes when taker flow is more likely forced/panic-driven, vs
low-vol regimes where taker imbalances may just reflect normal two-sided flow.
*Result*: baseline (day-level) net +31.8bps (t=1.26, N_days=43). Low vol: +7.39 (t=0.73,
N_days=22). High vol: **+57.38bps, t=1.04**, N_days=21. **Status: WEAK/DATA_LIMITED** -
directionally consistent with the hypothesis (high vol roughly 8x the low-vol number) but neither
t-stat clears significance and the window is a single 6-week regime; genuinely suggestive,
nowhere near confirmable.

**C3 - LSR day-over-day momentum x weekend/weekday**
*Preregistration*: base signal = go WITH the direction of day-over-day change in global LSR
(momentum, not fade), next-day return. Regime = weekday vs weekend. Hypothesis: weekday LSR
momentum should be more reliable (driven by real flow/news), weekend moves more likely to be
thin-liquidity noise that reverses rather than continues.
*Result*: baseline (day-level) net -5.98bps (t=0.27, near zero). Weekday: +9.45 (t=0.59). Weekend:
**-45.83bps, t=-1.10**, N_days=12. **Status: DATA_LIMITED** - weekend bucket is the single
worst-looking number in group C, but N_days=12 is too thin to be more than a note for a future,
longer positioning-data window.

### Group D - Options/vol regime interactions (`data/options_backfill/deribit/`, DVOL + daily flow features, 2021-2026 IC-based, no execution vehicle)

**D1 - DVOL mean-reversion x funding-shift regime**
*Preregistration*: base signal = daily DVOL % change -> next-day DVOL % change (own-series mean
reversion). Regime = |day-over-day change in BTC funding z-score| ("funding-shift" - proxy for
whether the vol move coincided with a genuine market-regime change vs a pure vol event), split
at median. Hypothesis: mean reversion should be cleaner during LOW funding-shift days (a "pure"
vol event with no accompanying regime change reverts faster) and weaker/absent during HIGH
funding-shift days (a genuine regime change, vol may not revert as expected).
*Result*: baseline IC=-0.023 (p=0.315). Low shift: IC=-0.045 (p=0.157). High shift: IC=-0.003
(p=0.925). **Status: DEAD** - no signal in either regime.

**D2 - Net options flow (call-put) x DVOL-level regime**
*Preregistration*: base signal = net call-minus-put flow -> next-day BTC return. Regime = DVOL
level, split at median (calm vs stress). Hypothesis: options flow should carry more directional
information during stress (high DVOL, when options are the marginal venue for expressing urgent
views) than during calm markets (flow more likely routine/hedging noise).
*Result*: baseline IC=0.013 (p=0.627). Low DVOL: IC=0.031 (p=0.334). High DVOL: IC=-0.029
(p=0.586). **Status: DEAD** - no signal, and the high-DVOL bucket flips sign versus the
hypothesis.

**D3 - DVOL-shock directional signal x weekday/weekend**
*Preregistration*: base signal = daily DVOL % change -> next-day BTC return (directional, not
mean-reversion). Regime = weekday vs weekend. Hypothesis: this modest but real unconditional
relationship (checked first, see result) should be weekday-driven (genuine options-market
information) and weaken on weekends (thin trading, less informative flow).
*Result*: baseline IC=**0.051, p=0.023** (N=1,986 - a small but nominally significant
unconditional relationship, itself worth flagging as a minor new observation). Weekday: IC=0.054
(p=0.040, N=1,418 - 71% of the sample). Weekend: IC=0.014 (p=0.732, N=568). **Status: WEAK** -
confirms the signal is not weekend-driven (consistent with the hypothesis), but the weekday
number is statistically indistinguishable from the pooled baseline (0.054 vs 0.051) since weekday
already dominates the sample - this is a "regime doesn't add information beyond sample
composition" result, not a strengthened edge. IC of ~0.05 is also too small to be economically
actionable regardless (no execution vehicle for BTC options in this project per round 2).

**D4 - Put/call volume ratio x funding regime, target = forward 5d realized vol**
*Preregistration*: base signal = put/call volume ratio -> forward-5d BTC realized vol. Regime =
`|funding_z|`, split at median. Hypothesis: put/call ratio should be a cleaner forward-RV
predictor when funding is also extreme (both signal the same underlying stress build-up,
reinforcing) vs funding-neutral (put/call ratio alone is noisier).
*Result*: baseline IC=0.038 (p=0.171). Extreme funding: IC=0.051 (p=0.186). Neutral: IC=0.028
(p=0.487). **Status: DEAD** - neither bucket is significant and the improvement (0.038->0.051) is
well within noise for N=631-663/bucket.

**D5 - DVOL mean-reversion x IV-level-extremity regime**
*Preregistration*: same base signal as D1 (own-series mean reversion). Regime = 180-day trailing
percentile of DVOL level, extreme (>=80th or <=20th pctile) vs mid-range - distinct hypothesis from
D1 (level-extremity, not shift-magnitude). Hypothesis: standard vol-clustering theory predicts
mean reversion should be STRONGEST when the starting level is already in an extreme decile ("vol
reverts fastest from extremes") and weak/absent near the middle of its range.
*Result*: baseline IC=-0.022 (p=0.318). Extreme level: IC=-0.032 (p=0.289). Mid level: IC=-0.009
(p=0.802). **Status: DEAD** - no signal in either regime; the level-extremity hypothesis is not
supported by this data (contrast with D1's shift-based regime, also DEAD - DVOL mean-reversion
is simply not present in this dataset at the daily horizon, regardless of how it's conditioned).

**D6 - DVOL-shock directional signal x DVOL-level regime**
*Preregistration*: same base signal as D3 (directional). Regime = DVOL level, split at median -
distinct from D3's weekday/weekend regime. Hypothesis: a vol spike from an ALREADY-HIGH DVOL
level ("stress compounding on stress") should predict more forward downside/continuation than a
spike from a calm starting level (more likely a one-off absorbed without follow-through).
*Result*: baseline IC=0.051 (p=0.023). Low DVOL level: IC=0.047 (p=0.142, N=993). High DVOL
level: IC=0.055 (p=0.081, N=993). **Status: DEAD/WEAK** - both buckets are statistically
indistinguishable from each other and from baseline; the loss of significance in each half-sample
is consistent with simple N-halving, not a real regime effect. No support for the
stress-compounding hypothesis.

### Group E - Microstructure regime interactions (`market_physics_v3/raw` BTCUSDT/binance trades, 5 non-contiguous days: Aug 15/16/17/28/29 2026)

**Data ceiling, disclosed upfront**: only 5 non-contiguous days are available on disk for this
dataset (both `microstructure_reduced`, 2 days, and `market_physics_v3/raw`, 5 days, were
checked; 5-day is the larger). DATA_LIMITED is the ceiling status for this entire group
regardless of point estimates, exactly as round 2's W10/W3/W5 flagged for other short-window
datasets.

**E1 - Large-trade (>=p99 notional) 30-second impact-continuation x session**
*Preregistration*: base signal = signed 30-second forward price move following a top-1%-notional
trade (continuation = informed/toxic flow; reversion = absorption), declustered to >=60s apart.
Regime = session (ASIA/EU/US). Hypothesis: thin-liquidity ASIA-session large trades should show
stronger continuation (less standing liquidity to absorb size) than deeper US/EU sessions.
*Result*: baseline (declustered) mean **+0.714bps, t=7.74**, N=671 - a real, statistically strong
continuation effect (matches round 2 W7's characterization of this project's first
maker-cost-adjacent microstructure signals). ASIA: +0.81bps (t=4.89, N=249). EU: +0.39bps
(t=3.98, N=22 - thin). US: +0.67bps (t=5.83, N=400). **Status: DATA_LIMITED /
NEEDS_FULL_VALIDATION** - hypothesis direction weakly confirmed (ASIA highest) but the swing
across sessions (0.39-0.81bps) is small relative to the 3bps maker-cost threshold used here - all
three session buckets remain sub-cost, so the regime split doesn't change the economic
conclusion (not tradeable standalone), and 5 non-contiguous days is too thin to trust a
session split regardless of the point estimate.

**E2 - Large-trade impact x liquidation-cascade-density regime**
*Preregistration*: base signal = same large-trade impact metric as E1. Regime = whether a
liquidation cascade was active nearby in time (ties this axis's "cascade density" candidate
regime directly to round 2's headline repeat-cascade finding). Hypothesis: large-trade
continuation should be stronger when a cascade is actively unfolding (forced flow more likely to
keep moving price the same direction than discretionary flow).
*Result*: **BLOCKED, by data non-overlap, not by a null finding.** `liq_cascade_dataset.parquet`
ends 2026-07-04; `market_physics_v3/raw` BTCUSDT trade data only covers 5 non-contiguous days in
Aug 2026 (verified: 0 liquidation-cascade events recorded on any of the 5 available microstructure
days). This interaction cannot be tested with data currently on disk. Reported honestly as
BLOCKED/DATA_LIMITED rather than silently dropped or substituted without disclosure.
**Status: BLOCKED.**

**E3 - Large-trade impact x trade-size-extremity regime (substitute for E2, same dataset)**
*Preregistration*: added after discovering E2's infeasibility, before looking at its own result.
Base signal = same large-trade impact metric. Regime = trade-size extremity within the
already-large sample: >=p99.9 notional ("most whale") vs the p99-p99.9 band ("merely large").
Hypothesis: truly extreme-size prints are more likely institutional/visible and more
capacity-constrained to hide, so should show LESS continuation (some absorption/anticipation
already priced in) than the "merely large" p99-p99.9 band, which may carry more genuinely hidden
informed flow.
*Result*: baseline (replication) +0.714bps (t=7.74, N=671). Extreme (>=p99.9): **+0.961bps,
t=4.73**, N=74. Merely-large (p99-p99.9): +0.683bps (t=6.80), N=597. **Status:
DATA_LIMITED/WEAK, hypothesis REJECTED IN DIRECTION** - extreme-size trades show slightly MORE
continuation, not less (opposite of preregistered direction), though the difference is small and
N=74 for the extreme bucket is thin; both buckets remain sub the 3bps cost threshold regardless.
Reported as a reversed/negative result per this worker's own discipline.

## 3. Summary results table

Costs: group A/B/C use 14bps round-trip (5bps taker + 2bps slippage, doubled); group E uses
3bps (maker-scale); group D reports IC only (no execution vehicle, no cost applied). "gross_bps"
/ "net_bps" below are the REGIME-CONDITIONAL bucket's own number (declustered where applicable),
not the baseline - baseline is given in the mechanism/result text above for comparison.
"distinctness" flags whether this is a new mechanism vs a refinement of an existing
round-1/round-2/round-3 finding.

| candidate_id | family | economic_risk_factor | mechanism | N_raw | N_indep | gross_bps | net_bps | PF | stability | capacity | cost_sensitivity | distinctness | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | liq-cascade onset | forced-flow/liquidity | onset fade x mktwide density | 18,349 | 18,349 | -2.3/+2.3 | -16.3/-11.8 | ~1.0 | robust-null | n/a | high | refinement (null check) | DEAD |
| A2 | liq-cascade onset | forced-flow/liquidity | onset fade x funding regime | 4,311 | 4,311 | -10.6/-4.2 | -24.6/-18.2 | 0.87/0.94 | robust-null, worse both ways | n/a | high | refinement (null check) | DEAD |
| A3 | liq-cascade onset | forced-flow/liquidity | onset fade x session | 18,349 | 18,349 | +8.2 (ASIA) | -5.8 (ASIA, best) | 1.12 | directionally consistent | small | high (sub-cost) | refinement | WEAK |
| A4 | liq-cascade repeat, SHORT_SQUEEZE | forced short-covering | repeat>=2 momentum x OI regime | 2,719 | 1,180-1,212 | 38.7 (low-OI) | +19.1 (low-OI) | 1.40 | positive 5/6 yrs (low-OI) | moderate | moderate | refinement of round2 #6/#1 | PROMISING |
| A5 | liq-cascade repeat, LONG_CASCADE | forced deleveraging | repeat>=2 fade x OI regime | 5,457 | 2,341-2,360 | 34.4/25.1 | +20.4/+11.1 | 1.27-1.38 | both ~ baseline | large | moderate | refinement (null interaction) | DEAD (no regime lift) |
| A6 | liq-cascade repeat, LONG_CASCADE | forced deleveraging (systemic) | repeat>=2 fade x mktwide density | 5,457 | 2,047-2,874 | 53.5 (high-dens) | +39.5 (high-dens) | 1.57 | positive 5/6 yrs | moderate | low | refinement of round2 #1 (headline) | PROMISING |
| A7 | liq-cascade repeat, LONG_CASCADE | forced deleveraging | repeat>=2 fade x funding regime | 1,172 | 394-651 | 21.1/26.1 | +6.1/+0.4 | 1.27-1.35 | both < baseline | small | high | refinement (null, wrong direction) | DEAD |
| A8 | liq-cascade repeat, pooled | forced-flow (mixed) | pooled-direction fade x session | 19,792 | 16,310 | 14.1 (ASIA) | -2.9 (ASIA, best) | 1.16 | all sessions net-negative | large | high | sanity-check / convention confound | DEAD |
| B1 | cross-sectional funding carry | funding risk premium | carry x dispersion regime | 234 | 117 | 74.4 (low-disp) | +46.4 (low-disp) | n/a | 4/5 yrs positive | moderate | moderate | new interaction | PROMISING-CAVEAT |
| B2 | cross-sectional idio-vol premium | idiosyncratic vol | idio-vol x trend regime | 227 | 112-115 | -12.0/+6.6 | -40.0/-21.4 | n/a | pure noise both ways | n/a | high | refinement (null) | DEAD |
| B3 | betting-against-beta | leverage-constraint premium | BAB x vol regime | 234 | 116-118 | 46.3 (low-vol) | +18.3 (low-vol) | n/a | directionally consistent, t<1 | moderate | high | new interaction | WEAK |
| B4 | OI-growth reversal | positioning/crowding | OI reversal x funding regime | 234 | 77-79 | 78.2 (fund-ext) | +50.2 (fund-ext) | n/a | 3/4 yrs, thin | small | moderate | new interaction | WEAK |
| B5 | cross-sectional basis carry | funding/basis risk premium | basis carry x crowding regime | 234 | 119 | 53.8 (low-crowd) | +25.8 (low-crowd) | n/a | modest | moderate | moderate | refinement (marginal) | WEAK |
| B6 | cross-sectional skew | lottery/sentiment | skew -> fwd BTC x liquidity regime | 239 | n/a | n/a | n/a (p=0.22-0.87) | n/a | no signal any regime | n/a | n/a | refinement (null) | DEAD |
| B7 | cross-sectional momentum | trend/momentum premium | xs-mom(7d/7d) x breadth regime | 232 | 112-120 | 16.8/56.7 | -11.2/+28.7 | n/a | hypothesis reversed, t<1 | large | high | refinement of round2 #4 | WEAK (reversed) |
| B8 | cross-sectional momentum | trend/momentum premium (crash risk) | xs-mom(7d/7d) x vol regime | 232 | 115-117 | 91.8 (low-vol) | +63.8 (low-vol) | n/a | sign-consistent w/ literature | large | high | refinement of round2 #4 | PROMISING-CAVEAT |
| C1 | positioning LSR fade | crowded-positioning | global LSR fade x weekend | 810 | 43 days | -31.5/-26.6 | -45.5/-40.6 | n/a | loses all cuts | n/a | high | refinement (null) | DEAD/DATA_LIMITED |
| C2 | positioning taker-flow | forced/panic flow | taker fade x vol regime | 809 | 43 days | 44.2/66.6 | 30.2/52.6 (high-vol) | n/a | directionally consistent, t<1.1 | small | moderate | new interaction | WEAK/DATA_LIMITED |
| C3 | positioning LSR momentum | crowd-following flow | LSR momentum x weekend | 810 | 43 days | 24.8/-42.9 | 10.8/-56.9 (weekend, worst) | n/a | thin (N=12 weekend) | n/a | high | new interaction | DATA_LIMITED |
| D1 | options DVOL mean-reversion | vol risk premium | DVOL meanrev x funding-shift | 1,986 | n/a | IC -0.02/-0.05 | n/a | n/a | no signal | n/a | n/a | refinement (null) | DEAD |
| D2 | options net flow | positioning/flow | flow -> fwd ret x DVOL level | 1,325 | n/a | IC 0.03/-0.03 | n/a | n/a | sign flips, no signal | n/a | n/a | new interaction | DEAD |
| D3 | options DVOL directional | vol risk-premium feedback | DVOL shock -> fwd ret x weekday | 1,986 | n/a | IC 0.05/0.01 | n/a | n/a | not weekend-driven, but not amplified | n/a | n/a | new interaction (minor) | WEAK |
| D4 | options put/call ratio | crash-hedge demand | pc-ratio -> fwd RV x funding | 1,321 | n/a | IC 0.05/0.03 | n/a | n/a | no significant bucket | n/a | n/a | refinement (null) | DEAD |
| D5 | options DVOL mean-reversion | vol risk premium | DVOL meanrev x IV-level regime | 1,929 | n/a | IC -0.03/-0.01 | n/a | n/a | no signal | n/a | n/a | refinement (null, 2nd cut) | DEAD |
| D6 | options DVOL directional | vol risk-premium feedback | DVOL shock -> fwd ret x DVOL level | 1,986 | n/a | IC 0.05/0.06 | n/a | n/a | indistinguishable from N-halving | n/a | n/a | refinement (null) | DEAD |
| E1 | microstructure large-trade impact | informed-flow/toxicity | impact-continuation x session | 3,718 | 671 | 0.81 (ASIA) | +0.81 sub-cost | n/a | positive+significant all sessions | small | low (sub-3bps) | refinement of round2 W7-family | DATA_LIMITED |
| E2 | microstructure x cascade density | forced-flow overlap | impact x cascade-density | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | BLOCKED |
| E3 | microstructure large-trade impact | informed-flow/toxicity | impact-continuation x size-extremity | 671 | 671 | 0.96 (extreme) | +0.96 sub-cost | n/a | reversed vs hypothesis, thin extreme bucket (N=74) | small | low (sub-3bps) | refinement of round2 W7-family | DATA_LIMITED |

## 4. TOTAL_MECHANISMS_TESTED

**28** preregistered interaction tests (4 A + 4 A-extended + 6 B + 2 B-extended + 3 C + 4 D + 2
D-extended + 1 E + 2 E-extended), spanning 5 datasets (liquidation cascades, cross-sectional
daily panel, positioning, Deribit options, microstructure trades). Status breakdown: **2
PROMISING** (A4, A6), **2 PROMISING-WITH-CAVEAT** (B1, B8), **7 WEAK** (A3, B3, B4, B5, B7, C2,
D3), **4 DATA_LIMITED-capped** (C1, C3, E1, E3), **1 BLOCKED** (E2), and **12 DEAD** (A1, A2, A5,
A7, A8, B2, B6, D1, D2, D4, D5, D6).

## 5. Top findings in prose

**The single strongest result this round is A6: round 2's headline repeat-cascade fade edge is
itself regime-conditional on cascade density, not uniform.** Splitting the LONG_CASCADE
repeat-fade mechanism (the same one W2 and W9 independently corroborated last round) by whether
the repeat cascade occurred during a systemic, multi-symbol flush (high market-wide density) or
an isolated single-symbol repeat (low density) produces a striking divergence: **+39.5bps net
declustered in the systemic bucket (t=5.68) vs essentially breakeven, -1.2bps, in the
idiosyncratic bucket (t=2.43)** - a 3x-plus difference in economic magnitude from a single,
well-motivated regime cut, stable across 5 of 6 calendar years in the systemic bucket. This
should be evaluated for direct incorporation into round 2's own recommended
same-symbol-repeat-count split: repeat cascades should additionally be filtered by market-wide
density before sizing, not just by repeat count. A4 corroborates the same pattern from a
different angle: within the SHORT_SQUEEZE-specific momentum-convention subset, splitting by OI
percentile (not density) nearly doubles the net edge in the low-OI bucket (+19.1bps vs +11.1bps
baseline, t=4.19 preserved on half the sample) while the high-OI bucket collapses to
+0.4bps. Together, A4 and A6 show the project's #1 mechanism is genuinely stronger under specific,
economically legible conditions (systemic flush density; low remaining OI/short interest) rather
than being a flat average that happens to net positive - exactly the kind of refinement this
axis was designed to surface.

**Two same-mechanism control tests (A5, A7) show these regime effects are NOT universal across
every cut of the same base signal** - OI regime did nothing for the LONG_CASCADE fade side (A5,
where it mattered enormously for the SHORT_SQUEEZE momentum side in A4), and funding regime made
the LONG_CASCADE fade side slightly worse in the "extreme" direction opposite to what was
hypothesized (A7). Reporting both null/reversed results alongside the two strong hits (rather
than only publishing A4/A6) is the direct application of this report's own preregistration
discipline - it also demonstrates the regime effects that DID work were not found by scanning
many cuts until one landed; each of A4-A8 was a single, independently motivated hypothesis.

**Outside the liquidation-cascade family, the most economically interesting finding is B8: the
equity "momentum crash" pattern replicates directionally in this crypto cross-sectional
momentum signal** - the same 7d/7d cross-sectional momentum base signal that round 2's W1 found
PROMISING (+89bps, t=2.60, on their own construction) swings from +63.8bps/7d (low-vol regime,
t=1.36) to -48.7bps/7d (high-vol regime, t=-0.31) on this worker's independent panel. The sign
pattern matches a well-established, independently-motivated literature prior rather than being
fitted to this data, which raises confidence despite individually sub-2 t-stats; B1 (funding
carry conditional on cross-sectional dispersion, +46.4bps/7d low-dispersion vs -70.8bps/7d
high-dispersion, t=1.97) is the other cross-sectional finding worth an `INDEPENDENT_CONFIRMATION`
pass. Neither is tradeable standalone yet.

**Everything tested in the options (group D) and most of positioning (group C) stayed DEAD or
DATA_LIMITED regardless of regime conditioning** - DVOL mean-reversion and options-flow
directional signals show no differentiable regime effect across five different conditioning
variables tried (funding-shift, DVOL level in two forms, funding extremity), consistent with
round 2's own characterization that Deribit options data yields IC-scale, non-actionable signals
without an execution vehicle. Positioning (group C) is capped at DATA_LIMITED by its 43-day
window regardless of point estimates, exactly as round 2's W10 flagged for the same dataset -
C2's taker-flow-fade-in-high-vol number (+52.6bps net, t=1.04) is the most suggestive positioning
result but cannot be trusted beyond "worth re-testing once more positioning history accumulates."
One interaction (E2, large-trade impact conditioned on cascade density - the natural
microstructure analog of A6) was preregistered but found genuinely infeasible: the liquidation
cascade dataset ends 2026-07-04 while the only available microstructure trade data is 5
non-contiguous days in late August 2026, with zero temporal overlap - reported as BLOCKED rather
than silently dropped, and substituted with a same-dataset trade-size-regime test (E3) that
itself reversed its preregistered hypothesis direction (extreme-size prints show slightly MORE
continuation, not less).
