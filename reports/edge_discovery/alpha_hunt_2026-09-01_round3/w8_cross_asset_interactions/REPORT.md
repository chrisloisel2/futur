# W8 — Cross-Dataset / Cross-Asset Interactions (Alpha Hunt round 3)

Date of analysis: 2026-09-02. Read-only on all existing `data/`/`reports/`; nothing collected,
nothing modified in `src/institutional/` or `configs/live_alpha_registry.yaml`; Track A
(`Live Alpha Lab` frozen alphas) never touched, copied, or used as a template. Continues from
an interrupted prior run of this exact task (found in scratch: `tier1_liq_cascade.py`,
`tier2_spillover.py`) whose crude regime-conditioning experiments are reused unchanged below
(tier 1/2) rather than redone, and whose planned next step — re-testing regime-conditioning
against the actual round-2-corroborated repeat-cascade signal instead of a crude proxy — is
what tier 3 executes.

## 1. Methodology note

**PIT discipline.** All external regime series are lagged to the following UTC day
(`asof_date = date + 1d`) before being `merge_asof`'d (`direction="backward"`) onto event
timestamps, so a regime value is only usable the day after it was observable. The one exception
is `n_events_sym_24h`, an existing column in `data/events/liq_cascade_dataset.parquet` that is
already causal by construction (count of prior same-symbol events in the trailing 24h, as of
`event_time`) — reused rather than re-derived to avoid introducing a lookahead bug.

**Declustering.** Every PnL-style test reports both raw-N and independent-episode-N (24h
same-symbol gap rule, `decluster_mean` in `common.py`). Every probability-style test (tier 3/4)
additionally got a **day-level declustering re-check** on its raw results, because this
project's round-2 sweep found (four independent times) that daily/regime-level clustering
inflates apparent significance in exactly this kind of cross-sectional test — a concern that
applies with extra force here since a single volatile *day* can put many different symbols'
"fresh" cascades in the same regime bucket with correlated repeat outcomes. That check mattered:
see §3.

**Costs.** 14bps round-trip (5bps taker + 2bps slippage, each way) throughout, for every PnL-style
test.

**Base signals — deliberately crude, not a Track-A replication.** Two "own construction" base
signals are used, matching the interrupted run's discipline:
- *cascade continuation*: `direction = sign(px_ret_30m)`, `pnl = direction * fwd_4h`, on ALL
  38,141 rows of `liq_cascade_dataset.parquet` (no oi_drop_z/vol threshold filtering — those
  thresholds belong to the frozen A7-TAIL-E1 candidate and are deliberately not replicated here).
- *spillover continuation*: `direction = sign(btc_ret_1h_at)`, `pnl = direction * fwd_4h`, on
  `spillover_dataset.parquet`.

**A negative methodological finding worth stating up front**: neither crude base signal, taken
over the FULL event population, reproduces round 2's corroborated "cascades only pay on repeat"
pattern (W2+W9: first-hit ~flat/-6 to -19bps, repeat >=+27 to +87bps). This own construction's
occurrence-bucket split instead shows first=-13.4bps, repeat(>=1)=-21.3bps, serial(>=2)=-24.4bps —
i.e. *worse*, not better, on repeat. This is almost certainly because A7-TAIL-E1's edge lives in
a *threshold-filtered tail* (extreme `oi_drop_z`/`vol_24h`, fit on 2021-2024), not in the average
of all 38k events — reproducing that threshold exactly would mean copying Track A's own spec,
which this worker will not do. Practical consequence: **tier 1 and tier 2 (crude-signal PnL
conditioning) tell us about regime effects on a weak/noisy proxy, not about the real repeat-cascade
edge** — kept for completeness and because they're still six/two genuinely distinct interaction
ideas with honest negative results, but downgraded accordingly. **Tier 3's probability-based test
(Q1) sidesteps this problem entirely** — it asks a question (does a fresh cascade turn into a
repeat within 24h?) that needs no assumption about trade direction or Track-A thresholds at all,
which is why it is the more trustworthy half of tier 3.

