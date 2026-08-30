# Alpha Hunt 2026-08-29 — aggregate scoreboard

Six parallel workers (W1-W6), read-only on all existing data/reports, no deletions, no sealed
experiments touched (A2, A2-RV-v1, A13-H, TRM Fleet). Per-worker detail in `w*/REPORT.md`.
Two of the mission's "candidates connus" (A2, A2-RV-v1) were correctly left untouched by every
worker; A13-H was correctly not reopened (a new ID, A13-H-E1, was built instead).

## Ranked table

| rank | mechanism | data | gross | est. net | n | regimes | status |
|---|---|---|---|---|---|---|---|
| 1 | A7-TAIL-E1 — liquidation cascade, rare+large tail (oi_drop_z≤-8 / market-wide>5 / extreme vol) | `data/events/liq_cascade_dataset.parquet` (49 sym, clean) | +37.1bps | +23.1bps@14bps, +9.1bps@28bps stress | 3,799 (2025-26 pure holdout, thresholds fit 2021-24) | positive 7/7 test quarters | **PROMISING, kept as-is** — SHORT_SQUEEZE sign resolved (see below, not flipped); 1 open caveat remains (proxy 4.9% recall vs real liq feed) |
| 2 | Calendar basis — perp vs Binance quarterly futures, hedged spread | `derivatives_backfill/binance_vision_quarterly` + perp | k7d +10.0bps declustered (was +360-680bps on raw autocorrelated prints) | k7d/k3d not significant (p=0.42/0.25) after proper 1-obs-per-episode decluster; k1d flips net-negative after 15bps cost; k14d nets +19.5bps next to a **-703.6bps worst episode** | 91-93 true independent episodes (was 111-114 raw daily prints of the same regimes) | BTC directionally positive but not significant (t=1.50,p=0.14); **ETH flat (t=-0.01,p=0.99)**, driven to zero by two -570bps blowups in 2021-03 & 2024-03 "rich keeps getting richer" regimes | **DOWNGRADED to WEAK/high-tail-risk** — see `w3_derivatives/ADDENDUM_calendar_basis_episode_decluster.md`. Real bid/ask/depth execution reconstruction is impossible: this data does not exist anywhere (Binance Vision never published historical L2; the local quarterly-futures files keep close price only) |
| 3 | A14 — options IV-shock → BTC realized-vol forecast | `options_backfill/deribit` (BTC only) | IC +0.32 daily (partial +0.22 after RV-clustering control) | no execution vehicle here | 1,294 days / 1,867 hours | stable across time-split, both granularities | **PROMISING signal, NOT tradeable standalone** — route into existing VRP short-variance overlay instead of a new sleeve |
| 4 | A11 — informed wallet flow (Hyperliquid, top 1% wallets) | `hyperliquid/trades` (buyer/seller addresses) | +1.24 to +3.21bps (top-1% cohort, 2 horizons) | thin vs 4-9bps HL round-trip | 282 wallets, 42 days | sign-stable in both independent test sub-halves, but wallet-level p=0.44, bootstrap CI crosses 0 | **WEAK/inconclusive** — genuinely novel, retest after 12+ weeks collection |
| 5 | Cross-venue liquidation propagation fade (Bybit→OKX) | `derivatives_raw` force_order | 2.2-4.3x cascade-propagation lift | fade nets 3.5-5.3bps, barely clears maker cost | BTC/ETH only, 57 days | one regime only | **WEAK/marginal** — "follow" direction is wrong-signed and loses money |
| 6 | A4 — liquidity resilience / refill asymmetry | `market_physics_v3/raw` (3 sym, 2 wks) | 0.5-0.9bps (bybit ask) | sub-cost vs 4.5bps taker | small, exploratory | sign-stable across venues/periods | WEAK, sub-cost |
| 7 | A1 — cross-venue price discovery / lead-lag | idem | up to 1.4bps @ p99 extremity | sub-cost | idem | monotonic-in-extremity, sign-stable | WEAK, sub-cost |
| 8 | CROWDING_REVERSAL (pre-existing engine) | `reports/liq_cascade/` | +239 to +270bps both cohorts (in/out universe) | PF swings 0.16-8.4x year to year | 16-48 trades/yr | crashes 2022 & 2026 both cohorts | WEAK — small-sample fragile, **zero live shadow evidence ever produced** |
| 9 | PREMIUM_DISLOCATION (pre-existing engine) | `reports/liq_cascade/` | stable ex-2023 | gate-blocked by one fold (2023 PF 0.70, n=45) | varies | stable 2022/24/25/26 | WEAK — gate-blocked, unchanged, **never fires in live shadow** |
| 10 | A5 — toxic flow / absorption | `market_physics_v3/raw` | BTC/ETH ≈0; SOLUSDT/bybit swung +11→-8.5bps between periods | sub-cost | idem | SOL result is an **artifact, discard** | WEAK |
| 11 | A9 — basis velocity/acceleration | `data_v2/event_feature_panel` | t up to 14 alone | collapses to t=0.9-3.3 once orthogonalized vs basis level | 1.48M OOS | — | WEAK — **not genuinely new**, mostly the already-exhausted level effect |
| 12 | A3 — queue depletion hazard | `market_physics_v3/raw` | present on Bybit only | sub-cost | small | binance/OKX **BLOCKED_DATA** (crossed-book bug, see below) | WEAK/BLOCKED_DATA |
| 13 | A6 — liquidity shock propagation | `market_physics_v3/raw` | none detected as tested | — | — | — | DEAD as tested — undirected combined-depth-shock design washes out signal; a directional redesign might revive it |
| 14 | A10 — funding settlement event | `data_v2/event_feature_panel` | \|t\|<1.3 | ≈0 | 27k-112k events, 29 symbols | sign flips year to year | **DEAD** |
| 15 | A13-H-E1 — event-driven residual RV (new ID, mandated spec) | `data_v2/event_feature_panel` | +2.3bps gross | -17.7bps net (fee-only floor) | 546,251 | negative 19/19 quarters, 5/5 years | **DEAD** |
| 16 | A12 — cross-asset leader→follower propagation | idem | -1.5 to -7.2bps (wrong sign vs hypothesis) | worse net | 25k events/leader | negative every year | **DEAD** |
| 17 | A15 — on-chain / stablecoin flow | `market_physics_v3/context`, `data/stablecoins` | one Bonferroni-surviving finding: depeg→BTC forward **volatility**, IC -0.30 (not returns) | overlay degrades existing 3-leg book | pre-existing frozen protocol | — | **NO_EDGE (already tested, closed)** — do not reopen |
| 18 | Post-listing momentum/reversion | `data/listings_backfill` | LONG net negative every cohort; SHORT fade real, +400 to +1,700bps | SHORT_REJECTED by standing project rule | 518 listings | consistent across 4 cohorts | **CLOSED (already tested)** — `ListingAgeGate` exists, defaults off |
| 19 | News sentiment → forward return | `data/news_raw` | r=0.15-0.18 | noise (n too small) | n=53 usable days | — | **DEAD** |
| 20 | `session_20260523_*` | — | — | — | — | — | **N/A** — not trading data (robotics teleop footage in the wrong folder) |

