# ALPHA ATLAS — every measured effect in this repository, ranked by distance to tradable (2026-09-15)

**Answer to « ai-je assez de data ? » : OUI.** ~904 mechanisms examined (lower bound), 9 sealed looks, 14 effects with a real gross signal. None is tradable today, and the reason is never the data: it is VIP0 taker cost (24–53 bps round trip at the windows that matter), per-event dispersion (740–1 936 bps), or a look budget of 0 that only new episodes can refill. No return was computed for this atlas; it reads verdicts already on disk.

## The 14 effects with a real gross signal

| # | effect | family | gross | cost RT | gross/cost | t (gross) | N / N_eff | verdict | binding | what flips it |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | event_listing_reversal_v1 (spot fade after perp-listing announcement) | news | 202 bps | 28 | 7.20× | 3.25 | 141 / 64 | INDECIDABLE (tradable_share 0.00) | EXECUTABILITY: a spot short needs borrow; the measured market is spot | borrow availability AT EVENT TIME (margin/allPairs + isMarginTrade recorded by the P4 tape at t0); if >= 50 % of new events are borrowable, seal a forward spot-short-via-margin variant. sigma ~740 bps (SE 62 at n 141) => n ~ 77 events for t 2.4 at +202 bps => ~15 months at ~5 spot-listed perp announcements/month. Family news is burned to 2026-09-10: forward only. |
| 2 | event_delisting_pressure_v1 (short after delisting announcement) | news | 631 bps | 20 | 32× | 2.44 | 56 / 22 | INDECIDABLE (N_eff 21.6 < 30, top-1 13 %) | CONCENTRATION + CAPACITY: mean carried by 5 micro-caps (DREP -7686 bps); 4+4 bps spread/slippage is not the cost of a 200 k$ short in a coin being wound down | the forward seal already running: 30 delisting events after 2026-09-11 with MEASURED cost (P4 microstructure captures). 14 Binance delistings/month => ~3 months. 6 h horizon (+1077, t 4.08) and 24 h (+1246, t 3.44) are stronger than 60 min but were sensitivities. |
| 3 | event_listing_perp_fade_v1 (short the new perp +15 min -> +6 h) | news | 252 bps | 24 | 10× | 2.12 | 174 / 93 | INDECIDABLE (t 2.12 < 2.326) | DISPERSION: sigma 1546 bps/event (prereg assumed 400); MDE 276 bps; two populations averaged (MEXC-first 113 vs true-first 6) | nothing historical (burned). Forward: MEXC_TO_BINANCE_V1 sealed (pre-announcement run-up conditioning, Spearman-permutation), 60 eligible ~ 10-14 months; expected verdict at declared power still INDECIDABLE unless the conditional fade >> 700 bps. |
| 4 | SWEEP_V4 lsr_globacct_x (crowd account L/S, cross-sectional contrarian, h3 k8) | crowd_positioning | 28 bps | 14 | 1.96× | 2.40 | 213 / 213 | INDECIDABLE (OOS t 2.40 < bar 2.955; Sharpe 1.66 arithmetic) | MULTIPLICITY + MOMENTUM OVERLAP: 88 sealed trials (bar 3.25 in the kernel); half the edge is 60-day momentum (allf-neutral t 3.00, net 15 bps/day); cost 14 assumed not measured; capacity 2-5 M$ | one sealed look on 2028-12-06 (FORWARD_CROWD_POSITIONING_V1). Nothing else: family burned 2020-09-01 -> 2026-09-10. |
| 5 | AMIHUD_ILLIQUIDITY_PREMIUM_V1 (weekly long illiquid / short liquid) | cross_sectional | 126 bps | 28 | 4.50× | 3.02 | 332 / 243 | VALIDATED_FOR_FORWARD (frozen 2026-09-02); 6/7 years, 2025 flat (-0.2) | CONFIRMABILITY: post-haircut Sharpe ~0.60 => ~900 weekly episodes (17 years) to reconfirm; forward labels show PnL = beta of a +10.9 % universe | none within a human horizon at Sharpe 0.6. It is an illiquidity premium: it pays only if the book can carry illiquid names (participation 0.38 % of ADV at 300 k$). |
| 6 | ENSEMBLE_A / ENSEMBLE_C4 (cascade-event composite + XS book) | liquidation x cross_sectional | 49 bps | 14 | 3.50× | 4.55 | 607 / 43 | UNCONFIRMABLE_IN_HORIZON (Sharpe 2.6-2.8 in-sample, ETA 4.1-4.6 y) | LATENCY: the cascade family reads a Vision backfill 45-48 h stale vs a 4 h horizon (100 % of forward decisions expire); in-sample Sharpe from 25 correlated signals (ENB 2.9) | re-express the cascade detector on the LIVE forced-flow tape (running since 2026-09-10, entry lag 2 s) and run it forward; the first-look on that tape found the 30 s continuation dead and the 5 min reversal +13.8 bps vs a 43 bps wall -- the 4 h horizon was not tested there. |
| 7 | M8_VOLSHOCK_REVERSION_6X (4 h reversal after a 6x hourly volume shock) | liquidation (cascade in disguise) | 41 bps | 14 | 2.90× | 8.92 | 2083 / 328 | UNCONFIRMABLE_IN_HORIZON (ETA 5.2-5.8 y); Sharpe 2.3-2.45, positive every year 2020-2026 | IDENTITY: 62 % of episodes within +-12 h of a cascade; the cascade-disjoint remainder is -17 bps => same edge as rank 6, same latency problem | same as rank 6 (live tape, forward). Capacity 4.7-14 M$. |
| 8 | forced_liquidation_exhaustion_v1 (5 min reversal after >= 250 k$ liquidation) | liquidation | 14 bps | 14 | 0.96× | 1.18 | 62 / 28 | REJECTED_COST_WALL (one session, 15 clusters) | COST WALL: right sign, one third of the 43 bps wall; wide-spread bucket +27 bps, >= 1 M$ bucket +20 | a maker entry (measured maker gain ~2 bps RT) does not close a 30 bps gap; only a larger reversal on more sessions would -- the live tape accrues them for free. |
| 9 | lsr_globacct_x_v2 (kernel, crowd L/S per account) | crowd_positioning | 9.60 bps | 16 | 0.60× | 2.04 | 848 / 848 | COST_WALL (needs 167 % capture) | COST WALL + POWER: MDE 15.3 bps at bar 3.25 vs 9.6 observed | cost tier: breakeven 9.6 bps RT => needs ~VIP5+ taker or maker-only fills; even then t 2.04 < 3.25 |
| 10 | FEAR_GREED_BUY_FEAR_M1 (long BTC in extreme fear) | sentiment | 64 bps | 14 | 4.60× | 2.25 | 94 / 94 | UNCONFIRMABLE_IN_HORIZON (ETA 20.8 y) | EPISODE RATE: ~11 fear episodes/year; 47.5 % explained by trailing price | none (structural) |
| 11 | SHORT_COVERING_CONTINUATION (OI-state overlay) | liquidation | 17 bps | 14 | 1.20× | 2.97 | 1582 / 1582 | NEEDS_MORE_RESEARCH (relative signal real; long product +2.5 bps) | PRODUCT: an overlay/filter, not a position | only as a sizing layer on an executable base -- which does not exist |
| 12 | PREMIUM_EXTREME_THEN_CASCADE | liquidation | 20 bps | 14 | 1.40× | 1.46 | 533 / 533 | NEEDS_MORE_RESEARCH (ETA 38 y) | DISPERSION + CONCENTRATION (episode +19.7 vs event +101.9) | none |
| 13 | HL_L2_IMBALANCE_T11 (Hyperliquid book imbalance, 5 min) | microstructure | 0.90 bps | 14 | 0.06× | 15 | 39 / 39 | DATA_LIMITED (best t and best ETA of round 4) | CAPACITY + COST: 9 k$ top-of-book, 14-16x under cost | maker-only market making on HL at rebate tiers = a different business |
| 14 | cross_exchange_dislocation_v1 / microstructure_imbalance_v1 (kernel) | cross_exchange / microstructure | 1.97 bps | 24 | 0.08× | 65 | 934 / 934 | COST_WALL (needs 1215 % / 1599 % capture) | COST WALL by an order of magnitude | none at any retail tier |