## 2. Interactions, economic story first

### Tier 1 — six external regimes x crude cascade continuation (own base signal)
*Not new vs. W9's list* (W9 conditioned OI/funding/book-depth/HL-wallet/options-IV/options-flow/
basis-OI/basis-funding/dispersion/A4-vol-regime) — these six regime variables
(stablecoin flow, cross-venue funding divergence, quarterly-futures basis, sentiment, DVOL,
options skew) were **not** tried by W9 or W10 on any liquidation-cascade mechanism.

- **W8-1 stablecoin supply flow**: fresh USD-stablecoin issuance funds new leveraged positions —
  hypothesis: cascades hitting during supply *expansion* (fresh capital available to re-lever/chase)
  continue harder than during contraction. **Result: DEAD.** Both regimes net -5.6/-16.2bps
  (marginal -17.5bps), decl_t -0.77/-2.47, no consistent year pattern.
- **W8-2 cross-venue funding divergence (Binance vs Bybit)**: large divergence signals leverage
  fragmented across venues (harder to arb away, "stickier" crowding) — hypothesis: continuation
  stronger when divergence is high. **Result: DEAD.** -11.0 vs -15.0bps net, decl_t -0.94/-2.04.
- **W8-3 quarterly-futures term basis (structural carry leverage)**: a rich calendar basis reflects
  institutional carry positioning stacked on top of perp leverage — hypothesis: richer basis =
  more total leverage = stronger cascade continuation. **Result: DEAD**, both directions strongly
  negative (-26.4/-25.9bps net, decl_t -6.65/-4.30) — if anything the opposite of expected.
- **W8-4 fear/greed sentiment**: extreme fear = genuine panic/capitulation dynamics —
  hypothesis: continuation strongest in extreme fear. **Result: DEAD.** All three buckets
  net-negative (-19.3/-12.6/-26.0bps), no monotonic pattern.
- **W8-5 options DVOL (crypto-wide implied stress)**: a market-wide options-priced stress regime
  should amplify any symbol's cascade continuation. **Result: DEAD.** High-DVOL -24.4bps
  (decl_t -5.11) is *worse* than low-DVOL -16.6bps — inverse of the naive hypothesis, and both
  net negative regardless.
- **W8-6 options 25-delta skew**: put-heavy (crash-priced) skew = the market already positioned
  for downside continuation. **Result: DEAD.** Put-heavy -28.6bps net vs complacent -12.2bps —
  again inverse of the naive hypothesis and both negative.

All six: **DEAD** on this crude base signal (see S1 caveat on what that does/doesn't tell us).
Full table: `evidence/tier1_liq_cascade_regimes.csv`.

### Tier 2 — two external regimes x crude spillover continuation (own base signal)
- **W8-7 spillover x sentiment**: a BTC shock during panic should drag correlated alt-selling
  harder (contagion) than during greed. **Result: DEAD.** Fear -26.6bps, greed -27.1bps, both
  *worse* than the (already-marginal, +2.61 t-stat but small) unconditional -5.1bps.
- **W8-8 spillover x stablecoin flow**: fresh stablecoin capital should be available to absorb/
  extend a BTC-shock-driven alt move. **Result: DEAD/mixed.** Expansion -10.8bps (n=1,353,
  decl_t +0.94, weak positive lean but n.s.), contraction -19.1bps (decl_t -0.25). No real signal.

Full table: `evidence/tier2_spillover_regimes.csv`.

### Tier 3 — six external regimes x the ACTUAL repeat/serial cascade structure (not a proxy)
This is the interrupted run's planned next step, executed properly using
`n_events_sym_24h` (an existing PIT column: count of prior same-symbol cascades in the trailing
24h). Two genuinely different economic questions per regime variable:

**(Q1) Repeat-probability**: for a *fresh* cascade (no prior same-symbol event in the last 24h),
does the external regime value at that moment predict whether the SAME symbol has ANOTHER
cascade within the next 24h? This operationalizes the brief's "does X predict which cascades
will be repeat vs one-and-done," generalized across six regimes.

**(Q2) Edge amplification**: within the repeat bucket (`n_events_sym_24h>=1`), does the regime
amplify the crude continuation signal's PnL? (Inherits the S1 caveat — the base signal doesn't
show a real repeat-edge to amplify, so Q2 results are reported but flagged **not
interpretable** as evidence about the real A7-TAIL-E1-style edge; kept only as an honest record
of what was tried.)

