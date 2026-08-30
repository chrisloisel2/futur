# W1 — LIQ_CASCADE / CROWDING_REVERSAL / PREMIUM_DISLOCATION: shadow-vs-backtest contradiction, root-caused

## Executive summary

**BUG_FOUND, not regime decay.** The live shadow (`/home/qbee/futur/reports/liq_cascade/shadow/`) is losing money (PF 0.56, mean −96.6 bps over 168 labelled LIQ_CASCADE decisions, 49 days) not because the mechanism died, but because its candidate universe silently grew from the 50 curated, gate-validated symbols (`/home/qbee/futur/configs/portfolio_v1_1_parallel_50.yaml`) to **312 symbols** between 2026-08-09 and 2026-08-14 — a side effect of the unrelated `feat/free-derivatives-backfill` work landing ~260 new Vision-metrics files in `data/derivatives_backfill/binance_vision_metrics/`, a directory that both `scripts/run_event_shadow_daily.py` (line 139-140) and `scripts/train_event_engine.py` (line 198-199) scan with a bare `glob("*_metrics_5m.parquet")` instead of reading the frozen universe file. Splitting the shadow ledger by "symbol in the validated 50-universe" is a clean natural experiment: **in-universe decisions run PF 2.19 / +58.5 bps (74 trades) — genuinely positive, matching the historical edge — while the 94 decisions (56% of the ledger!) on drifted symbols (UBUSDT, BEATUSDT, SKYAIUSDT, KAITOUSDT, meme/micro-cap tickers never part of any validated harness) run PF 0.39 / −218.8 bps**, dragging the blended metric below 1. The exact same contamination explains the weekly-retrain walk-forward's apparent "2025-2026 destructive" flip on 2026-08-16 (tape size jumped 7,537→29,771 trades in one week, +4x, right after the Aug-14 backfill burst): split the same way, 2025 in-universe PF is 0.99 vs 0.82 out-of-universe, 2026 in-universe PF is 1.33 vs 0.93 out-of-universe — every single year is better in-universe than out. Cost accounting (14 bps round-trip, consistent everywhere) and timestamp lag (event→decision lag is 1.3-5.6 days, matching the documented Vision-J2 design, not a leakage bug) are both clean; this is a pure universe-drift/execution-mismatch bug, independently of whatever residual decay the mechanism has. Separately, a freshly re-derived, purely causal **A7-TAIL-E1** test (thresholds fit on 2021-2024 only, tested blind on 2025-2026, run only on the clean 49-symbol dataset) reproduces the DEEP_DIVE tail-bucket edge honestly out of sample: n=3,799 (28% of the population), net +23.1 bps/trade at 14 bps cost (PF 1.19), +9.1 bps at 28 bps stress (PF 1.07), positive in **every one of the 7 test quarters** at realistic cost. Verdict: **PROMISING**, small, and mechanistically fragile — worth a proper shadow re-run once the universe-drift bug is fixed, not worth sizing capital against yet. CROWDING_REVERSAL is contaminated by the same universe-glob bug but its instability (PF swinging 0.16→8.4 year to year even inside the validated 50-universe) is a separate, pre-existing small-sample problem; PREMIUM_DISLOCATION is **not** affected by the drift (it stays on 49 symbols throughout) and its NO_EDGE-at-gate verdict is unchanged and genuine (2023 fold PF 0.70, n=45). Neither of the other two engines has ever produced a single decision in 49 days of live shadow — there is no forward evidence for them at all, positive or negative.

---

## 1. Shadow-vs-backtest contradiction — diagnosis

### 1.1 The numbers that don't reconcile

- `EDGE_STACK.md` / `LIQ_CASCADE_WALKFORWARD.json` (historical OOS, sized 10%): net +152.7%, 7,152 trades, 2022-01-02 → 2026-07-04.
- `shadow/shadow.log` (live forward, 49 days from 2026-07-10): tier `book` (score ≥ 0.70) PF 0.49, mean −45.1 bps, n=9 labelled; tier `probe` (0.50-0.70) PF 0.57, mean −99.5/−99.6 bps, n=158-159 labelled. Both well below 1.