## Two structural bugs found (rule-12 "bugs masking edges") — one fixed, one still open

1. **Universe drift in the live `liq_cascade` shadow/weekly-retrain pipeline — FIXED, with a regression test.** `scripts/run_event_shadow_daily.py` and `scripts/train_event_engine.py` globbed `data/derivatives_backfill/binance_vision_metrics/*_metrics_5m.parquet` instead of reading the frozen 50-symbol universe (`configs/portfolio_v1_1_parallel_50.yaml`). An unrelated bulk backfill on 2026-08-14 (+222 files, from this branch's own `feat/free-derivatives-backfill` work) silently expanded the candidate universe to 312 symbols. **Fixed**: both scripts now call a `load_universe(root)` helper that reads only the frozen yaml, regardless of what's sitting in the metrics directory. **Regression test added**: `tests/test_universe_drift_guard.py` drops 300 synthetic extra files into a fake metrics dir and asserts the engine still resolves exactly the 50 frozen symbols — passes, along with all 14 pre-existing `test_liq_cascade.py`/`test_event_engines.py` tests (unaffected). The original contaminated ledger was **not deleted**: preserved verbatim at `w1_liq_cascade/shadow_legacy_contaminated.{parquet,log}` + `_state.json`. A clean reconstruction restricted to the frozen 50 is at `w1_liq_cascade/shadow_reconstructed_frozen50.parquet` (+ `_stats.json`) — see below for what it shows. `shadow_clean_after_patch` has no content yet by construction: the fix means every future daily cron run appends already-clean rows to the same `reports/liq_cascade/shadow/decisions.parquet` going forward — no separate ledger is needed.
2. **L2 reconstruction crossed-book bug in `market_physics_v3` raw capture** (still open, not fixed this round). Naively merging Binance/OKX's dedicated top-of-book stream with their separate full-depth diff stream produces a crossed book (bid≥ask) on 28-56% of ticks, because the two websocket streams are independently jittered and late messages resurrect retired price levels. Fix: use the dedicated top-of-book stream exclusively for price/qty; use the diff stream only for auxiliary signals. Bybit has no dedicated top-of-book stream and retains ~10-15% crossed ticks even after reconstruction. Anyone doing further microstructure work on this dataset needs this fix first — it directly caused A3's BLOCKED_DATA verdict on binance/OKX.

## SHORT_SQUEEZE sign — resolved without touching PnL to pick a winner

Git archaeology (no useful commit history — `detector.py` has a single "ok" commit — so resolved
via cross-referencing the actual production code path and prior standalone research instead):

- The production engine's **actual designed intent is continuation, not reversal**.
  `reports/liq_cascade/LIQ_CASCADE_ENGINE_REPORT.md` describes the model as a single
  `P(fwd_4h > cost)` classifier trained across **all** events regardless of `kind` — `kind` was
  built as a descriptive/diagnostic dimension (used in `DEEP_DIVE.md`'s "par type" table), never
  as a sign-flip instruction. Every production script (`dataset.py`, `train_liq_cascade_engine.py`,
  `train_event_engine.py`, `run_event_shadow_daily.py`) consistently uses raw `fwd_4h` for both
  kinds — this is uniform, not an oversight in one place.
- The reversal framing exists in exactly one place: `scripts/measure_bear_short_edges.py`, a
  **separate, standalone experiment** (part of the broader "audit mai 2026" SHORT investigation
  already closed under the project's standing `SHORT_REJECTED` policy) that explicitly names and
  tests `A_SHORT_SQUEEZE_FADE` — "après un short-squeeze..., shorter le rebond qui cale" — with
  its own correctly-sign-flipped PnL (`short_net = -fwd - cost`). Its result, already on record
  in `reports/liq_cascade/BEAR_SHORT_EDGES.json`: **n=11,286, PF 0.739, mean -28.6bps, verdict
  NO_EDGE** — reversal was tried on its own terms across the full 2021-2026 dataset and failed.
- Conclusion: this was never truly ambiguous once both code paths were read side by side —
  continuation is what the production model was built and trained to do, and reversal is not an
  untested alternative sitting there for the taking, it's an already-tried-and-rejected one.
  **A7-TAIL-E1 stays exactly as reported, sign unchanged**, per the instruction not to pick the
  side that improves PnL.
- The frozen-50 shadow reconstruction (see below) adds one more data point, taken for what it's
  worth given the tiny n: live, `kind`-split, in-universe-only performance is
  `LONG_CASCADE` n=63 PF 3.22 (+86.1bps) vs `SHORT_SQUEEZE` n=11 PF 0.08 (-99.4bps) — i.e. on
  this very small live sample, continuation is *not* working for `SHORT_SQUEEZE` either. This
  doesn't argue for flipping the sign (n=11 is nothing against a historical n=11,286 already
  showing NO_EDGE the other way too); it argues that `SHORT_SQUEEZE` specifically may just not
  have a stable direction at all, and that finding #1's edge is really a **`LONG_CASCADE`-tail
  story**, not a squeeze story.

## Frozen-50 shadow reconstruction — does the "bug, not decay" explanation hold up?

Restricting the existing (unmodified) shadow ledger to the 50 frozen symbols, split across the
three windows you asked for (full breakdown by week/symbol/kind in
`w1_liq_cascade/shadow_reconstructed_frozen50_stats.json`):

| period | n | PF | net bps/trade | note |
|---|---:|---:|---:|---|
| 2026-07-10 → 07-31 | 9 | 1.40 | +17.5 | pre-drift, small n |
| 2026-08-01 → 08-13 | 4 | 5.33 | +30.2 | pre-drift, tiny n |
| **2026-08-14 → 08-29** | **61** | **2.27** | **+66.5** | **post-drift date, still strongly positive** |

All three windows are net positive for the frozen universe — including the two weeks *after* the
contamination started, which is the key test: if the frozen-50 engine had actually decayed on
2026-08-14, this window would look bad too, and it doesn't. This is real support for "bug, not
regime decay" (matches your prediction), though it comes with real caveats stated plainly: total
labelled n is only 74, weekly variance is large (one very strong week, `2026-W34`, n=57 PF 2.52,
carries most of the positive total; the most recent partial week, `2026-W35`, n=4 PF 0.06 is bad
but on a sample too small to read as anything), and per-symbol splits (36 symbols, most with 1-4
trades each) are not individually meaningful — several show `PF=inf` purely from having zero
losing trades on n=1-2.

## Calendar basis — downgraded after proper episode-declustering

The original "+360-680bps, dominates costs 20-50x" number was real arithmetic on real data, but
counted ~111-114 raw daily prints from the same handful of multi-week extreme-basis regimes as if
they were independent experiments. Redone with **one observation per contiguous regime episode**
(91-93 true episodes instead), the picture is much weaker: see row #2 above and
`w3_derivatives/ADDENDUM_calendar_basis_episode_decluster.md` for full detail. Net: k7d/k3d are
not statistically distinguishable from zero (p=0.42/0.25); k1d flips net-negative after a
realistic 15bps cost; ETH shows no edge at all (t=-0.01) once two catastrophic -570bps
"rich-keeps-getting-richer" episodes (2021-03, 2024-03) are counted as single events instead of
being diluted across dozens of correlated daily rows. True bid/ask/depth-based fill simulation,
as originally requested, is **not possible with any data that exists anywhere** for this
instrument (Binance never published historical order-book depth; the local quarterly-futures
files keep close price only, by explicit design of the backfill script) — flagged rather than
faked.

## Bottom line, updated

The single highest-leverage action from this session — fixing the universe-drift bug — is now
**done, tested, and shows real support for "bug, not decay"** on the (still small) frozen-50
shadow sample: all three sub-periods positive, including after the contamination date. The
`SHORT_SQUEEZE` sign question is resolved without cherry-picking (kept as-is; the alternative was
already tried and rejected). Calendar basis, which looked like the best new find last round, is
**downgraded** to a real-but-noisy, tail-risk-heavy lead once measured honestly — not dead, but
nowhere near the "beats costs by 20x" candidate it first appeared to be. A7-TAIL-E1 remains the
strongest single candidate, now with one fewer open question and one live (if thin) forward data
point in its favor. Next real gate for #1: let the now-fixed shadow accumulate more frozen-50 days
before any promotion talk; next real work for #2: decide whether it's worth a bounded, explicitly
network-touching re-backfill (hourly klines, no bid/ask attainable regardless) before investing
more analysis time.