Results (Q1, raw event-level, then day-declustered where the raw result was non-trivial):

| regime | economic story | raw repeat-rate hi vs lo | raw z / p | day-declustered t / p | verdict |
|---|---|---|---|---|---|
| stablecoin flow | fresh capital -> more likely to re-lever into a 2nd cascade | 0.476 vs 0.487 (n~4.6k ea) | z=-1.08, p=0.28 | not tested (n.s. already) | **DEAD** |
| cross-venue funding div. | fragmented leverage harder to arb -> more likely to repeat | 0.532 vs 0.477 (n~870 ea) | z=2.28, p=0.022 | t=1.59, **p=0.11** | **DEAD (clustering artifact)** |
| quarterly basis | rich structural carry -> more total leverage -> more repeats | 0.497 vs 0.468 (n~4.6k ea) | z=2.74, p=0.006 | t=0.47, **p=0.64** | **DEAD (clustering artifact)** |
| sentiment (fear/greed) | extreme fear = ongoing panic -> more repeats | 0.503 vs 0.463 (n~4.7k ea) | z=3.87, **p=0.0001** | t=1.65, **p=0.10** | **DEAD (clustering artifact)** |
| DVOL stress | crypto-wide implied stress -> more repeats | 0.476 vs 0.497 (n~4.6k ea, inverse) | z=-2.01, p=0.045 | t=-1.06, **p=0.29** | **DEAD (clustering artifact)** |
| **options skew** | put-heavy (crash-priced) skew = market already positioned for more downside -> fresh cascades more likely to cascade again | **0.438 vs 0.510** (n~3.6k ea) | z=-6.14, **p<0.0001** | **t=-3.76, p=0.0002** | **PROMISING — survives declustering** |

**The skew finding is the standout of this entire worker's output.** Fresh liquidation cascades
occurring when BTC options skew is put-heavy (crash protection already priced/positioned, bottom
quartile of a 180d rolling percentile) have a materially higher chance of turning into a repeat
cascade within 24h (43.8% raw / 38.3% at complacent-skew day-level vs 51.0%/45.6% at put-heavy
day-level — direction: **put-heavy skew -> higher repeat rate**), and this survives collapsing to
one observation per calendar day (t=-3.76, p=0.0002), which is the exact check that killed the
other five regimes. Economic story that fits: put-heavy skew reflects the options market already
pricing/positioning for further downside — when that's true, a fresh deleveraging event is more
likely one wave in an ongoing capitulation than an isolated shock. This is a genuinely novel,
cross-dataset, declustering-robust finding not reported by W9 or W10 (W9's only skew-adjacent
result was DVOL-funding->RV, not a repeat-cascade interaction).

Q2 (edge amplification, base signal caveat applies — not shown as a finding, only logged):
all 12 rows net-negative regardless of regime bucket (range -8.9 to -33.4bps), consistent with
tier 1's observation that this crude base signal simply doesn't carry a positive repeat-edge to
amplify. `evidence/tier3_q2_edge_amplification.csv` kept for completeness/audit only.

Full evidence: `evidence/tier3_q1_repeat_probability.csv`, `evidence/tier3_q2_edge_amplification.csv`.