### 1.2 Ruled out by direct inspection

- **Cost accounting**: `scripts/run_event_shadow_daily.py:51` (`COST_RT = 0.0014`) is identical to `scripts/train_event_engine.py:36` and to the walk-forward's declared "coûts 14 bps" gate. `net_labeled` is computed once, appended (`led.loc[sub.index, "net_labeled"] = j[spec["horizon"]].values - COST_RT`, lines 226-227), never touched again — no double counting, no missing leg. Not the bug.
- **Timestamp/lag leakage**: `decisions.parquet` has `event_time` and `decided_at`. Lag across all 15 real run-batches: min 1.28 d, max 5.56 d, mean 2.44 d — consistent with the documented "Shadow sur données Vision J-2" design comment (`run_event_shadow_daily.py:20-22`). No multi-week-old event masquerading as fresh. Ruled out.
- **Tail-bucket selection**: the shadow's `book`/`probe` split is a *model-confidence* threshold (`CONF_THRESHOLD=0.70`, `PROBE_THRESHOLD=0.50`, lines 55-63), unrelated to the DEEP_DIVE descriptive buckets. By design, not the discrepancy.

### 1.3 The actual bug: silent universe drift via a shared glob

`scripts/run_event_shadow_daily.py:139-140`:
```python
symbols = sorted(p.stem.replace("_metrics_5m", "")
                 for p in METRICS_DIR.glob("*_metrics_5m.parquet"))
```
`scripts/train_event_engine.py:198-199` (weekly retrain) does the identical thing. `METRICS_DIR` is `data/derivatives_backfill/binance_vision_metrics` (`src/institutional/engines/liq_cascade/detector.py:28`). Neither script reads `configs/portfolio_v1_1_parallel_50.yaml` (the frozen, gate-declared universe). Whatever files happen to exist in that directory become the candidate universe for both the weekly walk-forward tape and the live shadow.

**Timeline, from file mtimes** in `data/derivatives_backfill/binance_vision_metrics/`:

| date | new files added |
|---|---:|
| 2026-07-06 | 2 |
| 2026-08-09 | 13 |
| 2026-08-10 | 28 |
| **2026-08-14** | **222** |
| 2026-08-29 | 47 (routine top-up touch) |

Total today: **312 files**, vs. the 50-symbol declared universe. The +222 burst on 2026-08-14 lines up exactly with the `feat/free-derivatives-backfill` branch's own mission — an unrelated bulk ingestion of Vision metrics for hundreds of extra symbols landed in a directory these two scripts glob indiscriminately.

**Direct consequence on the live shadow** (`decisions.parquet`, 168 labelled rows, 83 unique symbols, 46 outside the validated 50-universe — UBUSDT, BEATUSDT, SKYAIUSDT, KAITOUSDT, RAREUSDT, MUBARAKUSDT, TRUMPUSDT, BOMEUSDT, PEOPLEUSDT, 1000LUNCUSDT, and 36 more):

| split | n | PF | mean bps |
|---|---:|---:|---:|
| in validated 50-universe | 74 | **2.194** | **+58.5** |
| outside (drifted) | 94 | **0.387** | **−218.8** |

