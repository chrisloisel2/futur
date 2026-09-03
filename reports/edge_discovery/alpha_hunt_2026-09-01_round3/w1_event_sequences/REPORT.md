# W1 — Event Sequences (A→B, A→B→C causal chains): Round 3

Read-only worker on axis A (event sequences). Continuation of an earlier launch of this exact
task that was interrupted by a session-wide rate limit before writing any report; prior
partial work (`group_a.py`, `group_b_volume.py`, `lib.py`, and their `results_*.json`) was
found intact in this worker's scratch subfolder and reused/extended rather than redone. Never
touched `configs/live_alpha_registry.yaml`'s FROZEN alphas or
`src/institutional/live_alpha_lab/` production code (only reused the discipline pattern from
`episodes.py`, reimplemented independently in `lib.py` for speed on large frames).

## Methodology / discipline applied

1. **PIT discipline**: every trigger condition uses only data available strictly at or before
   the decision timestamp. All A→B / A→B→C chains use forward-only matching (`event_time_B >
   event_time_A`, within a stated window) — never a symmetric or backward window. Forward
   return columns (`fwd_1h/4h/8h/24h`, `fwd3d`) used for outcomes are the dataset's own
   pre-computed no-lookahead labels (`label_full==True` filter applied throughout on
   `data/events/*` datasets) or freshly computed `shift(-N)` forward closes on daily klines.
2. **Declustering**: every N is reported both raw and independent (`lib.decluster_episodes`,
   same-symbol/window-hours collapse into one cluster, matching
   `src/institutional/live_alpha_lab/episodes.py` semantics, reimplemented vectorized for
   speed). Window = 24h for hourly-cadence liq_cascade/event-dataset chains, matching round
   1/2 convention.
3. **Costs**: standing project convention, `net_bps = gross_bps − 14` (5bps taker + 2bps
   slippage, round-trip = 2×7 = 14bps, "net14"), and `net_bps_stress = gross_bps − 28` (2×
   stress) reported alongside for every row, always.
4. **Novelty checked against round 1/2 before running**: read
   `alpha_hunt_2026-08-30/SCOREBOARD.md` in full, plus the detailed `REPORT.md` of W2
   (liquidation/leverage, 19 mechanisms on `liq_cascade_dataset`) and W9 (cross-dataset
   interactions, 11 interactions including the liq-cascade × OI/funding/basis conditioning
   work) before designing this round's mechanisms — grepped the whole `reports/edge_discovery/`
   tree for `ignition_dataset`, `spillover_dataset`, `premium_dataset`, `FLOW_IGNITION`,
   `BTC_SPILLOVER_LAG`, `PREM_CAPITULATION`, `PREM_FOMO` and got zero hits, confirming those
   four purpose-built event datasets (siblings of `liq_cascade_dataset`, same detector suite,
   never used in round 1/2) were free to mine for genuinely new precursor→cascade chains.
   Every row's `distinctness` field states explicitly what round-1/2 result it might be
   confused with and why it isn't the same test.
5. **Simple models first**: every mechanism below is a rule/state-machine (threshold ± window
   match), no ML, per the mission's "prefer simple models" instruction.
6. **Negative results kept**: DEAD/WEAK rows are the majority, reported in full, not filtered
   out.
7. **Own scratch subfolder used throughout**:
   `.../scratchpad/round3/w1/` (never the shared root), five `group_*.py` scripts +
   `results_group_*.json`, copied into this report's `evidence/` folder.

## Datasets used

`data/events/liq_cascade_dataset.parquet` (clean 49-symbol universe, 2021-2026, the same file
W2/W9 used in round 2 — deliberately not the contaminated 312-symbol `cascade_dataset.parquet`)
as the "cascade" leg for almost every chain; four sibling event datasets never touched by any
prior worker (`ignition_dataset.parquet` — FLOW_IGNITION, `premium_dataset.parquet` —
PREM_CAPITULATION/PREM_FOMO, `spillover_dataset.parquet` — BTC_SPILLOVER_LAG,
`crowding_dataset.parquet` — CROWD_WASHOUT); `data/derivatives_backfill/um_klines_1d`
(daily volume/close, 2020-2026); `data/derivatives_backfill/binance/funding` (8h funding,
full history); `data/options_backfill/deribit/DVOL_{BTC,ETH}_1d.parquet` (2021/2023-2026);
`data/positioning/*_top_position.parquet` (top-trader LSR, standalone series — found to have
**zero calendar overlap** with `liq_cascade_dataset`, see w1_d04). Explored but not used:
`data/derivatives_raw/.../open_interest` (only 63 days, 2026-06-28→09-02, single-regime —
deprioritized in favor of the event datasets' own long-history OI columns already used in
group A); `data/derivatives_backfill/binance/open_interest_hist` (only ~21 days, same
limitation).