## What is actually left to do (three things; everything else is closed)

1. **Spot listing fade with borrow** (rank 1, the best t in the repository, gross 7× cost): the only reason it failed is that a spot short needs borrow. Record `isMarginTrade` for the asset at t0 in the P4 tape, re-query `/sapi/v1/margin/allPairs` in full (the stored 238-pair file has no USDT pair: inconclusive). If borrow exists for most new events, seal a forward spot-short-via-margin variant: σ ≈ 740 bps ⇒ ~77 events ⇒ ~15 months. Family `news` is burned: forward only.
2. **Delisting pressure forward** (rank 2): already sealed, 14 delistings/month ⇒ 30 events in ~3 months; the risk is capacity in micro-caps, which the P4 captures will measure.
3. **Cascade family on the live liquidation tape** (ranks 6–8): the in-sample Sharpe 2.3–2.8 composites are the same 4 h reversal after cascades; they died of a 45–48 h backfill lag. The forced-flow tape now delivers events 2 s after the exchange; the 4 h horizon has not been tested on it. ETA to confirm ≈ 4 years at the measured Sharpe.

Not on the list, by measurement: anything cross-sectional daily (best gross 8–10 bps short of a 3 bps break-even, or ETA > 200 years), anything options (ETA 10–84 years), anything microstructure or cross-exchange at retail cost (16× / 12× under the wall), sentiment (11 episodes/year).