94/168 = 56% of all shadow decisions are on symbols never part of the validated harness. The model doesn't distinguish them by confidence (mean score 0.599 in-universe vs 0.601 out — nearly identical), consistent with covariate-shift extrapolation: `MH_LIQ_CASCADE.pkl` was trained on exactly 38,138 rows (matches `data/events/liq_cascade_dataset.parquet`, 49 symbols, trained 2026-07-13) and is scored, post-hoc, on hundreds of symbols it never saw in training. Mechanistically: `oi_drop_z` is a per-symbol rolling z-score (`liq_cascade/dataset.py:_roll_z`); thinly-backfilled, low-liquidity, newly-listed tokens have unstable rolling baselines, so ordinary moves trip "extreme" thresholds calibrated for liquid majors/large-alts, and forward outcomes are dominated by idiosyncratic micro-cap risk (worst trades: UBUSDT −27.9%, −21.7%, −18.6%, −15.7%, −13.6% net over 4h) rather than the capitulation-relief-bounce mechanism the edge was built around.

**Same bug, same evidence, in the weekly-retrain walk-forward** (`LIQ_CASCADE_trades.parquet`, 29,817 rows — 4x the 7,537-row tape from the 2026-08-09 retrain, one week before the Aug-14 backfill burst):

| year | in-universe PF | out-of-universe PF |
|---:|---:|---:|
| 2022 | 0.746 | 0.745 |
| 2023 | 2.979 | 1.778 |
| 2024 | 1.403 | 1.290 |
| 2025 | **0.994** | 0.823 |
| 2026 | **1.330** | 0.931 |
| overall | 1.107 (+11.8 bps) | 0.960 (−6.0 bps) |

Every year is stronger in-universe than out. `reports/liq_cascade/weekly_retrain.log` shows the verdict physically flip between the two runs bracketing the backfill burst:

```
=== WEEKLY RETRAIN 2026-08-09 ===  tape: 7,537 trades
VERDICT LIQ_CASCADE : CANDIDATE (valides [2023,2024,2025,2026], PF≥1.35 3/4, destructeurs [], exclus [2022])

=== WEEKLY RETRAIN 2026-08-16 ===  tape: 29,771 trades   ← +222 metrics files landed 2026-08-14
VERDICT LIQ_CASCADE : NO_EDGE (valides [2023,2024,2025,2026], PF≥1.35 1/4, destructeurs [2025, 2026], exclus [2022])
```

The "2025 and 2026 both suddenly destructive" signal that would normally read as genuine decay is, at minimum, partly a universe-composition artifact.

**Verdict: BUG_FOUND (universe-drift / execution-mismatch).** Fix is mechanical: both scripts should read `configs/portfolio_v1_1_parallel_50.yaml`'s `universe` list (already loaded elsewhere in `run_event_shadow_daily.py:119-120` for the topup step) instead of globbing `METRICS_DIR`. Until patched, **no promotion decision should be made off the current shadow ledger** — the `book`/`probe` PF numbers in `shadow.log` measure a 56%-contaminated blend, not the validated edge.

### 1.4 Secondary, unresolved caveat found along the way (not the cause of the contradiction, but flag before trading)

`liq_cascade/detector.py:95` labels `SHORT_SQUEEZE` events (price↑ + OI↓, shorts liquidated) but no code path anywhere (`dataset.py`, `train_event_engine.py`, `event_production.py`, `run_event_shadow_daily.py`) flips the sign of `fwd_4h` for that kind — raw signed price return is used identically for `LONG_CASCADE` and `SHORT_SQUEEZE`. If the intended trade on a short-squeeze event is to *short* the exhaustion (the "mean reversion" framing used throughout `DEEP_DIVE.md`), correct PnL is `-fwd_4h`, not `fwd_4h`. As coded, `SHORT_SQUEEZE` numbers everywhere in this research track represent "buy after a short-covering spike," a momentum-continuation bet, not a reversion-short. This has been baked in identically since inception (not the cause of the recent divergence) but means the "kind" split has an ambiguous, possibly inverted, economic interpretation. Flipping the sign would take the A7-TAIL-E1 SHORT_SQUEEZE sub-segment from +63.8 bps/PF 1.59 to roughly −64 to −78 bps/PF≈0.6. Not resolved here for lack of an authoritative execution-side reference; flagged as an open question for whoever owns execution.