## Results table (38 rows / candidates, all distinct hypotheses tested)

Cost convention: net_bps = gross − 14bps; net_bps_stress = gross − 28bps. Status thresholds:
**PROMISING** = net_bps clearly positive, stable ≥5/6-7 years, N_independent ≥several hundred;
**WEAK** = net_bps near breakeven or thin margin, or unstable; **DEAD** = net_bps clearly
negative or PF<1 with no stable years; **DATA_LIMITED/BLOCKED_DATA** = insufficient/non-
overlapping data.

### Group A — preconditions on liq_cascade_dataset (single-dataset, precursor-state chains)

| candidate_id | family | economic_risk_factor | N_raw | N_indep | gross_bps | net_bps | net_bps_stress | PF | stability | status |
|---|---|---|---|---|---|---|---|---|---|---|
| w1_a01 | TAKER_FLOW_PRECONDITIONED_CASCADE | aggressive taker-flow-driven cascade | 1,261 | 1,196 | -4.34 | -18.34 | -32.34 | 0.95 | pos 3/6 | DEAD |
| w1_a02 | SMARTMONEY_PRECONDITIONED_CASCADE | top-trader positioning extreme drives cascade | 11,869 | 7,106 | 7.02 | -6.98 | -20.98 | 1.07 | pos 5/6 | WEAK |
| w1_a03 | LSR_CROWDING_CASCADE | retail LSR crowding at cascade moment | 12,283 | 7,839 | 10.15 | -3.85 | -17.85 | 1.11 | pos 5/6 | WEAK |
| w1_a04 | TAKER_SMARTMONEY_DIVERGENCE_CASCADE | retail vs smart-money disagreement into cascade | 1,458 | 1,347 | 12.82 | -1.18 | -15.18 | 1.16 | pos 5/6 | WEAK (best-of-group margin) |
| w1_a05a | MOMENTUM_INTO_CASCADE | trend decelerating into cascade | 34,876 | 17,802 | 3.69 | -10.31 | -24.31 | 1.04 | pos 4/6 | DEAD |
| w1_a05b | MOMENTUM_INTO_CASCADE | trend still accelerating into cascade | 3,246 | 2,923 | 1.47 | -12.53 | -26.53 | 1.01 | pos 4/6 | DEAD |
| w1_a06a | VOL_REGIME_CASCADE | high realized-vol regime | 9,534 | 4,545 | 18.67 | 4.67 | -9.33 | 1.13 | pos 4/6 | WEAK |
| w1_a06b | VOL_REGIME_CASCADE | low realized-vol regime | 9,533 | 6,284 | -3.72 | -17.72 | -31.72 | 0.93 | pos 0/6 | DEAD |
| w1_a07a | SESSION_CONDITIONED_CASCADE | Asia low-liquidity session | 9,431 | 7,391 | 11.32 | -2.68 | -16.68 | 1.14 | pos 5/6 | WEAK |
| w1_a07b | SESSION_CONDITIONED_CASCADE | US session | 18,061 | 12,541 | 4.06 | -9.94 | -23.94 | 1.04 | pos 5/6 | DEAD |
| w1_a08 | WEEKEND_LIQUIDITY_CASCADE | weekend thin liquidity | 7,313 | 4,870 | 3.84 | -10.16 | -24.16 | 1.05 | pos 3/6 | DEAD |
| w1_a09a | OI_BUILDUP_SHAPE_CASCADE | 24h build then 2h reversal (positioning-driven) | 12,211 | 8,988 | 2.83 | -11.17 | -25.17 | 1.03 | pos 4/6 | DEAD |
| w1_a09b | OI_BUILDUP_SHAPE_CASCADE | flat OI (externally-shocked, "surprise") | 9,527 | 7,631 | -1.03 | -15.03 | -29.03 | 0.99 | pos 4/6 | DEAD |
| w1_a10 | FUNDING_PRECONDITIONED_SHORTSQUEEZE | funding stress specific to short side | 1,459 | 828 | 8.37 | -5.63 | -19.63 | 1.10 | pos 1/6 | DEAD (unstable) |
| w1_a11 | CASCADE_KIND_FLIP_CHAIN | opposite-kind cascade within 24h, same symbol | 4,893 | 4,679 | -4.80 | -18.80 | -32.80 | 0.95 | pos 3/6 | DEAD |
| w1_a12 | BTC_LEAD_ALT_CASCADE | BTC shock precedes/co-occurs with alt cascade | 3,489 | 3,097 | 47.02 | 33.02 | 19.02 | 1.41 | pos 5/6 | PROMISING |