## Cost reference (what would change the answer)

| item | value |
|---|---|
| VIP0 taker | 5 bps/side; measured round trip at 500 $: t0 52.9, +5 m 24.8, +15 m 23.7, +60 m 18.6; majors 8.6, wide alts 15.3 |
| maker gain | ≈ 2 bps round trip (measured on 166 k queue attempts); the 1.5× stress consumes it everywhere but BTC/ETH/BNB |
| tiers that flip ranks 9, 13, 14 | OKX VIP8 maker −0.25 bps; Binance VIP9 taker 1.7; HL rebate tier −0.3 — market-maker business, not this book |
| identity | confirmable in N years ⇔ Sharpe ≥ 5.60/√N |

## Dead or artefact (29, each with its named cause in the JSON)

- event_reaction_v1 H1 (pump inside the first minute)
- event_cross_venue_lag_v1 H4 (Binance moves before the venue's own timestamp)
- forced_liquidation_reaction_v1 (cascade over before the public message)
- CME_SEGMENTATION_V1 (t_net 1.58, closed)
- H-BASIS-1..4 (best t 2.49 < placebo median 2.81)
- SWEEP_V5 (no config clears Bonferroni OOS)
- XSEC_REV_1D_LS and 3 others (net-t artefact)
- W9_H2b low-wick reversion (random signal earns the same)
- LATE_TO_ASIA session reversal (bid-ask bounce)
- HL_TWAP_FLOW_IMBALANCE (PIT leak)
- HL_BINANCE_LEAD_LAG (sign inverted)
- M5_VRP_CROSS_ASSET (41 alts = 1 observation)
- M3 dealer gamma (no OI, wrong sign)
- M4 pin risk
- M7 options block flow (ETA 10-56 y)
- M6 DVOL divergence (confounded by realised vol)
- INSTRUMENT_AGE_FACTOR (null)
- LISTING_WAVE_REGIME (overlapping windows)
- NEWS_TIMING (coincident, not leading)
- EVENT_SCANNER_V1 4/4 KILL
- BTC_LEAD_ALT_CASCADE (deflated p 0.535)
- LIQ_CASCADE_FAR_FROM_LOW (sign depends on the weighting unit)
- OI_COLLAPSE_BOUNCE (adds nothing to the unconditional fade)
- CVD_SHOCK_DOWN_MEMORY (contrast significantly negative)
- XSEC_RESIDUAL_MOMENTUM_14D / HORIZON_EXTENSION / SECTOR_ROTATION / SECTOR_RS_REVERSAL (measured against zero, not the universe; ETA 215-397 y)
- CROSS_SECTIONAL_MOMENTUM_CVD (one date out of 333)
- BTC_ETH_CURVE_STEEPNESS (one year explains everything)
- HOURLY_XSEC_PORTFOLIO (8-10 bps short of a 3 bps break-even)
- HOURLY_RESIDUAL_REVERSION (edge scales with sqrt(h), cost does not)

## Claimed, never independently run (8)

- CROSS_ASSET_OI_BUILDUP_FADE
- BASIS_RICHENING_FADE
- BASIS_FUNDING_AGREEMENT_FADE
- FUNDING_CARRY_X_DISPERSION
- XSMOM_REGIME_META
- OPTIONS_IV_SHOCK_MEMORY
- SPILLOVER_X_DVOL_STRESS
- XSEC_RELATIVE_LEVERAGE_14D (OI panel never assembled)

## Method traps this repository has measured (25, listed in the JSON's sources)

Decluster chaining (rediscovered 4×), net-t artefact, calendar-day control fabricating edge, per-asset counting of a one-factor cross-section, PIT leaks in scheduled flow, universe drift, survivorship in `data/enriched`, placeholder columns, four cost conventions, sign ambiguities carried for months, multiple-comparison deflation (t 3.3 → 0.09 at 904 trials).

Sources: `reports/first_look/*`, `mechanisms/*/results/verdict.json`, `reports/research_kernel/SCOREBOARD.md`, `reports/loop/*`, `reports/edge_discovery/**` (rounds 1–4, sweeps v4/v5, validation wave 2), `reports/live_alpha_lab/SCOREBOARD.md`. No result file was re-read for a return; every number above is a published verdict figure.
