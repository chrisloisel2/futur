# Alpha Hunt 2026-08-30 — aggregate scoreboard (round 2)

Ten parallel workers (W1-W10), read-only on all existing data/reports, no new data collection,
no sealed experiments touched (A2, A2-RV-v1, A13-H), TRM Fleet untouched. Scope: mine
data ALREADY on disk for genuinely different economic mechanisms, per explicit user directive
that a dataset is not "dead" because one prior mechanism/feature/strategy expression on it
failed. ~146 mechanisms/interactions tried across all ten workers combined. Per-worker detail
in `w*/REPORT.md`; round 1 was `reports/edge_discovery/alpha_hunt_2026-08-29/`.

## Headline finding — cross-corroborated independently by two workers

**Liquidation cascades only pay on repeat, not on the first hit.** W2 (working the raw
`liq_cascade_dataset`) and W9 (working a cross-dataset interaction of A7-TAIL-E1 against its own
repeat-event count) arrived at the *same* structural finding through independent methodologies:

| | W2's framing | W9's framing |
|---|---|---|
| 1st occurrence in a symbol (24h window) | net14 **-10.6bps full / -18.8bps OOS** ("onset") | n=1,509, net **-6.2bps** (no edge) |
| 3rd+ / repeat ≥1 | net14 **+27.1bps full / +45.2bps OOS** ("exhaustion") | n=2,290, net **+42.5bps** |
| Serial ≥2 | — | n=1,155, net **+86.6bps** |
| (for reference) A7-TAIL-E1 blended | — | +23.1bps marginal |

This means **A7-TAIL-E1's existing +23.1bps net is a blend that hides a much stronger signal in
the repeat-cascade tail and a near-zero/negative one on first occurrences** — this is a
refinement of the project's one already-PROMISING candidate, not a new independent mechanism
(same underlying detector, same risk factor), but it is the single most actionable output of
this entire round: splitting the existing engine by same-symbol repeat-count within 24h should
be evaluated before any further A7-TAIL-E1 work. Distinctness from A7's own tail bucket verified
by W2 (Jaccard overlap = 0.116, low).

## Ranked table — genuinely new candidates (PROMISING or better)