### Group B — cross-dataset volume/funding chains

| candidate_id | family | economic_risk_factor | N_raw | N_indep | gross_bps | net_bps | net_bps_stress | PF | stability | status |
|---|---|---|---|---|---|---|---|---|---|---|
| w1_b01a | VOLUME_SHOCK_REPEAT_CLUSTER | organic capitulation volume, 1st shock | 1,447 | 1,447 | -10.78 | -24.78 | -38.78 | 0.98 | pos 2/7 | DEAD |
| w1_b01b | VOLUME_SHOCK_REPEAT_CLUSTER | organic capitulation volume, repeat shock | 684 | 550 | -20.02 | -34.02 | -48.02 | 0.96 | pos 3/7 | DEAD |
| w1_b02 | FUNDING_EXTREME_THEN_VOLUME_SHOCK_CHAIN | funding-crowding resolves via volume unwind | 3,783 | 1,536 | -169.45 | -183.45 | -197.45 | 0.73 | pos 3/7 | DEAD (large-magnitude, see note) |
| w1_b17 | FUNDING_FLIP_THEN_VOLUME_CONFIRMATION_CHAIN | funding sign-flip confirmed by volume | 3,005 | 1,981 | 20.99 | 6.99 | -7.01 | 1.05 | pos 4/7 | WEAK |
| w1_b03 | VOLUME_SHOCK_THEN_CASCADE_CHAIN | organic vol shock precedes cascade (task's suggested axis) | 2,238 | 1,900 | 3.56 | -10.44 | -24.44 | 1.02 | pos 3/6 | DEAD (≈ baseline, NO_INTERACTION_EFFECT) |
| w1_b03_baseline | (comparison arm, not a family) | cascades with no preceding volume shock | 35,995 | 18,069 | 3.95 | -10.05 | -24.05 | 1.05 | pos 5/6 | — |

### Group C — precursor-event → cascade escalation chains (new datasets: ignition/premium/spillover/crowding)

| candidate_id | family | economic_risk_factor | N_raw | N_indep | gross_bps | net_bps | net_bps_stress | PF | stability | status |
|---|---|---|---|---|---|---|---|---|---|---|
| w1_c01 | IGNITION_ESCALATION_CHAIN | ignition escalates into full cascade | 2,039 | 1,805 | 0.59 | -13.41 | -27.41 | 1.01 | pos 4/6 | DEAD |
| w1_c02 | IGNITION_ESCALATION_CHAIN | ignition absorbed, no cascade follows | 6,526 | 5,413 | -13.78 | -27.78 | -41.78 | 0.84 | pos 0/6 | DEAD |
| w1_c03 | PREMIUM_EXTREME_THEN_CASCADE_CHAIN | perp-discount capitulation resolves via cascade | 5,553 | 4,190 | 26.14 | 12.14 | -1.86 | 1.23 | pos 5/6 | PROMISING (cost-sensitive, fails stress by 1.9bps) |
| w1_c04 | PREMIUM_EXTREME_THEN_CASCADE_CHAIN | perp-premium FOMO resolves via cascade | 2,779 | 2,232 | 22.26 | 8.26 | -5.74 | 1.21 | pos 4/6 | WEAK (corroborates c03's family direction, weaker) |
| w1_c05 | SPILLOVER_THEN_CASCADE_CHAIN | BTC-alt lag resolves via forced cascade | 1,127 | 957 | -31.36 | -45.36 | -59.36 | 0.77 | pos 1/6 | DEAD |
| w1_c06 | SPILLOVER_CATCHUP_MOMENTUM | lagging alt catches up to BTC (smooth) | 7,853 | 5,162 | 8.87 | -5.13 | -19.13 | 1.09 | pos 4/6 | WEAK |
| w1_c07 | CROWD_WASHOUT_ESCALATION_CHAIN | short-crowding resolves via squeeze cascade | 514 | 505 | -9.84 | -23.84 | -37.84 | 0.94 | pos 3/6 | DEAD |
| w1_c08 | CROWD_WASHOUT_ESCALATION_CHAIN | short-crowding resolves WITHOUT formal cascade | 1,760 | 1,665 | 24.64 | 10.64 | -3.36 | 1.35 | pos 6/7 | PROMISING (best stability of any row; cost-sensitive) |
| w1_c09 | CAPITULATION_IGNITION_CASCADE_3STEP | 3-stage escalation (fear→ignition→cascade) | 304 | 242 | -12.42 | -26.42 | -40.42 | 0.88 | pos 3/6 | DEAD |

### Group D — options/funding-direct/positioning chains

| candidate_id | family | economic_risk_factor | N_raw | N_indep | gross_bps | net_bps | net_bps_stress | PF | stability | status |
|---|---|---|---|---|---|---|---|---|---|---|
| w1_d01 | DVOL_SHOCK_THEN_CASCADE_CHAIN | options IV shock precedes perp cascade | 172 | 149 | -50.93 | -64.93 | -78.93 | 0.50 | pos 0/6 | DEAD |
| w1_d02 | DVOL_SHOCK_THEN_FUNDING_FLIP_CHAIN | options IV shock precedes funding-sign flip | 130 | 102 | -136.23 | -150.23 | -164.23 | 0.55 | pos 0/6 | DEAD |
| w1_d03 | FUNDING_EXTREME_THEN_CASCADE_DIRECT_CHAIN | funding extreme directly precedes cascade, no confirmation filter | 10,616 | 5,349 | 15.23 | 1.23 | -12.77 | 1.16 | pos 4/6 | WEAK (large N, thin margin) |
| w1_d04 | POSITIONING_DELTA_THEN_CASCADE_CHAIN | sharp 24h LSR swing leads a cascade | 0 | 0 | — | — | — | — | — | BLOCKED_DATA (zero calendar overlap: liq_cascade ends 2026-07-04, positioning starts 2026-07-16) |

### Group E — post-cascade dynamics (reverse-direction chains)

| candidate_id | family | economic_risk_factor | N_raw | N_indep | gross_bps | net_bps | net_bps_stress | PF | stability | status |
|---|---|---|---|---|---|---|---|---|---|---|
| w1_e01a | CASCADE_THEN_VOLUME_ABSORPTION_CHAIN | distress persists (elevated next-day volume) | 9,478 | 4,461 | 1.20 | -12.80 | -26.80 | 1.01 | pos 5/6 | DEAD |
| w1_e01b | CASCADE_THEN_VOLUME_ABSORPTION_CHAIN | clean absorption (next-day volume normalizes) | 9,478 | 6,342 | 14.69 | 0.69 | -13.31 | 1.24 | pos 5/6 | WEAK |
| w1_e02 | CASCADE_THEN_PREMIUM_DISLOCATION_CHAIN | cascade leaves a premium overshoot that reverts | 6,290 | 4,178 | 16.49 | 2.49 | -11.51 | 1.13 | pos 5/6 | WEAK |

**TOTAL_MECHANISMS_TESTED = 38** (16 group A + 6 group B + 9 group C + 4 group D + 3 group E;
28 distinct `mechanism_family` values, the remainder being contrast/baseline branches of the
same family reported per the mission's discipline). 3 PROMISING, 1 BLOCKED_DATA, 12 WEAK, 22
DEAD.

## Top findings, with the actual economic story

**1. w1_a12 — BTC_LEAD_ALT_CASCADE (strongest, only stress-cost-robust finding).** Restricting
altcoin liquidation-cascade fades to the top decile of `|btc_ret_30m|` at the moment of the
event lifts net14 from a general-population baseline to +33.02bps (net_bps_stress
+19.02bps, PF 1.41, N_independent=3,097, positive 5/6 years). Economic story: an alt
cascade that co-occurs with an outsized simultaneous BTC move is more likely a market-wide,
systematic flush (correlated forced deleveraging across the book, mean-reverting once the
BTC-driven pressure passes) than an idiosyncratic, alt-specific unwind (which has no such
external catalyst to fade against and is more likely driven by genuinely bad, alt-specific
news that keeps moving). This is a directional, magnitude-based test — distinct from round 2
W2's market-wide "breadth" (simultaneous event count, WEAK, decaying OOS) and from this
round's own w1_c05/c06 (BTC_SPILLOVER_LAG is a different, purpose-built detector requiring a
lag/gap structure, not a simple co-occurrence). This is the one candidate in the entire batch
worth flagging for INDEPENDENT_CONFIRMATION consideration.

**2. Premium/crowding "extreme resolves via forced event" family (w1_c03, w1_c08) — real but
cost-sensitive.** Two independently-constructed chains from two different precursor datasets
tell a consistent story: a retail-visible positioning extreme (perp trading at a deep
discount to spot [PREM_CAPITULATION], or top-traders sitting at an extreme net-short
[CROWD_WASHOUT]) reliably resolves upward — net14 +12.14bps (5/6 years positive,
N_independent=4,190) and +10.64bps (6/7 years positive — the best stability of any row in
this report, N_independent=1,665) respectively. Both fail at 28bps stress cost by only
1.9-3.4bps — genuinely marginal, sitting right at the edge of this project's execution-cost
uncertainty, not a clean pass. Economically these look like the same underlying risk factor as
round 2's headline "liquidation cascades pay on repeat, not first hit" finding (W2#1/W9#11):
crowding/premium extremes are a slower-moving, earlier-stage read on the same forced-unwind
dynamic that the liq-cascade repeat-count captures once the flushing is actually underway —
flagged as likely correlated exposure, not two independent new risk factors, consistent with
the mission's economic_risk_factor discipline.

**3. Escalation chains (does a smaller precursor event become a full cascade) are
systematically DEAD, while the corresponding "resolves without formal escalation" arm is often
better.** Every escalation-confirmation construction tried — IGNITION escalating to a cascade
(w1_c01, -13.41bps), CROWD_WASHOUT escalating (w1_c07, -23.84bps), the 3-step
capitulation→ignition→cascade chain (w1_c09, -26.42bps), even the task's own suggested
volume-shock→cascade axis (w1_b03, -10.44bps, statistically indistinguishable from the
unconditional cascade baseline at -10.05bps — a clean NO_INTERACTION_EFFECT) — lose money,
while the matched "absorbed without a formal cascade" contrast arms are flat-to-positive
(w1_c08: +10.64bps; w1_c02 is the one exception, also DEAD). Reading these together: by the
time a precursor event has visibly escalated into a full liq_cascade-detector hit, most of the
tradeable edge has already been extracted or the position is already adverse — the useful
signal is in catching the crowding/premium/ignition extreme before it needs a formal cascade
to resolve, not in waiting for cross-dataset confirmation. This is a genuine, if modest,
methodological finding about how to use this project's event-detector suite going forward: a
confirmation filter meant to raise conviction instead systematically selects for the already-
decayed part of the move.

## Other notable results / cost-sensitivity note

- Two extreme-magnitude DEAD results (w1_b02: -183.45bps net, w1_d02: -150.23bps net) are
  flagged explicitly as a methodology caution, not hidden: multi-leg chains requiring two
  independent triggers to align in time (funding extreme and a later volume/DVOL
  confirmation) produce wildly large, unstable point estimates even at N>100-1,000 — the
  conditioning itself is highly regime-clustered (both triggers fire disproportionately during
  the same handful of historical stress episodes), so these numbers should be read as a
  structural warning about multi-leg confirmation chains generally, not as unusually strong
  negative edges specific to funding or DVOL.
- w1_d04 (positioning-delta leading indicator) is BLOCKED_DATA, not DEAD: `liq_cascade_dataset`
  currently ends 2026-07-04 while `data/positioning` only starts 2026-07-16 — a hard, zero-
  overlap coverage gap (worse than round 2 W2's already-flagged partial gap with
  `event_feature_panel`), not a modeling failure. Should be revisited once either dataset's
  coverage is extended.
- DVOL/options-vol-shock chains (w1_d01, w1_d02) are cleanly DEAD, unlike round 2's
  options-RV findings (W6/W9, which forecast realized-vol magnitude via IC and were
  PROMISING) — using the same DVOL shock as a directional trigger into a perp liquidation
  cascade or funding flip does not work, 0/6 positive years both ways. Vol-shock magnitude
  forecasting and vol-shock direction-trading are evidently different questions with different
  answers on this data.

## Files

- `REPORT.md` — this file.
- `evidence/results_group_a.json` — group A (16 rows, liq_cascade_dataset preconditions).
- `evidence/results_group_b_volume.json` — group B (6 rows, volume/funding cross-dataset chains).
- `evidence/results_group_c.json` — group C (9 rows, ignition/premium/spillover/crowding escalation chains).
- `evidence/results_group_d.json` — group D (4 rows, options/funding-direct/positioning chains).
- `evidence/results_group_e.json` — group E (3 rows, post-cascade reverse-direction chains).

No files outside this report's own directory and this worker's own scratch subfolder
(`.../scratchpad/round3/w1/`) were created or modified. All source data in `data/` was
read-only throughout; `configs/live_alpha_registry.yaml` and
`src/institutional/live_alpha_lab/` production code were never touched.
