# W5 — EXECUTION COST LAYER — PREREGISTRATION

Written **before** running any test. Round 4, Alpha Hunt, 2026-09-03.
Author: worker W5_EXECUTION_COST_LAYER.

## 0. Framing (what this worker is and is NOT doing)

This worker does **not** look for a directional signal. Round 2 / W8 already returned
`DEAD` for "maker posting as a standalone alpha" and that verdict is **not re-litigated as a
directional claim**. The question here is orthogonal:

> What is the true, conditional round-trip execution cost of this project's strategies, and
> which of the ~146+ mechanisms judged in rounds 1-3 change verdict when the flat
> `net_bps = gross_bps - 14` convention is replaced by a measured, conditional cost model?

The deliverable of record is the **retrospective re-judgement table** (§H5 below). Everything
else exists to make that table defensible.

## 1. Datasets declared in advance

| id | path | span | role |
|---|---|---|---|
| D1 | `data/execution_probe/date=*/` | 2026-07-12 → 2026-09-03 (51 dates, 3.98M virtual orders, 15 symbols) | probe of passive-order outcomes |
| D2 | `data/microstructure_reduced/raw/{bbo,trades}` | 2026-08-31 → 2026-09-03 (3 venues x BTC/ETH/SOL) | **real** BBO w/ top-of-book sizes + signed trades |
| D3 | `/home/qbee/futur-data-v2/data/market_physics_v3/raw/book_events` | 2026-08-15..17, 08-28..29 | L2 depth (okx `books`, bybit `orderbook.50`, HL `l2Book`); binance is L1-only |
| D4 | `data/enriched/*_1h_enriched.parquet` | 50 symbols, multi-year | OHLC for a PIT high-low spread proxy |
| D5 | `data/events/liq_cascade_dataset.parquet`, `cascade_dataset.parquet` | multi-year | cascade event timestamps for the urgency test |

Read-only on all of the above. All intermediates go to scratch, none to `data/` or `reports/`.

## 2. Cost convention under test

Project convention: `net_bps = gross_bps - 14` round-trip
(= 2 x [5bps taker fee + 2bps slippage]). Stress: `- 28`.
Binance USDM VIP0 reference used throughout: **taker 5.0bps, maker 2.0bps one-way**.
Any deviation from these two numbers is stated where used.

## 3. Hypotheses and PRE-SET decision thresholds

### H1 — the probe's markout is mechanically negative by construction (INSTRUMENT AUDIT)

The probe fills a BUY iff `ask < limit` where `limit = bid_at_place`. At the fill instant this
forces `mid_fill < limit`, so the recorded markout **cannot be positive at t=0** and its floor is
approximately `-(half_spread + 1 tick)` in bps. If true, round 2 / W8's headline
("adverse selection dominates spread capture everywhere, maker is negative even at 0 fee")
is at least partly an artifact of the instrument, not a fact about the market.

- **Test**: OLS of `adv_bps_60s` on `spread_bps` (placement), pooled and within-symbol; and
  comparison of per-symbol mean markout against the mechanical floor `-(spread_bps/2 + tick_bps)`.
- **Pre-set threshold**: declare `CONFOUNDED_BY_CONSTRUCTION` if BOTH
  (a) the pooled slope on `spread_bps` is in `[-1.2, -0.3]` (i.e. markout tracks the half-spread),
  AND (b) cross-symbol Spearman(mean markout, -mean spread) > 0.7.
- If instead markout is roughly spread-independent, H1 is **rejected** and W8's reading stands.

### H2 — a queue-aware simulator on real BBO+trades gives a materially different maker economics

Build a **post-only queue simulator** on D2: at sampled times, join the touch; queue-ahead =
`bid_qty` at placement; decrement by aggressive volume printed **at that price level**; fill when
queue-ahead is exhausted; cancel on TTL. Markout measured from the **fill price** at
1s / 10s / 60s / 300s. This admits *benign* fills, which D1 structurally excludes.

- **Pre-set threshold**: "materially different" = mean markout at 60s differs from the probe's by
  **> 1.0 bps** for the same symbol/period, or the sign of `half_spread + markout - maker_fee` flips.
- **Pre-set threshold for "maker is usable as a cost layer"**: the queue-aware
  `E[cost_maker_oneway] = maker_fee - half_spread - E[markout_60s | fill]` must be
  **< 5.0 bps (the taker one-way all-in)** on at least one liquid symbol, with the conclusion
  stable across the 4 available days. Otherwise maker is NOT a cost improvement and the
  `-14` convention is not too conservative.

### H3 — round-trip cost is size-dependent; a capacity curve exists

From D2 top-of-book notional and D3 L2 depth, compute walk-the-book cost for notional
$1k / $10k / $100k / $1M / $5M, per symbol/venue.