| rank | mechanism | worker | dataset | horizon | N | net bps | stability | status |
|---|---|---|---|---|---|---|---|---|
| 1 | Liquidation cascade repeat-event split (see above) | W2 + W9 | liq_cascade_dataset | 4h | 1,140-2,290 | +27 to +115 (regime-dependent) | corroborated 2 ways, improving by year, only 2022 weak | **PROMISING** |
| 2 | Short-covering continuation (price↑ + OI↓ vs baseline) | W2 | event_feature_panel (frozen-50) | 4h | 23,422/7,217 | excess +9.2 full / **+19.0 OOS**, t=5.5/4.7 | strengthens OOS, best majors/capacity coverage of anything new | **PROMISING** |
| 3 | "Far from local low" liquidation reversion | W2 | liq_cascade_dataset | 4h | ~6.7k/2.8k | +15.5 to **+73.3 OOS** | stable, both variants agree | **PROMISING** |
| 4 | Cross-sectional momentum (7d→7d, long-only, liquid names) | W1 | data_v2/normalized (312-sym PIT) | 7d | full sample | **+89** (t=2.60, p=0.01) | positive 6/7 years | **PROMISING, NEEDS_FULL_VALIDATION** — first real cross-sectional finding in this project's edge-discovery phase |
| 5 | Funding-vs-quarterly-basis disagreement | W4 | derivatives_backfill BTC/ETH curve | 14-30d | 15-24 (thin) | +7.7 to +33.2 @14bps / breakeven to +19.2 @28bps stress | positive every year 2024-2026, not a repackaging of basis level (corr 0.49-0.52) | **PROMISING** — worst episode -111.8bps (BTC)/-106.6bps (ETH), thin-N |
| 6 | SHORT_SQUEEZE exhaustion (liquidation) | W2 | liq_cascade_dataset | 4h | 1,140/350 | +40.0 full / **+114.6 OOS** | unstable by year (2022 -50.8, 2025 +145.5 outlier) | **PROMISING-WITH-CAVEAT** — inherited unresolved sign-convention question from round 1 |
| 7 | RV/IV spread mean-reversion (options) | W6 | options_backfill/deribit | daily | n=1,291 | partial IC **-0.39** | stronger survival than A14's own +0.22 bar | **PROMISING, no execution vehicle** — route toward VRP overlay alongside A14 |
| 8 | Far-OTM put share → forward RV (crash-hedge demand proxy) | W6 | options_backfill/deribit | daily | — | partial IC **+0.16** | sign-stable across splits (+0.22/+0.22) | **PROMISING, no execution vehicle** |
| 9 | Hourly block-trade count → RV, 4h/24h | W6 | options_backfill/deribit | 4h/24h | n=31,050 hours | partial IC +0.10 | extends A14's hourly block signal | **PROMISING but modest, no execution vehicle** |
| 10 | Queue depletion/absorption, binance+OKX (A3 unblocked) | W7 | market_physics_v3/raw, post-crossed-book-fix | 1s | n=2,100-66,100/venue/side | 0.28-2bps gross (absorption sign) | far more statistically solid than round 1's Bybit-only result | **NEEDS_FULL_VALIDATION**, still sub-taker-cost |
| 11 | Directional liquidity-shock propagation (A6 revival) | W7 | market_physics_v3/raw | varies | — | 0.013-0.075bps, correctly signed 16/16 combos | binance/OKX | **WEAK-but-real** (was DEAD in round 1's undirected design) |
| 12 | Depth-imbalance / microprice-offset / OFI cluster | W7 | market_physics_v3/raw | 5s | — | 0.7-1.63bps gross | binance/OKX/hyperliquid | **NEEDS_FULL_VALIDATION** — first microstructure signal in either round approaching maker-leg cost (~1.5bps) |

## Thin / regime-confounded — real numbers, low confidence

| mechanism | worker | net bps | caveat |
|---|---|---|---|
| Positioning taker-flow extremes | W2 | excess +27.7/-41.0, t up to 16.3 | single 2-week regime only |
| Whale (top-position) LSR extreme-long → underperformance | W10 | -57.8bps, n=87, p=0.006 | SHORT-shaped (screen-only use), sign-stable both halves but thin |
| HL wallet "fade the sell" timing-skill | W5 | net +5.55/+0.55bps @4-9bps cost | entire 16-day test window was one uninterrupted rally (all 12 coins +9.5% to +44%); excess over baseline shrinks to +6.42bps |
| Basis-funding-agreement fade (interaction) | W9 | +18.5 to +20.6bps/1d when basis+funding agree | vs -1.3 to -1.7bps (wrong-signed) when they disagree — real conditioning effect, needs full validation |
| Options IV-shock × funding extremity | W9 | IC 0.438 (funding-extreme) vs 0.338 marginal | still no options execution vehicle |

## Cross-cutting methodology findings (as important as any single mechanism)

1. **Declustering discipline burned four independent workers this round** (W1, W4, W9, W10),
   each rediscovering — on four different datasets — that raw overlapping/autocorrelated
   observations produce wildly inflated significance. Notable casualties: W10's best-looking raw
   number (LSR momentum +134bps, p<0.0001) evaporated to non-significant once declustered; W9's
   basis×OI interaction vanished entirely the same way; W1 had to redo five regime-conditioning
   tests because they were being compared to zero instead of to each other, given this dataset's
   strong unconditional upward drift. **This should become a standing checklist item for any new
   backtest on this project**, not just calendar basis (where it was first documented in round 1).
2. **Single-regime confound**: several datasets are young enough (6 weeks of positioning, 16 days
   of the HL trades window used, 45 days of execution probe) that they're dominated by one bull
   run — W3, W5, W10 all flag this explicitly as a reason to discount otherwise-clean-looking
   numbers pending a second regime.
3. **Funding/basis mean-reversion has been arbitraged away**: W3 tested ~7 independent
   constructions (cross-sectional basis ranking, cross-sectional funding dispersion, vol-regime
   fade, convergence-speed fade, funding-surprise fade, multi-settlement drift) — all show large
   significant edges 2020-2024 collapsing or reversing in 2025-2026. Consistent pattern, not
   noise. SHORT_REJECTED independently re-confirmed on its own terms (funding-harvest short nets
   -68.87bps, t=-11.5).
4. **Two of round 1's own claims got downgraded on closer inspection**: W7 found A4 (refill
   asymmetry, round 1's "cleanest result") now flips sign across horizons on 6/12 combos, and A5
   (toxic flow) breaks sign-stability for binance/OKX BTC in one sub-period.
5. **Execution probe (A16-adjacent) is DEAD as a standalone maker edge** (W8): every state
   conditioning tried still nets negative (-2.68bps best case vs -3.65bps baseline), including a
   caught-and-corrected confound (fast fills looked toxic, vanished once symbol mix was
   controlled for). Useful as cost/sizing context for other mechanisms, not as its own sleeve.
6. Two harness-infra notes for future multi-agent sweeps: (a) several workers hit a tool
   restriction blocking direct `Write` of `REPORT.md`-named files and had to route through Bash
   or return content as text for the coordinator to persist (W2, W10 needed coordinator
   transcription; W1/W3/W4/W5/W6/W7/W8/W9 wrote their own successfully, apparently via Bash
   workaround or not hitting the restriction) — no data was lost, just a friction point. (b) W3
   found the shared `/tmp` scratchpad is common across all parallel workers in a round — two of
   its intermediate script files got silently overwritten by another worker before it isolated
   its scratch work into a dedicated subfolder; no report/repo impact, but future multi-worker
   sweeps should have each worker use its own scratch subdirectory from the start.

## Bottom line

No mechanism from this round is ready for `INDEPENDENT_CONFIRMATION` on its own yet. The
standout actionable item is **not a new mechanism but a refinement**: A7-TAIL-E1 should be
re-evaluated split by same-symbol repeat-cascade count, since two independent workers found the
blended average hides a near-zero first-hit and a much stronger repeat-hit edge. Behind that,
five genuinely new PROMISING candidates emerged with real numbers and no fabrication (short-
covering continuation, "far from local low", cross-sectional momentum, funding-vs-quarterly-basis
disagreement, and the three options-RV mechanisms feeding the existing VRP overlay) — all still
need `INDEPENDENT_CONFIRMATION` on unseen data before anything moves further, per this project's
standing anti-p-hacking discipline. Microstructure produced its first-ever signals approaching
maker-cost economics (W7's OFI/microprice cluster) after fixing the crossed-book bug, still not
confirmed tradeable. The single biggest force multiplier from this round, arguably bigger than
any one mechanism, is the fourfold-independent rediscovery of the declustering trap — worth
writing into a shared checklist so it stops costing a full research cycle per dataset.