### 1.5 Independent corroboration that the underlying proxy is noisy

`PROXY_VS_REAL.json` (2026-07-17, 23.5h overlap window, `INDICATIVE_ONLY_OVERLAP_LT_7D` — too short to be definitive alone): the Vision-metrics `oi_drop_z` proxy recalls only 4.9% of real liquidation clusters from the actual `force_order` feed, at 0.8% precision. Consistent with — not proof of — a detector that is inherently noisy about what counts as a genuine cascade, on top of the universe-drift bug.

---

## 2. A7-TAIL-E1 — fresh causal re-derivation of the tail-bucket edge

**Data used**: `data/events/liq_cascade_dataset.parquet` only (38,141 rows, 49 symbols, 2021-01-04→2026-07-04 — the clean, gate-declared dataset). **Deliberately did NOT use `data/events/cascade_dataset.parquet`** (146,920 rows, 312 symbols) — §1.3 proved that file is the contaminated one; reusing it here would launder the exact bug this report exists to surface.

**Method**: split at 2025-01-01. Fit period = 2021-2024 (n=24,465), used *only* to set the "extreme vol quartile" cutoff (`vol_24h ≥ 0.0698`, 75th pct of `vol_24h` in the fit period — fixed, carried forward unchanged). `oi_drop_z ≤ -8` and `n_events_mktwide_30m > 5` are literal DEEP_DIVE thresholds (fixed numbers, not adaptive quantiles). Tail bucket = union of the three. Test period = 2025-2026 (n=13,676), rule applied blind, no refitting. Sanity check on fit period: n=8,915 (36%), net14 mean +15.6 bps, PF 1.13 — confirms the bucket wasn't cherry-picked using 2025-2026 knowledge.

**Result on the pure holdout (2025-2026)**:

| | n | gross mean | net@14bps | PF@14bps | net@28bps (stress) | PF@28bps |
|---|---:|---:|---:|---:|---:|---:|
| tail bucket | 3,799 (28% of pop.) | +37.1 bps | **+23.1 bps** | **1.190** | +9.1 bps | 1.071 |
| rest of population | 9,877 | −5.7 bps | −19.7 bps | 0.769 | −33.7 bps | 0.639 |

By year: 2025 n=2,569, PF 1.159, +21.3 bps; 2026 (partial, to Jul) n=1,230, PF 1.283, +27.0 bps — no sign flip, if anything stronger.

By quarter (14 bps cost) — **positive in all 7 quarters**:

| quarter | n | net bps | PF |
|---|---:|---:|---:|
| 2025 Q1 | 702 | +10.2 | 1.066 |
| 2025 Q2 | 641 | +23.1 | 1.278 |
| 2025 Q3 | 486 | +11.8 | 1.133 |
| 2025 Q4 | 740 | +36.5 | 1.194 |
| 2026 Q1 | 558 | +10.4 | 1.091 |
| 2026 Q2 | 666 | +41.0 | 1.512 |
| 2026 Q3 (partial, 6 events) | 6 | +23.9 | 1.377 |

At 2x stress cost (28 bps), both Q1s dip slightly negative (−3.8, −3.6 bps) — a recurring seasonal soft spot; otherwise 5/7 quarters hold up under stress.

Sub-condition decomposition (2025-2026): `oi_drop_z≤-8` alone: n=212, PF **4.04**, +181 bps — smallest population, strongest signal. `n_events_mktwide_30m>5` alone: n=1,891, PF 1.39, +41.4 bps. `vol_24h extreme` alone: n=2,238, PF 1.38, +52.6 bps. All three legs individually positive out of sample.

By kind: `LONG_CASCADE` n=2,610, net +4.6 bps, PF 1.036 (marginal, negative under stress); `SHORT_SQUEEZE` n=1,189, net +63.8 bps, PF 1.594 (strong) **but see §1.4 — sign convention unresolved.**

### Template