- **Pre-set threshold**: report the notional at which one-way walk-the-book slippage exceeds
  **2 bps** (the project's assumed slippage) — this is the project's true capacity per clip.
  If that notional is below **$100k** on majors, the `-14` convention is optimistic for size.

### H4 — execution cost degrades sharply during cascade events (URGENCY PENALTY)

Cascade-triggered alphas fire precisely when the book is worst. Join D5 cascade timestamps to
D1 (spread + fill outcomes, 7 weeks x 15 symbols) and D2 (real BBO, 4 days).

- **Pre-set thresholds**:
  - `SPREAD_SHOCK` if median `spread_bps` in the [0, +5min] window after a cascade is
    **> 1.5x** the symbol's own trailing baseline;
  - `MAKER_UNUSABLE_ON_EVENTS` if maker fill probability at 60s inside the event window drops by
    **> 20% relative** vs baseline, or the markout worsens by **> 3 bps**;
  - `URGENCY_PENALTY_MATERIAL` if the implied extra round-trip cost inside event windows is
    **> 5 bps** vs baseline.
- Declustering applies: cascade windows are collapsed to **independent episodes**
  (same-symbol / 24h), and effect sizes are reported per independent episode, never per raw row.

### H5 — retrospective re-judgement (CENTRAL DELIVERABLE)

Take every mechanism from rounds 1-3 (`alpha_hunt_2026-08-29`, `2026-08-30`,
`2026-09-01_round3`) and the validation round with a stated gross bps, and recompute the verdict
under the measured cost model instead of the flat 14.

- **Resurrection rule (pre-set)**: a mechanism is `RESURRECTION_CANDIDATE` iff
  `gross_bps - cost_realistic_rt > 0` **AND** `gross_bps - cost_stress_rt > 0`, where
  `cost_stress_rt = 1.5 x cost_realistic_rt`, AND its universe is one where the measured cost
  actually applies (i.e. the cost model was calibrated on comparable liquidity).
  A resurrection is **only** a candidate — it inherits every other gate failure it already had
  (ETA, declustering, year-concentration). A mechanism whose original kill reason was NOT cost
  is **not** resurrectable by this worker and is marked `KILL_REASON_NOT_COST`.
- **Death rule (pre-set)**: a currently-retained mechanism is `NEWLY_DEAD` iff
  `gross_bps - cost_realistic_rt <= 0` on its actual universe.
- **Pre-set honesty guard**: because maker execution requires *waiting*, a resurrection is only
  admissible for mechanisms whose holding period is **>= 1 hour** and whose trigger is **not**
  a shock requiring immediate execution. Event/cascade-triggered mechanisms are explicitly
  **barred from maker-based resurrection** and are instead re-judged with the H4 urgency penalty
  (i.e. they get *worse*, not better).

### H6 — PIT spread proxy for retrospective extension

The measured spread covers 15 symbols x 7 weeks. To re-judge mechanisms that traded a wider
universe over years, a proxy is needed. Test the Corwin-Schultz (2012) and Abdi-Ranaldo (2017)
high-low spread estimators on D4 1h bars against the measured spread from D1/D2.

- **Pre-set threshold**: usable iff cross-symbol Spearman(proxy, measured) **> 0.6** on the
  overlap window. If <= 0.6, the retrospective is stamped `DATA_LIMITED` outside the 15 probe
  symbols and no numeric re-judgement is issued for the rest — only a directional statement.

## 4. Declustering plan (gate §2, applied to cost claims too)

- **L1** same-symbol / 24h window.
- **L2** calendar day, all symbols.
- **L3** mechanism-natural macro unit: for cost claims, the **(symbol, UTC day)** cell for
  baseline statistics and the **independent cascade episode** for event statistics.
Every reported cost difference carries `n_raw` and `n_independent_L1/L2/L3`, a
block-bootstrap CI95 with blocks = calendar day, and a t-stat computed on **daily** means
(not on raw orders — 4M correlated orders is not N=4M).

## 5. Declared limitations (stamped in advance, not discovered later)

1. **The probe is virtual.** It is not in the book. It has no queue position, no latency, no
   post-only rejection, and no market impact. Its "fill" is a price-traversal event.
   Direction of bias is *derived in H1*, not assumed.
2. **7 weeks / 4 days = mono-regime.** Everything from D1/D2/D3 is stamped `DATA_LIMITED`
   for regime coverage. No year-by-year decomposition is possible on cost data; the
   `year_by_year` and `ex_best_year` gate columns are marked `N/A_COST_LAYER` with a reason,
   and are reported for the *mechanisms* being re-judged, not for the cost measurement.
3. **15 probe symbols, 3 microstructure symbols.** Cost for the rest of the universe is proxy
   or DATA_LIMITED (H6 decides which).
4. **No real fee schedule is observable.** VIP0 (5/2 bps) is assumed; a rebate tier would only
   improve maker and is reported as a sensitivity, never as the base case.
5. **A cost model cannot resurrect an ETA failure.** Any mechanism whose round-3/validation kill
   reason was `UNCONFIRMABLE_IN_HORIZON` stays dead regardless of cost.

## 6. What would make me report a negative result

If H2 shows queue-aware maker economics are no better than taker after adverse selection, then
the `-14` convention is **correct or too generous**, no mechanism is resurrected, and the
central deliverable becomes a list of `NEWLY_DEAD` mechanisms. That is an acceptable and
expected outcome and will be reported as the headline if it is what the data says.