### Tier 4 — whale positioning & cross-venue (Hyperliquid) x FRESH cascade repeat-probability
The frozen `liq_cascade_dataset.parquet` ends 2026-07-04 with **zero calendar overlap** with
`data/positioning` (starts 2026-07-16) or `data/hyperliquid` (starts 2026-07-18) — these
interactions are impossible on the existing frozen dataset. Built a fresh liquidation event set
from the real-time OKX `force_order` feed (`data/derivatives_raw`, read-only), 2026-07-16 to
2026-08-30, 9 symbols, within-symbol p95 1-minute notional threshold, 15-minute consolidation ->
748 events, 105 "fresh" (no prior same-symbol event in trailing 24h), base repeat-rate 61.9%.

- **W8-15a whale (top_position) LSR extremity**: an already-stretched whale long/short ratio
  (|7d rolling z-score| top quartile) reflects crowded, fragile positioning that should predict
  a higher repeat rate — the brief's flagship suggested combination. **Result: NULL.**
  Extreme=0.520 (n=25) vs near-neutral=0.640 (n=25), z=-0.86, p=0.39 — wrong-signed if anything,
  not significant, small N.
- **W8-15b whale-lean agrees vs. disagrees with cascade direction**: a whale positioned in the
  SAME direction the forced flow is already pushing = more fuel for another leg; positioned
  against it = absorption capacity. **Result: NULL.** Agree=0.583 (n=36) vs disagree=0.623
  (n=61), z=-0.39, p=0.70.
- **W8-16a Hyperliquid cross-venue OI acceleration**: if HL (a separate order-flow pool) already
  shows an anomalous same-coin OI move in the hour before a Binance/OKX-detected cascade, that
  would be genuine cross-venue informational leadership. **Result: NULL.** Large HL OI move
  pre-event=0.640 (n=25) vs quiet=0.560 (n=25), z=0.58, p=0.56.

**All three: DATA_LIMITED (not DEAD)** — directionally noisy, N per quartile is only 25, and the
45-day window is the single choppy-to-bull regime W10 already flagged for `data/positioning`
generally. Consistent with W10's finding that most `data/positioning` interactions evaporate;
this extends that conclusion to a genuinely new pairing (whale positioning x cascade repeat-ness,
and HL x cross-venue leadership) that W9/W10 never tested, with the same negative outcome.

Full evidence: `evidence/tier4_fresh_positioning_hl.csv`.

### Tier 5 — BTC-shock alt-spillover x options DVOL stress regime
Neither W9 (single-symbol mechanisms only) nor tier 2 above (sentiment, stablecoin flow) tested
DVOL as a regime for the multi-asset spillover dataset. Economic story: does a crypto-wide
options-implied stress regime make a BTC shock's alt-spillover more likely to continue
(correlated panic-selling) or revert (already-arbed, noisy)?

**Result: real interaction, inverse of the naive hypothesis, with a recency caveat.** Low-DVOL
(calm) regime: declustered net **+7.8bps**, PF 1.32, t=**3.36**, n=1,219 independent episodes,
positive 4 of 6 years (2021 +45.0, 2022 +6.5, 2023 +50.8, 2024 +14.9) but negative both 2025
(-45.4) and 2026 (-37.3). High-DVOL (stress) regime: declustered net **-24.5bps**, t=-1.84 —
clearly worse than calm, not better, contradicting "stress amplifies contagion." Economic
read: in calm regimes a BTC shock's alt-reaction is more likely a clean leadership signal that
plays out; in high-DVOL/stress regimes everything is already moving together and/or gets faded
faster. **Verdict: WEAK/PROMISING-WITH-CAVEAT** — real, sign-stable direction (low-DVOL always
beats high-DVOL) but decaying in the same 2025-26 window where round 2 already flagged
funding/basis-family effects as arbitraged away; not tradeable as-is given the recent-year sign
flip.

Full evidence: `evidence/tier5_spillover_dvol.csv`.

## 3. Results table