- **MECHANISM**: as coded — buy immediately following a violent 30-minute OI-drop event, restricted to the rare/large tail (extreme depth, market-wide breadth, or extreme realized vol), hold to fwd_4h, exit. `LONG_CASCADE` = capitulation dip-buy; `SHORT_SQUEEZE` = as coded, a continuation bet after a short-covering spike (§1.4 caveat).
- **PAYER**: forced deleveraging flow creating a transient price dislocation that liquidity providers/opportunistic buyers are compensated for absorbing; in the market-wide-breadth leg, cross-symbol contagion/overreaction during systemic stress.
- **WHY EDGE EXISTS**: at the true tail the dislocation is large enough to be economically distinguishable from noise even after realistic costs — matches DEEP_DIVE's full-history finding (z≤-8: +91.4 bps, PF 2.18, n=640), now shown to also hold walk-forward-clean on 2025-2026 with thresholds frozen from 2021-2024.
- **SIGNAL**: `oi_drop_z ≤ -8` OR `n_events_mktwide_30m > 5` OR `vol_24h ≥ 0.0698` (fixed, fit on 2021-2024 only).
- **ENTRY**: next 5-min bar after event detection.
- **EXIT**: fwd_4h fixed horizon.
- **EXECUTION VENUE**: Binance USDT-M perps (source data); OKX/Bybit streams exist under `data/derivatives_raw` but were not used here.
- **EXPECTED HORIZON**: 4 hours/trade; ~3,800 qualifying events over 19 months in the 49-symbol universe (≈6.6/day market-wide, low single-digit per-symbol frequency — WIFUSDT/ORDIUSDT/ENAUSDT top-3, ~110-150 events over 2025-2026, roughly one every 4-5 days per symbol).
- **EXPECTED CAPACITY**: rough/directional only. `oi_drop_30m` in the tail bucket is a relative fraction: median −1.9%, 75th pct −1.4%, worst −25.5% of the symbol's own OI in 30 min. Spot-check WIFUSDT OI (`data/derivatives_raw/exchange=binance/market=usdm/stream=open_interest/symbol=WIFUSDT`, 2026-08-29 snapshot): ≈87.4M contracts × $0.2017 ≈ **$17.6M notional OI** — a typical tail event in a mid-cap alt corresponds to roughly $250K-$500K of forced-flow notional (up to a few million at the extreme). Population is alt-heavy (top-10 by frequency has no BTC/ETH), capping single-name capacity to low hundreds of thousands to low single-digit millions per trade; aggregating across ~15-20 eligible symbols could plausibly support a low-single-digit-million-dollar book, not more.
- **MAIN FAILURE MODE**: (1) the universe-drift bug in §1 — operationalizing through the same code path without fixing the glob re-contaminates immediately; (2) SHORT_SQUEEZE sign-convention ambiguity (§1.4) — resolving it the "reversion" way flips ~1,189/3,799 test trades and likely erases the aggregate edge; (3) `oi_drop_z` proxy independently measured at only 4.9% recall/0.8% precision against real liquidations (§1.5); (4) thin sample at the strongest sub-condition (z≤-8, n=212 over 19 months); (5) Q1 seasonal softness under stress costs both test years.

**VERDICT: PROMISING** (small, thin, two unresolved caveats — not DEAD, not ready to size).

---

## 3. CROWDING_REVERSAL and PREMIUM_DISLOCATION — same bug, or different problem?

Neither engine has ever produced a single decision in the live shadow: `decisions.parquet`'s `engine` column is 100% `LIQ_CASCADE` across all 168 rows and 49 days. **No forward evidence exists for either engine**, positive or negative — MH-consensus thresholds (registry: CROWDING_REVERSAL n_train=2,241, val_auc 0.54-0.60; PREMIUM_DISLOCATION n_train=27,437, val_auc 0.52-0.54) simply never clear the 0.50 probe bar in practice, or some other gating issue silently suppresses them — not investigated further given time budget (BLOCKED_DATA for shadow purposes).

