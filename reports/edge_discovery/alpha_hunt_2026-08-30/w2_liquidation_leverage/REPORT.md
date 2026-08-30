# W2 — Liquidations + Leverage/OI/Positioning: 19 mechanisms tried, 2-4 real, rest weak/dead/blocked

Read-only, round 2 of `alpha_hunt` (round 1: `alpha_hunt_2026-08-29`). Note: this worker could not
call `Write` for the report file (a harness constraint hit mid-run — "Subagents should return
findings as text, not write report files"); evidence JSON was written successfully. This report
is transcribed by the orchestrating session from the worker's final findings, verbatim in
substance.

## Scope, universes, guardrails

Read-only throughout. A7-TAIL-E1's exact spec was never retuned — reproduced once, read-only,
only to compute a Jaccard-overlap sanity check against this worker's own buckets. A2/A2-RV-v1/
A13-H/TRM Fleet never touched. SHORT-shaped findings labelled "not deployable, standing
SHORT_REJECTED rule." Four universes used, stated per mechanism:

1. `data/events/liq_cascade_dataset.parquet` — clean 49-symbol universe (⊂ frozen 50, missing
   PEPEUSDT only), 2021-2026, 38,141 rows. Used for all `L*`. Deliberately avoided the
   312-symbol contaminated `cascade_dataset.parquet`.
2. `/home/qbee/futur-data-v2/data_v2/normalized/event_feature_panel`, filtered to the frozen 50
   from `configs/portfolio_v1_1_parallel_50.yaml` (48/50 present — PEPEUSDT, PYTHUSDT missing),
   resampled to top-of-hour bars, 2020-2026, 2,057,660 symbol-hours. Used for all `O*`.
3. `derivatives_raw` bybit/okx `force_order` (BTC/ETH, 2026-07-04→08-30, 57-day hard ceiling)
   joined to `derivatives_backfill/bybit/perp_klines_1h`.
4. `data/positioning/*` (46-47 symbols) — only 2026-07-16→07-31 overlaps available price data
   (`event_feature_panel` stops 2026-08-01 even though positioning runs to 08-30) — a genuine
   coverage gap, flagged not worked around.

Also used once: `binance_vision_liquidation` COIN-M (BTC/ETH, 2023-06-25→2024-10-14, confirmed
dead-end). Re-confirmed (not re-litigated): Bybit/OKX carry **no OI stream anywhere** in this
backfill — cross-venue OI divergence is BLOCKED_DATA, matching A8. Cost convention: net14 =
gross − 14bps round-trip (matches project `COST_RT`).

## Ranked table

| rank | mechanism | dataset/universe | horizon | events (full/OOS) | gross bps | net bps @14bps | PF/hit | stability | status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **LONG_CASCADE exhaustion** — buy the 3rd+ same-symbol OI-drop cascade in 24h, not the 1st | liq_cascade_dataset (49-sym) | fwd_4h | 1,988/615 | +41.1 | **+27.1 full / +45.2 OOS** | PF 1.43/1.69 | improving 2023→2026, only 2022 weak | **PROMISING** |
| 2 | **Short-covering continuation** — price up + OI down vs. baseline | event_feature_panel hourly (frozen-50) | fwd_4h | 23,422/7,217 | +11.3 | −2.7/+2.3 (**excess +9.2/+19.0** vs baseline) | PF 1.11/1.18, t 5.5/4.7 | strengthens OOS | **PROMISING** |
| 3 | **SHORT_SQUEEZE exhaustion** — same logic, other kind | liq_cascade_dataset (49-sym) | fwd_4h | 1,140/350 | +54.0 | +40.0/**+114.6 OOS** | PF 1.54/2.71 | unstable by year (2022 −50.8, 2025 +145.5 outlier) | **PROMISING-WITH-CAVEAT** (unresolved sign convention, see W1 §1.4 in `alpha_hunt_2026-08-29`) |
| 4 | **"Far from local low" beats "at the low"** | liq_cascade_dataset (49-sym) | fwd_4h | ~6.7k/2.8k | +18.5→+33.6 | +4.5→+19.6 full / **+15.5→+73.3 OOS** | PF 1.18-1.98 | stable, both kinds agree, near-low flips negative OOS | **PROMISING** |
| 5 | Pure ΔOI-shock-down, no breadth/vol condition | event_feature_panel hourly | fwd_4h | 78,174/18,241 | +12.5 | excess **+10.4/+12.1**, t=12.2/4.3 | PF 1.14/1.11 | stable | **NEEDS_FULL_VALIDATION** — corroborates A7, not independent |
| 6 | Positioning taker-flow extremes (sell-extreme fade, buy-extreme continuation) | data/positioning, 46 sym, single 2-wk window | fwd_4h | 1,603/1,613 | +19.4/−49.5 | excess **+27.7/−41.0**, t=7.1/−16.3 | PF 1.70/0.32 | half-split consistent, broad across 15+ symbols incl BTC/ETH | **NEEDS_FULL_VALIDATION** — one regime only |
| 7 | Basis-rich + OI-build ("froth") underperforms | event_feature_panel hourly | fwd_4h | 44,611/15,830 | −4.1 | excess **−6.2/−1.8**, t=−3.8/−2.6 | PF 0.95/0.94 | consistent sign, shrinking OOS | **WEAK** — avoid-filter only; short-side SHORT_REJECTED |
| 8 | Liquidation deceleration / already-delevering (SHORT_SQUEEZE) | liq_cascade_dataset | fwd_4h | 2,822/817-1,092 | +23.7→24.3 | +9.7→10.3/**+25.8→36.5 OOS** | PF 1.24-1.48 | improves OOS | **WEAK-standalone** — Jaccard 0.15-0.19 with #3, likely restatement of the same mechanism |
| 9 | Market-wide clustering (tight event gaps) | liq_cascade_dataset | fwd_4h | 5,361/2,288 | +28.4/+34.4 | LONG decays OOS (+14.4→−7.7); SHORT holds (+20.4→+68.2) | PF 1.31/1.36 | mixed | **WEAK**, overlaps A7's breadth leg |
| 10 | Full-population asymmetric decile shape-check | liq_cascade_dataset | fwd_4h | 26,838/11,287 | +10.3/+12.6 | descriptive only | — | — | **INFORMATIVE** |
| 11 | Funding-state at cascade | liq_cascade_dataset | fwd_4h | 1.5k-3k/212-538 | mixed | sign flips full→OOS, \|t\|<1.7 | PF 0.85-1.24 | none | **DEAD** |
| 12 | Own-venue Bybit large-liq-cluster reaction (BTC/ETH) | derivatives_raw force_order+klines, 57d | fwd_1h/4h | 97-106 | +1.5→12.6 | mostly negative, t<1.3 | PF 1.06-1.50 | too thin | **WEAK/inconclusive** |
| 13 | Real liquidation-feed validation (COIN-M fills) | binance_vision_liquidation (BTC/ETH) | fwd_1d/3d | 48 each | BTC +33.5/+73.0, ETH −3.5/−44.6 | BTC +19.5/+59.0 (n.s.); **ETH −17.5/−58.6 (wrong sign)** | PF 1.31(BTC)/0.98(ETH) | BTC/ETH disagree | **WEAK/inconclusive** |
| 14 | Exhaustion generalized to CROWD_WASHOUT | crowding_dataset | fwd_4h | 0 | — | — | — | — | **N/A** (mechanically empty; CROWD_WASHOUT baseline itself is the already-known WEAK CROWDING_REVERSAL engine, not re-claimed) |
| 15 | OI acceleration (2nd derivative) | event_feature_panel hourly | fwd_4h | 76,183 | +5.3→8.5 | excess ≈0 | PF~1.05-1.09 | none | **DEAD** |
| 16 | Funding/OI disagreement | event_feature_panel hourly | fwd_4h | 40.8k-105k | mixed | excess −4.8→+2.7, inconsistent | PF 0.94-1.06 | none | **DEAD** |
| 17 | Taker-flow(CVD)/OI disagreement | event_feature_panel hourly | fwd_4h | 41k-70k | mixed | excess −4.0→+5.8, inconsistent | PF 0.93-1.10 | none | **DEAD** |
| 18 | Cross-venue OI divergence | derivatives_raw/backfill bybit/okx | — | — | — | — | — | — | **BLOCKED_DATA** (no OI stream, = A8) |
| 19 | Failed breakout with OI build | event_feature_panel hourly | fwd_4h/24h | 166k/54.5k | ≈0→1.0 | excess −3.7→+3.7, t~1-2 | PF~0.97-1.02 | none | **WEAK/DEAD** |

## Top finding detail (#1, LONG_CASCADE exhaustion)

**Mechanism**: condition liq_cascade events on `n_events_sym_24h` (same-symbol repeat count in
trailing 24h) — "onset" (1st in cluster, net14 full −10.6/OOS −18.8bps, negative) vs
"exhaustion" (3rd+, net14 full +27.1/OOS +45.2bps). **Payer**: forced-liquidation sellers — the
1st cascade in a name still has more forced selling ahead; by the 3rd+, most weak hands are
flushed. **Distinctness from A7**: Jaccard overlap with A7's exact tail bucket = 0.116 (LONG) —
low, genuinely different dimension. By-year net14: 2022 −1.8, 2023 +6.4, 2024 +31.5, 2025 +42.2,
2026 +50.9 — improving trend, only one soft year. Frequency ≈360/yr market-wide, top symbols
include ETH/XRP (not purely alt-tail). Same failure modes as A7 (oi_drop_z proxy noise,
universe-drift risk if reimplemented carelessly, correlated risk exposure with A7 since same
detector).

## Bottom line

Of 19 mechanisms: 1 clean new PROMISING finding (#1), 1 modest-but-robust new PROMISING finding
with the best capacity/majors coverage (#2), 1 PROMISING-with-inherited-caveat (#3, SHORT_SQUEEZE
sign question unresolved — same open item flagged in yesterday's sweep), 1 clean secondary
PROMISING (#4), 1 striking-but-thin candidate worth a re-test once positioning data accumulates
past its current single 2-week regime (#6). Rest: corroborating-not-independent (#5), a real-but-
marginal avoid-filter (#7), correlated restatements of the exhaustion family (#8-9), descriptive
(#10), or DEAD/WEAK/BLOCKED (#11-19). Nothing here supersedes A7-TAIL-E1; #1 and #4 are best read
as siblings within the same LIQ_CASCADE detector family, not independent mechanisms — correlated
risk exposure with A7-TAIL-E1 if ever combined into a book.

Evidence files: `evidence/liquidation_L1_L7_full.json`, `evidence/liquidation_L1_L5_deepdive_by_quarter_year.json`,
`evidence/liquidation_bucket_overlap_jaccard.json`, `evidence/liquidation_L8_L9_own_venue_and_real_feed.json`,
`evidence/liquidation_L10_crowd_washout_generalization.json`, `evidence/leverage_oi_O1_O9_full.json`,
`evidence/leverage_O5_positioning_standalone.json`, `evidence/leverage_O5_positioning_robustness.json`.