| candidate_id | family | economic_risk_factor | mechanism | datasets_combined | N_raw | N_independent | gross_bps | net_bps | PF | stability | distinctness | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W8-1a/b | stablecoin_flow_x_liq_continuation | leverage_unwind_liquidity | stablecoin supply regime x crude cascade continuation | stablecoins/supply_daily + events/liq_cascade_dataset | 9,535/9,547 | 4,816/4,832 | 8.4/-2.2 | -5.6/-16.2 | 1.08/0.98 | no year pattern | new (W9/W10 never tried) | DEAD |
| W8-2a/b | cross_venue_funding_divergence_x_liq | fragmented_leverage | Binance-Bybit funding div. x crude cascade continuation | derivatives_backfill/{binance,bybit}/funding + events/liq_cascade_dataset | 1,807/1,807 | 1,139/1,111 | 3.0/-1.0 | -11.0/-15.0 | 1.04/0.99 | unstable | new | DEAD |
| W8-3a/b | quarterly_basis_regime_x_liq | structural_carry_leverage | quarterly-futures basis level x crude cascade continuation | derivatives_backfill/binance_vision_quarterly + um_klines_1d + events/liq_cascade_dataset | 9,533/9,524 | 4,544/4,887 | -12.4/-11.9 | -26.4/-25.9 | 0.89/0.87 | consistently negative | new | DEAD |
| W8-4a/b/c | sentiment_regime_x_liq_continuation | panic_capitulation | fear/greed x crude cascade continuation | news_backfill/fear_greed + events/liq_cascade_dataset | 8,239/9,450/3,814 | 4,336/5,112/1,729 | -5.3/1.4/-12.0 | -19.3/-12.6/-26.0 | 0.95/1.02/0.91 | no monotonic pattern | new | DEAD |
| W8-5a/b | options_stress_regime_x_liq_continuation | implied_vol_stress | DVOL x crude cascade continuation | options_backfill/deribit/DVOL_BTC + events/liq_cascade_dataset | 9,481/16,422 | 4,614/8,209 | -10.4/-2.6 | -24.4/-16.6 | 0.91/0.97 | hi-DVOL worse (inverse) | new | DEAD |
| W8-6a/b | options_skew_regime_x_liq_continuation | crash_hedge_positioning | 25d skew x crude cascade continuation | options_backfill/deribit/features/BTC_daily + events/liq_cascade_dataset | 5,755/8,826 | 3,118/4,922 | -14.6/1.8 | -28.6/-12.2 | 0.85/1.02 | put-heavy worse (inverse) | new | DEAD |
| W8-7a/b | spillover_x_sentiment_regime | panic_contagion | fear/greed x crude spillover continuation | events/spillover_dataset + news_backfill/fear_greed | 2,408/1,598 | 1,576/874 | -12.6/-13.1 | -26.6/-27.1 | 0.88/0.90 | both worse than marginal | new | DEAD |
| W8-8a/b | spillover_x_stablecoin_flow | fresh_capital_absorption | stablecoin flow x crude spillover continuation | events/spillover_dataset + stablecoins/supply_daily | 1,969/1,967 | 1,353/1,205 | 3.2/-5.1 | -10.8/-19.1 | 1.03/0.96 | weak, n.s. | new | DEAD |
| W8-9a | stablecoin_flow_x_repeat_probability | leverage_unwind_liquidity | stablecoin flow at a fresh cascade -> P(repeat<=24h) | events/liq_cascade_dataset(+n_events_sym_24h) + stablecoins/supply_daily | n_hi=4,610/n_lo=4,592 | (proportions test) | -- | -- | -- | z=-1.08 p=0.28 | new | DEAD |
| W8-10a | cross_venue_funding_div_x_repeat_probability | fragmented_leverage | funding div. at a fresh cascade -> P(repeat<=24h) | events/liq_cascade_dataset(+n_events_sym_24h) + derivatives_backfill funding | n_hi=853/n_lo=888 | day-decl. n~525/457 | -- | -- | -- | raw z=2.28 p=0.02 -> day t=1.59 **p=0.11** | new | **DEAD (clustering artifact)** |
| W8-11a | quarterly_basis_x_repeat_probability | structural_carry_leverage | basis at a fresh cascade -> P(repeat<=24h) | events/liq_cascade_dataset(+n_events_sym_24h) + binance_vision_quarterly | n_hi=4,594/n_lo=4,595 | day-decl. n~417/458 | -- | -- | -- | raw z=2.74 p=0.006 -> day t=0.47 **p=0.64** | new | **DEAD (clustering artifact)** |
| W8-12a | sentiment_x_repeat_probability | panic_capitulation | fear/greed at a fresh cascade -> P(repeat<=24h) | events/liq_cascade_dataset(+n_events_sym_24h) + news_backfill/fear_greed | n_hi=4,709/n_lo=4,670 | day-decl. n~409/462 | -- | -- | -- | raw z=3.87 p=0.0001 -> day t=1.65 **p=0.10** | new | **DEAD (clustering artifact)** |
| W8-13a | dvol_stress_x_repeat_probability | implied_vol_stress | DVOL at a fresh cascade -> P(repeat<=24h) | events/liq_cascade_dataset(+n_events_sym_24h) + options_backfill DVOL | n_hi=4,593/n_lo=4,721 | day-decl. n~391/412 | -- | -- | -- | raw z=-2.01 p=0.045 -> day t=-1.06 **p=0.29** | new | **DEAD (clustering artifact)** |
| **W8-14a** | **options_skew_x_repeat_probability** | **crash_hedge_positioning** | **25d skew at a fresh cascade -> P(repeat<=24h)** | events/liq_cascade_dataset(+n_events_sym_24h) + options_backfill/deribit/features | n_hi=3,623/n_lo=3,599 | day-decl. n~304/271 | -- | -- | -- | raw z=-6.14 p<0.0001 -> **day t=-3.76, p=0.0002** | new, distinct from W9's DVOL-RV skew usage | **PROMISING** |
| W8-9b/c..14b/c | *_x_repeat_edge_amplification (6 vars) | (as above) | regime x PnL within repeat bucket (base-signal caveat, S1) | events/liq_cascade_dataset(+n_events_sym_24h) + 6 regime sources | ~950-5,100 each | not declustered (see caveat) | -1.6 to -19.4 | -8.9 to -33.4 | 0.81-1.05 | all negative regardless of bucket | new but not interpretable | **DATA_LIMITED / not interpretable** (base signal has no positive repeat-edge to amplify) |
| W8-15a | whale_positioning_x_repeat_probability | crowded_leverage_fragility | whale LSR extremity -> P(repeat<=24h), fresh OKX cascades | derivatives_raw/okx force_order(fresh) + positioning/top_position | n_hi=25/n_lo=25 | -- | -- | -- | -- | z=-0.86 p=0.39 | new (brief's flagship idea) | DATA_LIMITED |
| W8-15b | whale_positioning_x_repeat_probability | crowded_leverage_fragility | whale-lean agrees/disagrees w/ cascade dir. -> P(repeat<=24h) | derivatives_raw/okx force_order(fresh) + positioning/top_position | n=36/61 | -- | -- | -- | -- | z=-0.39 p=0.70 | new | DATA_LIMITED |
| W8-16a | hl_crossvenue_oi_x_repeat_probability | cross_venue_leverage_contagion | HL OI move pre-event -> P(repeat<=24h) | derivatives_raw/okx force_order(fresh) + hyperliquid/ctxs | n_hi=25/n_lo=25 | -- | -- | -- | -- | z=0.58 p=0.56 | new | DATA_LIMITED |
| **W8-17a/b** | **spillover_x_dvol_stress_regime** | **correlated_stress_contagion** | **DVOL x spillover continuation** | events/spillover_dataset + options_backfill/deribit/DVOL_BTC | 3,295/1,533 | 2,060/1,219 | -3.5/**23.0** | -17.5/**+9.0** | 0.97/**1.32** | low-DVOL: t=**3.36**, +4/6 yrs, but 2025-26 negative | new (neither W9 nor tier2 tried DVOL x spillover) | **WEAK/PROMISING-WITH-CAVEAT** |

## 4. TOTAL_MECHANISMS_TESTED

**43 individual parameterized tests** (CSV rows: tier1=14, tier2=5, tier3-Q1=6, tier3-Q2=12,
tier4=3, tier5=3), spanning **17 distinct mechanism families** (6 tier-1 + 2 tier-2 + 6 tier-3 +
2 tier-4 + 1 tier-5; tier-3's Q1/Q2 per regime variable counted as one family with two
sub-mechanisms, not two, per the anti-inflation instruction), plus **4 day-level declustering
robustness re-checks** run on tier 3's most significant raw results (the four that flipped to
non-significant, and the one -- options skew -- that survived).

## 5. Top findings (prose)

**The one real, declustering-robust finding: BTC options skew predicts liquidation-cascade
repeat probability, not just realized vol (W9's use).** When BTC's 25-delta skew is put-heavy
(bottom quartile of a 180-day rolling percentile -- crash protection already priced/positioned),
a fresh liquidation cascade on ANY of the 49-symbol universe is significantly more likely to be
followed by another same-symbol cascade within 24h than when skew is complacent (top quartile):
raw event-level 43.8% vs 51.0% (z=-6.14, p<0.0001), and -- critically, since this is exactly the
kind of result the project's round-2 sweep flagged as usually a clustering artifact -- it survives
collapsing to one observation per calendar day (t=-3.76, p=0.0002). Five other regime variables
tested the identical way (stablecoin flow, cross-venue funding divergence, quarterly basis,
sentiment, DVOL) either showed no raw effect or looked significant raw and evaporated under the
same day-level check, exactly reproducing round 2's four-worker "declustering trap" pattern on a
fifth and sixth dataset. This skew->repeat-probability link is a genuinely new, economically
motivated (the options market already pricing further downside = an ongoing, not isolated,
deleveraging event), cross-dataset finding not reported by W9 (whose only skew-adjacent
interaction was DVOL/funding->realized-vol) or W10. No execution vehicle exists to trade it
directly, but as a **filter/sizing input onto any repeat-cascade mechanism** (e.g. informing
which "fresh" cascades are worth watching for a follow-through leg) it is worth carrying forward.

**Second finding, weaker: BTC-shock alt-spillover continuation only survives in calm (low-DVOL)
regimes, not stressed ones -- the opposite of the naive "stress = more contagion" story.**
Declustered net +7.8bps (PF 1.32, t=3.36) when DVOL is calm vs -24.5bps (t=-1.84) when DVOL is
stressed. Sign-stable across the whole comparison but decaying in the same 2025-26 window
already flagged project-wide as where funding/basis-family effects get arbitraged away -- treated
as WEAK/PROMISING-WITH-CAVEAT, not actionable as-is.

**Everything else is a clean negative result, and that negative result is itself informative.**
Eight regime variables tried against two crude own-construction continuation signals (cascade
and spillover) -- all DEAD. The crude cascade-continuation base signal notably fails to reproduce
round 2's corroborated repeat-cascade edge at all (repeat bucket -21.3bps vs first-hit -13.4bps,
backwards from W2/W9's finding), confirming that edge lives in A7-TAIL-E1's specific
threshold-filtered tail rather than in the raw event population -- a boundary this worker
deliberately did not cross to stay clear of Track A. The brief's flagship suggested idea (whale
positioning predicting repeat-vs-one-off cascades) and a genuinely new cross-venue idea
(Hyperliquid OI leading a Binance/OKX cascade) were both actually tested, for the first time,
on a freshly built OKX-force-order event set spanning the one 45-day window where
`data/positioning` and `data/hyperliquid` overlap with real-time liquidation data -- both came
back null, consistent with (and extending) W10's general finding that `data/positioning`
interactions mostly evaporate, on a pairing W10 never tried.