**CROWDING_REVERSAL** *is* affected by the same universe-glob bug (`CROWDING_REVERSAL_trades.parquet`: 281 symbols, only 49 declared) but, unlike LIQ_CASCADE, the drifted subset isn't uniformly worse — in-universe PF 3.18/+269.5 bps, out-of-universe PF 2.06/+239.3 bps, both strong in aggregate, and both cohorts crash together in 2022 (PF 0.16/0.19) and 2026 (PF 0.88/0.64). Weekly-retrain verdict has been `NO_EDGE` since 2026-08-09 regardless (destructive fold 2026, both cohorts). Reads as genuine, pre-existing small-sample instability (PF swinging 0.16→8.42 year to year even inside the 49-universe, n as low as 16-48) rather than the same contamination mechanism — already flagged at inception in `EDGE_INVENTORY_2026-07-10.md` ("verdict dominé par 2026, n=43"). **WEAK** — too little independent signal to trust either way, zero live decisions to check against.

**PREMIUM_DISLOCATION** is **not** contaminated — `PREMIUM_DISLOCATION_trades.parquet` stays at 49 symbols throughout (the basis detector apparently only runs where `premiumIndexKlines` data exists, outside the runaway backfill). Walk-forward stable and genuine since inception: 2022 PF 1.08, 2023 PF 0.70 (the one destructive, small n=45 fold blocking the gate), 2024 PF 1.47, 2025 PF 1.27, 2026 (partial) PF 1.08 — no decay pattern, no drift artifact, matches `EDGE_INVENTORY_2026-07-10.md`'s original "edge réel mais instable." **WEAK** — real but gate-blocked by one bad fold, unchanged, and untested live since it never fires in shadow.

**Bottom line: LIQ_CASCADE is uniquely the one with a live, measurable, root-caused bug.** CROWDING_REVERSAL shares the code-level contamination but its core problem is independent small-sample fragility; PREMIUM_DISLOCATION is clean of the drift bug and its NO_EDGE status is genuine and unchanged. All three share a deeper problem: zero live shadow evidence exists for two of the three engines, and the one engine with live evidence has been measuring the wrong population for at least the second half of its 49-day run.

---

**Key file references** (all absolute paths, read-only, nothing modified):
- `/home/qbee/futur/scripts/run_event_shadow_daily.py` (lines 51, 55-63, 119-120, 139-140, 226-227)
- `/home/qbee/futur/scripts/train_event_engine.py` (lines 36, 198-199)
- `/home/qbee/futur/src/institutional/engines/liq_cascade/detector.py` (lines 8-9, 28, 95)
- `/home/qbee/futur/src/institutional/engines/liq_cascade/dataset.py` (`_roll_z`, `fwd_{name}` construction)
- `/home/qbee/futur/configs/portfolio_v1_1_parallel_50.yaml` (the frozen 50-symbol universe, ignored by the two scripts above)
- `/home/qbee/futur/data/derivatives_backfill/binance_vision_metrics/` (312 files vs 50 declared)
- `/home/qbee/futur/artifacts/event_engines/multihorizon_registry.json`
- `/home/qbee/futur/reports/liq_cascade/shadow/decisions.parquet`, `shadow.log`, `state.json`
- `/home/qbee/futur/reports/liq_cascade/weekly_retrain.log`, `LIQ_CASCADE_trades.parquet`, `CROWDING_REVERSAL_trades.parquet`, `PREMIUM_DISLOCATION_trades.parquet`
- `/home/qbee/futur/data/events/liq_cascade_dataset.parquet` (clean, 49 symbols — used for A7-TAIL-E1) vs `/home/qbee/futur/data/events/cascade_dataset.parquet` (contaminated, 312 symbols — deliberately excluded)

No files were created, moved, or overwritten during this investigation.
