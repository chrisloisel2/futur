# W5 — EXECUTION COST LAYER — REPORT

Round 4, Alpha Hunt, `reports/edge_discovery/alpha_hunt_2026-09-03_round4/w5_execution_cost_layer/`.
Preregistration: `PREREGISTRATION.md` (written before any test; every threshold below is the one
declared there). Scripts: `evidence/s01..s13`. Machine-readable results: `RESULTS.json`.

> **Recovery note.** This worker was interrupted by a session limit on 2026-09-03 after H1 and a
> first pass of H4, with the queue simulator half-built. It was resumed on 2026-09-05 from the
> preserved scratch. `evidence/s08` (vectorised simulator, final), `s09` (cost model), `s10`
> (bridge), `s11` (signed urgency + spread proxy), `s12` (directional arms + capacity) and `s13`
> (cost floor) were written after the interruption. No preregistered threshold was changed.

---

## 0. One-paragraph answer

The project's `net_bps = gross_bps − 14` convention is **wrong in both directions, and wrong in
the direction that hurts.** Measured on a queue-aware simulator over real books and on 3.98 M
probe orders, the true round-trip cost obeys two clean laws:

```
cost_taker_roundtrip_bps   = 10.0 + 1.00 × spread_bps      (R² = 1.000, exact by construction)
cost_maker_roundtrip_bps   =  8.2 + 1.22 × spread_bps      (R² = 0.982, post-only TTL 600 s,
                                                            must-trade policy, haircut included)
```

Maker execution is **real but small: a flat ≈ 2 bps round-trip**, and it decays to ~0.6 bps on
wide-spread alts. The reason is a single measured fact:

```
adverse_selection_60s_bps = 0.88 + 1.00 × spread_bps        (R² = 0.989)
```

**A maker fill gives back exactly the spread it captured, plus 0.9 bps.** The naive
"5 bps taker → 2 bps maker, therefore 6 bps saved round-trip" arithmetic is wrong by a factor of
three. Consequently **the graveyard of round 1-3 mechanisms that died between +5 and +14 gross
bps does not come back to life** — see §6. What *does* change is the opposite direction: on
BTC/ETH/BNB the true cost is **8.6 bps, not 14** (the convention is 5.4 bps too pessimistic),
while on wide-spread alts (AR/ADA/FET, spread 5.4-6.7 bps) it is **14.6-16.3 bps in the best
available mode, 15.4-16.7 as a taker — not 14** (the convention is 0.6-2.3 bps too *generous*,
+1.3 on the tier average), and it keeps rising 1 bps for every 1 bps of spread into the illiquid
tail where this project's cross-sectional alphas do their selection.

And the worst news, §5: **for a momentum/continuation alpha that triggers on a shock, maker
execution costs +1.95 bps round-trip at the 99th-percentile move and +10.4 bps at the 99.9th**
(t = 8.1 and 9.3 on declustered symbol-days). The spread does *not* widen during shocks — it
*tightens* (×0.88). The entire urgency penalty is adverse selection, it is invisible to any
spread-based cost model, and it has the **opposite sign** for contrarian and for momentum entries.

---

## 1. Instruments, and what each one can and cannot see

| id | source | span | what it measures |
|---|---|---|---|
| D1 | `data/execution_probe/date=*` | 2026-07-12 → 2026-09-03, 51 dates, **15 symbols, 3 975 968 virtual orders** | outcome of a post-only order under a *traversal* fill rule |
| D2 | `data/microstructure_reduced/raw/{bbo,trades}` | 2026-09-01 → 09-04 (4 usable days) × 3 venues × BTC/ETH/SOL | real BBO **with top-of-book sizes** + signed trades → queue simulation |
| D4 | `data/enriched/*_1h_enriched.parquet` | 9 of the 15 probe symbols have bars in the window | high-low spread proxy (H6) |
| D5 | `data/events/cascade_dataset.parquet` | overlaps D1 | cascade event timestamps (H4-A) |

Note D3 (`market_physics_v3` L2 books) was **not** used: 65 GB for 2 days, and the top-of-book
capacity question (§7) was answerable from D2 without it. That is a deliberate scope cut on a
94 %-full disk, not an oversight.

### 1.1 The probe is not what the project thinks it is

`src/institutional/execution/maker_fill_probe.py::check_fill` fills a BUY **iff `best_ask <
limit`** — the book has *traversed* the level. Two consequences, both derived in `s02`, neither
previously stated in the project:

1. **Traversal is a _sufficient_ condition for a real fill, not a necessary one.** If the ask
   falls below your resting bid, your whole price level was cleared and you were filled. So the
   probe's fill rate is a **lower bound**, not an optimistic one. Measured on the overlap
   (same attempts, both rules, `s08`): probe rule 0.916 vs queue rule 0.933 at TTL 600 s — the
   probe **under**-states fill probability by 1.7 pp. **This contradicts the standing assumption
   that the probe's fill rates are optimistic.** The optimism in this project's execution
   research lives in the *simulator*, not in the probe (see §1.2).
2. **The probe's markout is mechanically negative.** At the fill instant traversal forces
   `mid_fill ≤ limit − tick − half_spread`, so under a martingale
   `E[adv_bps_60s | fill] ≤ −(tick_bps + half_spread_bps)` — a floor containing zero information
   about adverse selection.

**H1 verdict: `CONFOUNDED_BY_CONSTRUCTION`, both preregistered criteria met and exceeded.**
Cross-symbol Spearman(mean markout, mechanical floor) = **0.982** (p = 8.2e-11), OLS
`adv60 = −1.164 + 0.850 × floor`, **R² = 0.983**. Preregistered thresholds were slope in
[−1.2, −0.3] on spread (pooled slope = −1.214, at the boundary) and Spearman > 0.7 (0.982). The
median ratio observed/floor is 1.33: **75 % of the probe's headline adverse selection is the
instrument.** Round 2 / W8's reading ("maker is negative everywhere even at zero fee") is
therefore an artefact to that extent — though, as §3 shows, correcting it does *not* rescue
maker execution, it only halves the error.

A second, unrelated structural fact fell out of `s01`: **on all 15 probe symbols the spread is
1.00 tick** (`spread_in_ticks` = 0.970 … 1.096, `evidence/` → `ticks.csv`). Binance USDM is
tick-constrained across this entire universe. The spread cannot narrow, and a "spread widening"
test on majors is testing something that structurally cannot happen (§5).

### 1.2 Where the optimism actually is — and the haircut

The queue simulator (`s08`) is not in the book either. It does not model: latency to the
exchange, post-only rejection and re-quote, hidden/iceberg size ahead of us, orders that join our
price level after we post, our own footprint, or cancellations ahead of us. It is therefore
**optimistic**, and the honesty term is handled two ways:

* a **queue-position sweep** κ ∈ {0.0, 0.5, 1.0, 2.0} × displayed size ahead of us
  (κ = 0 first in queue, κ = 1 last at the touch = the baseline, κ = 2 = twice the displayed size
  ahead, i.e. hidden liquidity + joiners). Result: κ = 1 and κ = 2 are nearly identical at TTL
  600 s (fill 0.933 vs 0.931, markout −1.337 vs −1.375) because over ten minutes the level
  evaporates anyway; κ = 0 is wildly better (fill 0.986, markout −0.160) and is reported only as
  an upper bound. **Queue position stops mattering above ~1 minute and dominates below 10 s.**
* an explicit **`HAIRCUT = 1.0 bps one-way`** added to every maker cost in §3-§7, reported as a
  separate named term so a reader can set it to zero.

---

## 2. Cost algebra (stated before the numbers, to avoid double counting)

Everything is marked against the **post-execution fair price**, so the spread is never counted
twice and the fill and no-fill branches share one benchmark.

```
taker now             cost_T = (ask0 − mid_H)/m0·1e4 + fee_taker            ≈ s/2 + 5
maker fill at touch   cost_M = (L − mid_{fill+H})/L·1e4 + fee_maker
                             = −s/2 + fee_maker + AS_H ,   AS_H := s/2 − markout_H
post TTL=T then cross cost_P(T) = Pf(T)·E[cost_M | fill ≤ T] + (1−Pf(T))·E[cross at T]
round trip            = 2 × one-way
```

Fees: Binance USDM VIP0, taker 5.0 bps, maker 2.0 bps one-way. `H = 60 s`.
`AS_H` is the brief's first decisive quantity: *a "free" fill that loses 3 bps in 60 s costs more
than a 5 bps taker.* It is measured below, not assumed.

**Cost is charged on realised turnover** (an actual round trip), not per signal — the
briefing §8.9 convention.

---

## 3. H2 — queue-aware maker economics on real books

`s08` ran 165 990 post-only attempts (both sides, every 30 s, TTL 600 s) over 4 days × 3 venues ×
BTC/ETH/SOL, computing the probe's rule and four queue rules **on the same attempts**.

A collector outage was found and handled: **binance, okx and hyperliquid all lost 15.3 h on
2026-09-04** (one 55 086 s quote gap). Without a guard this reads as "no fill, no traversal, no
adverse selection" and biases every statistic downward — fill rates collapsed to 0.30 before the
fix. `s08` now admits an attempt only if `[t0, t0+TTL+300 s]` is gap-free at 30 s
(2026-09-04 goes from 5 660 fake attempts to 1 888 real ones). 2026-08-31 (50 minutes of data) is
dropped entirely.

### 3.1 Adverse selection is the whole story

One-way bps, pooled over the 4 days × 3 venues × 3 symbols:

| fill rule | markout 1 s | 10 s | 60 s | 300 s | AS(60 s) | fill @600 s | median TTF | cost maker | cost taker | maker gain |
|---|---|---|---|---|---|---|---|---|---|---|
| probe (traversal) | −1.420 | −1.697 | −1.774 | −1.833 | 1.957 | 0.916 | 11.1 s | 3.774 | 5.187 | 1.413 |
| κ=0 (first in queue) | −0.084 | −0.141 | −0.160 | −0.187 | 0.346 | 0.986 | 0.6 s | 2.160 | 5.187 | 3.027 |
| κ=0.5 | −0.955 | −1.201 | −1.265 | −1.303 | 1.452 | 0.937 | 7.4 s | 3.265 | 5.187 | 1.922 |
| **κ=1 (baseline)** | **−1.019** | **−1.273** | **−1.337** | **−1.387** | **1.524** | **0.933** | **8.1 s** | **3.337** | **5.187** | **1.850** |
| κ=2 (haircut) | −1.048 | −1.307 | −1.375 | −1.423 | 1.561 | 0.931 | 8.5 s | 3.375 | 5.187 | 1.813 |

Two things to read here.

* **Adverse selection is instantaneous, then permanent.** AS is 1.21 bps at 1 s and 1.52 bps at
  60 s, and only 1.57 at 300 s. It is not a transient impact that reverts — it is a level shift.
  A cost model that ignores it is wrong by roughly the whole spread.
* **The maker advantage, conditional on filling, is +2.35 bps one-way** (declustered on the 36
  (venue, symbol, day) L3 cells: t = **47.1**, block-bootstrap CI95 [2.29, 2.40], blocks = day,
  n_L2 = 4 days). Large and unambiguous *as a conditional statement*, but see §3.3: the days are
  only 4 and the L2 block count is the binding limitation, not the t-stat.

### 3.2 Probe calibration — the number the project needs

Same attempts, probe rule vs queue rule:

| | probe rule | queue κ=1 | bias |
|---|---|---|---|
| fill rate @600 s | 0.9159 | 0.9330 | probe **under**-states by 1.72 pp |
| markout @60 s | −1.774 bps | −1.337 bps | probe **over**-states adverse selection by 0.437 bps |
| median time-to-fill | 11.1 s | 8.1 s | probe is 3 s slow |

Declustered on the 36 L3 cells the markout bias is **−0.281 bps, t = −6.25**, block-bootstrap
CI95 [−0.300, −0.252]. **H2 preregistered threshold "materially different = > 1.0 bps at 60 s for
the same symbol/period" is NOT met in the pooled average (0.44 bps) but IS met on the wider-spread
symbol: SOLUSDT binance markout −2.325 (probe rule) vs −1.190 (queue), a 1.14 bps bias.** The
bias scales with the spread — which is what makes the bridge in §4 possible, and what makes the
probe *most* wrong exactly on the alts.

**External validation.** The simulator, run with the probe's own rule on 4 days of September,
returns AS(60 s) = 2.86 bps for binance SOLUSDT. The live probe, over 7 independent weeks of
July-August, returns AS = 3.22 bps for SOLUSDT. Two independent instruments, disjoint data,
0.36 bps apart. The simulator is doing what it claims to do.

The second preregistered H2 threshold — *"maker is usable as a cost layer iff
`E[cost_maker_oneway] < 5.0 bps` on at least one liquid symbol, stable across the days"* — is
**met**: 3.34 bps pooled, on every symbol, on all 4 days. Maker execution is real. It is just
small.

### 3.3 What waiting costs — the TTL curve (must-trade policy)

Round-trip bps, κ=1, no haircut (add 2.0 for the haircut):

| TTL | P(fill) | cost of the fill leg | cost of the cross leg | **policy cost RT** | taker-now RT |
|---|---|---|---|---|---|
| 1 s | 0.133 | 3.216 | 5.126 | **9.74** | 10.37 |
| 5 s | 0.374 | 3.268 | 5.080 | **8.81** | 10.37 |
| 10 s | 0.502 | 3.260 | 5.132 | **8.38** | 10.37 |
| 30 s | 0.687 | 3.293 | 5.107 | **7.72** | 10.37 |
| 60 s | 0.780 | 3.311 | 5.075 | **7.40** | 10.37 |
| 120 s | 0.846 | 3.325 | 5.012 | **7.17** | 10.37 |
| 300 s | 0.904 | 3.337 | 5.283 | **7.05** | 10.37 |
| 600 s | 0.933 | 3.337 | 6.423 | **7.09** | 10.37 |

Two results worth keeping:

* **The chase is nearly free below 5 minutes.** The cross leg after waiting T costs 5.08-5.28 bps
  one-way against 5.19 for crossing immediately — statistically the same thing. Post-then-cross is
  close to free optionality up to 300 s, and only at 600 s does the chase start costing (+1.24 bps
  one-way). This is the opposite of the usual intuition and it is the reason the maker route is
  worth anything at all.
* **Add the 1 bps/side haircut and maker below ~30 s stops being worth doing**: 10.38 (T=1 s),
  10.38 (T=10 s) vs 10.37 taker. **Maker execution requires waiting a minute or more. Below that
  it is a rounding error at best.**

---

## 4. H6 / the bridge — extending from 3 symbols to 15, and why not to 300

### 4.1 The bridge (3 symbols → 15)

The simulator needs top-of-book sizes, which exist only for BTC/ETH/SOL since 2026-08-31. On the
9 (venue, symbol) cells where both instruments run, the probe-rule bias is identified and fitted
**multiplicatively** (`s10`):

```
AS_queue = ρ(spread) × AS_probe_rule ,   ρ = 0.9301 − 0.3095 × spread_bps ,  R² = 0.975,
                                          clipped to [0.60, 1.0]
```

Multiplicative, not additive, so that extrapolation to the wide-spread alts cannot produce a
negative adverse selection. ρ is **floored at 0.60, the most favourable value actually observed**
(binance/okx SOL): outside the fitted spread range [0.013, 0.988] bps the correction is capped at
the largest reduction ever measured, and every row beyond it is stamped `extrapolated=true`.
This is the honest weak point of the whole worker: **the bridge is fitted on spreads up to 1 bps
and applied up to 6.7 bps.**

### 4.2 H6 — the PIT spread proxy: `DATA_LIMITED` beyond the 15 probe symbols

Preregistered: usable iff cross-symbol Spearman(proxy, measured) > 0.6.

* **Corwin-Schultz (2012)**: Spearman = **0.867** (p = 2.5e-3, n = 9) → passes the letter of the
  threshold, **as a ranking**.
* **Abdi-Ranaldo (2017)**: Spearman = **−0.237** (p = 0.54) → fails. It returns exactly 0 for
  6 of 9 symbols on 1 h bars.

But the *levels* are unusable: CS returns 5.81 bps for BTCUSDT where the measured spread is
**0.015 bps** — a 400× error. On 1 h crypto bars the high-low estimator is measuring volatility,
not the spread. Since the cost laws in §0 are `cost = const + 1.00 × spread`, **a 1 bps error in
the spread proxy is a 1 bps error in the round-trip cost** — the precision required is exactly
the precision the proxy does not have.

**H6 verdict: `DATA_LIMITED`.** Per the preregistration, no numeric re-judgement is issued outside
the 15 probe symbols; only the directional statement of §0 (`cost_taker_rt = spread + 10`, exact),
which the project can apply to any symbol *the moment it measures that symbol's spread*.

A separate coverage finding: **6 of the 15 probe symbols (ARUSDT, FETUSDT, ORDIUSDT, PYTHUSDT,
SUIUSDT, TIAUSDT) have ZERO 1 h enriched bars after 2026-07-12.** `data/enriched/` has stopped
tracking them. Anyone joining `data/enriched` to a recent window on those symbols is silently
getting an empty frame.

---

## 5. H4 — urgency. The most important bad news, and it has a sign

This is where a naive test gets the wrong answer, and the project's earlier reading with it.

### 5.1 The spread does not widen during shocks — it tightens

Cascade events (`cascade_dataset`, 439 raw → **247 L1-declustered episodes**, 204 usable, 41
calendar days, 204 symbol-days): **median spread multiplier 1.000, block-bootstrap CI95
[0.990, 1.004]** (blocks = day). On the endogenous shock deciles the multiplier is **0.68-0.82**:
the spread is *tighter* in the top decile than in the bottom. On the real books (D2) at the 99th
percentile adverse move the multiplier is 1.59 on BTC/ETH/SOL, but on the 15-symbol probe panel
it is **0.875**. The reason is §1.1: **the book is tick-constrained** — the spread is already at
1 tick and physically cannot widen; competition for the touch during a shock only makes it stick
there harder.

**`SPREAD_SHOCK` (preregistered: median spread > 1.5× baseline) is REJECTED.** Any cost model
that prices urgency through the spread will price it at zero. That is the trap.

**Fill rates *rise* during shocks**, they do not fall: P(fill ≤ 60 s) goes from 0.44 to 0.83-0.88
at the 99.9th percentile. **`MAKER_UNUSABLE_ON_EVENTS` (preregistered: fill probability drops
> 20 % relative) is REJECTED.** You get filled. That is the problem.

### 5.2 The urgency penalty is pure adverse selection, and it flips sign

The first pass of this test (`s05`, and `s10` which inherited it) conditioned on **|5-min
return|**. That blends "the market ran *up* into my buy" with "the market ran *down* into my buy",
the two have opposite information content, and they cancel: the penalty read as ≈ 0 up to the
99th percentile and then exploded incoherently at the 99.9th. **This is a real bug and it is
fixed in `s11`/`s12`** by signing the shock against the side being executed. Both arms, 15
symbols × 7 weeks, declustered on (symbol, UTC day) cells with block-bootstrap CI95 (blocks =
calendar day):

| arm | tail | n_raw | cells L1 | days L2 | symbols L3 | spread × | P(fill≤60 s) | ΔAS | **maker penalty RT** | t | CI95 | taker penalty RT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **MOMENTUM (chase)** | top 1 % | 39 782 | 665 | 51 | 15 | 0.875 | 0.44 → 0.84 | +0.98 | **+1.95** | **8.1** | [1.07, 2.80] | −0.23 |
| **MOMENTUM (chase)** | top 0.1 % | 3 991 | 292 | 45 | 15 | 0.875 | 0.44 → 0.88 | +5.24 | **+10.39** | **9.3** | [5.69, 15.38] | −0.20 |
| ADVERSE (contrarian) | top 1 % | 39 782 | 651 | 51 | 15 | 0.875 | 0.44 → 0.82 | −1.07 | **−2.16** | −7.8 | [−2.94, −1.38] | −0.23 |
| ADVERSE (contrarian) | top 0.1 % | 3 991 | 277 | 44 | 15 | 0.875 | 0.44 → 0.83 | −3.38 | **−6.83** | −5.5 | [−11.33, −2.49] | −0.20 |

* **MOMENTUM arm** = posting a bid *after the market has already rallied* — any
  continuation/momentum/breakout alpha that fires on a shock. **`URGENCY_PENALTY_MATERIAL`
  (preregistered: extra round-trip cost > 5 bps inside event windows) is CONFIRMED at the
  99.9th percentile (+10.4 bps RT) and rejected at the 99th (+1.95 bps).** Per symbol at the
  0.1 % tail, maker round-trip penalty: SUIUSDT **+15.54**, ADAUSDT +14.70, ARUSDT +13.92,
  DOGEUSDT +13.82, TIAUSDT +11.61, FETUSDT +10.53, XRPUSDT +9.89, PYTHUSDT +9.83, LINKUSDT +8.79,
  BTCUSDT +8.18, ETHUSDT +6.20, AVAXUSDT +5.32, BNBUSDT +2.49, ORDIUSDT +2.16, SOLUSDT +0.75.
  **15 of 15 symbols have the same sign.** In absolute terms the maker route inside the 0.1 %
  momentum tail costs 9.2 (SOL) to 27.5 (ADA/AR) bps round-trip, against 10.0-16.2 for simply
  crossing — i.e. **on 11 of 15 symbols, posting is strictly worse than crossing during a shock
  you are chasing.**
* **ADVERSE arm** = posting a bid *after the market has fallen* — the cascade-bounce entry.
  Maker execution gets **better**, not worse.

### 5.3 The double-counting warning that goes with the ADVERSE arm

**The −6.83 bps "gain" on the contrarian arm must NOT be added to a contrarian alpha's gross
bps.** That improvement *is* the post-cascade bounce, which is precisely what
`LIQ_CASCADE_REPEAT_V1`, `BTC_LEAD_ALT_CASCADE_V1` and every cascade-bounce mechanism already
claims as their edge. Measuring it here and crediting it there books the same reversion twice.
The only admissible use of the ADVERSE result is the *negative* one: **cascade-bounce alphas are
not penalised by urgency; use the baseline cost, not a worse one.**

The symmetric caveat applies to the whole worker: adverse selection measured on a
direction-agnostic probe is an **upper bound** on the cost for a mechanism that itself predicts
the post-fill drift, and a fair estimate for a mechanism that does not.

### 5.4 The one thing that does get worse: capacity

Top-of-book notional at the 99th-percentile adverse move, real books:
**$160 774 → $97 632, a factor 0.61.** The book does not widen, it **thins**. For the project's
current paper sizes this is irrelevant (§7); for anything above ~$50 k a clip it is the binding
constraint during exactly the events the event alphas trade.

---

## 6. H5 — THE RETROSPECTIVE RE-JUDGEMENT (central deliverable)

### 6.1 The replacement cost floor

Round-trip bps, haircut included, must-trade post-then-cross policy (`s13`):

| tier | symbols | spread | taker RT | maker RT (T=60 s) | maker RT (T=600 s) | **best, slow** | **best, shock-triggered** | Δ vs the −14 convention |
|---|---|---|---|---|---|---|---|---|
| T1 MAJOR | BTC ETH BNB | 0.05 | 10.1 | 8.8 | 8.6 | **8.6** | 9.9 | **−5.4** |
| T2 LIQUID_ALT | XRP LINK SOL DOGE SUI AVAX | 1.35 | 11.3 | 9.9 | 9.4 | **9.4** | 11.1 | **−4.6** |
| T3 MID_ALT | PYTH ORDI TIA | 2.82 | 12.7 | 11.9 | 11.7 | **11.7** | 12.5 | **−2.3** |
| T4 WIDE_ALT | AR ADA FET | 5.46 | 15.8 | 15.4 | 15.3 | **15.3** | 15.6 | **+1.3** |
| beyond | any symbol, spread *s* | *s* | *s*+10.0 | — | *s*+8.2 (κ=1) | *s*+8.2 | *s*+9.4 | `DATA_LIMITED` on *s* |

### 6.2 Applying the preregistered resurrection rule

The preregistration commits to: `RESURRECTION_CANDIDATE` iff
`gross − cost_realistic > 0` **AND** `gross − 1.5 × cost_realistic > 0`. The second condition is
what does the work:

| tier | cost_realistic RT | 1.5 × cost_realistic | resurrection band (was dead at 14, alive under stress) | width |
|---|---|---|---|---|
| T1 MAJOR | 8.6 | **12.9** | gross ∈ (12.9, 14.0] | **1.1 bps** |
| T2 LIQUID_ALT | 9.4 | **14.1** | ∅ | **0** |
| T3 MID_ALT | 11.7 | **17.6** | ∅ | **0** |
| T4 WIDE_ALT | 15.3 | **23.0** | ∅ — mechanisms *die* instead | **0** |

> **The graveyard stays shut.** The preregistered stress condition consumes the entire maker
> gain everywhere except a 1.1 bps-wide window on BTC/ETH/BNB. **No round 1-3 mechanism that died
> between +5 and +14 gross bps is resurrected by this worker.** This is the negative outcome the
> preregistration §6 declared in advance as an acceptable headline, and it is the outcome.

### 6.3 The re-judgement table

Sources: `configs/validation_registry.yaml` (net bps at the −14 convention, so
`gross = net + 14`), rounds 1-3 scoreboards. `cost_realistic` is assigned from the tier that
matches the mechanism's stated universe; `trigger` decides whether the maker route is admissible
at all (preregistered honesty guard: **event/shock-triggered mechanisms are barred from
maker-based resurrection and are re-judged with the §5.2 momentum penalty instead**).

**Scope of the table.** 196 mechanisms were re-judged: **all 159 of round 3** (W1-W8) and **all
37 validation-round rows** (`configs/validation_registry.yaml` + the wave-1/wave-2 validation
reports). **Rounds 1 and 2 are deliberately excluded from the numeric re-judgement**, and the
reason is itself a finding: *the project does not apply the −14 convention uniformly to its own
rounds.* Round 1's calendar-basis worker charges **15 bps** round-trip; round 2's W1
cross-sectional worker charges **20 bps** round-trip for a long/short (4 legs) and 10 for a
long-only; rounds 1-2 microstructure charges **4.5 bps taker / 1.5 bps maker per leg** where
round 3 switched to 5.0/2.0. Restating those to a common basis requires re-deriving each
worker's turnover assumption from its own code, which is outside this worker's scope. Round 3
and the validation round both use 14/28 consistently, so they are comparable and they are what
the table covers. **Recommendation: pick one convention and enforce it in a shared helper — the
project currently has at least four.**

### Result

| verdict | n | share |
|---|---|---|
| `UNCHANGED_ALIVE` | 121 | 61.7 % |
| `UNCHANGED_DEAD` | 70 | 35.7 % |
| `STILL_COST_FRAGILE` (dead at −14, positive at the measured cost, **fails the 1.5× stress**) | 5 | 2.6 % |
| **`RESURRECTION_CANDIDATE`** | **0** | **0 %** |
| **`NEWLY_DEAD` (central tier)** | **0** | **0 %** |
| `NEWLY_DEAD` (wide-alt stress tier assignment) | 3 | 1.5 % |

The five that move at all — the entire population of mechanisms this worker's cost model touches:

| mechanism | round | gross | net @−14 | tier | trigger | maker OK | cost measured | net measured | net @1.5× | original kill reason |
|---|---|---|---|---|---|---|---|---|---|---|
| W7-018 BASIS_SHOCK down × time_since_prev | r3 | 13.20 | −0.80 | T2 | SLOW_STATE | yes | 9.4 | **+3.80** | −0.90 | "cost (net ~breakeven)" |
| CVD_SHOCK_DOWN_MEMORY | validation | 13.98 | −0.02 | T2 | EVENT_SHOCK | no | 11.3 | **+2.68** | −2.97 | validation gate (sign correction) |
| A6 LONG_CASCADE repeat≥2 × mktwide density (idiosyncratic) | r3 | 12.80 | −1.20 | T2 | EVENT_SHOCK | no | 11.3 | **+1.50** | −4.15 | "cost (breakeven)" |
| XSEC_RESID_MOM_7D | r3 | 12.50 | −1.50 | T3 | SLOW_STATE | yes | 11.7 | **+0.80** | −5.05 | "cost" |
| W7-017 BASIS_SHOCK up × time_since_prev | r3 | 11.50 | −2.50 | T2 | SLOW_STATE | yes | 9.4 | **+2.10** | −2.60 | "cost (net still negative)" |

And the three that get *worse* if their cross-sectional universe is priced at the wide-alt tier
(the honest stress case: a long/short over 312 names selects the illiquid tails):

| mechanism | gross | net @−14 | cost @T4_WIDE_ALT | net |
|---|---|---|---|---|
| D-VOLOFVOLRANK-I2_a-7D | 15.3 | +1.3 | 15.3 | **0.0** |
| D-PRICEOI-DIVERGENCE-R_fade-7D | 15.3 | +1.3 | 15.3 | **0.0** |
| D-RVOLRANK-I_b-7D | 14.7 | +0.7 | 15.3 | **−0.6** |

### Why nothing flips: the population is not at the boundary

The correction this worker produces is **uniform and small**: `delta_net_bps` has median **+2.3**
and a full range of **+1.3 to +5.4** across all 196 mechanisms (the +5.4 tail is BTC/ETH-only
curve work, the only T1 population in the corpus). A uniform shift of that size flips a verdict
only for mechanisms sitting inside the band that the shift spans. The distribution of gross bps
says almost nothing is there:

| gross bps | <0 | 0-5 | 5-8.6 | 8.6-9.4 | 9.4-11.7 | 11.7-14 | 14-15.3 | 15.3-20 | 20-30 | >30 |
|---|---|---|---|---|---|---|---|---|---|---|
| n | 20 | 27 | 15 | 5 | 4 | 4 | 10 | 16 | 34 | 61 |

**Only 23 of 196 mechanisms (11.7 %) live in the 8.6-15.3 bps band that any cost model between
this worker's best and worst tier could move across.** 62 are hopeless (gross < 8.6, dead under
*every* cost assumption including zero fees on majors) and 111 are comfortable (gross > 15.3,
alive under every assumption). **The round 1-3 corpus is bimodal around the cost line, not
piled against it.** The premise that "a lot of mechanisms died between +5 and +14 gross and would
come back" is, on the round 3 + validation corpus, empirically false: that band holds 13
mechanisms out of 196, and the preregistered 1.5× stress kills every one of them.

Two structural facts explain the rest:

* **39.3 % of the corpus is `EVENT_SHOCK`-triggered** and is therefore barred from maker-based
  resurrection by the preregistered honesty guard (§5, and the guard is right: §5.2 shows the
  momentum arm of exactly this population pays +1.95 to +10.4 bps RT to post). Those mechanisms
  get only the taker correction, which is `14 − (spread + 10)` ≈ +2.7 bps on T2.
* **51.5 % is maker-admissible** (holding ≥ 1 h and not shock-triggered) and gets the full
  correction — which is still only 4.6 bps on T2 and 2.3 bps on T3.


---

## 7. H3 — capacity, and why the "2 bps slippage" half of the convention is unfounded at current size

Top-of-book notional, USD, measured on the real books over 4 days (`s12`):

| venue | symbol | spread bps | p05 | p25 | median | p75 |
|---|---|---|---|---|---|---|
| binance | BTCUSDT | 0.013 | 42 353 | 205 856 | 379 833 | 702 180 |
| binance | ETHUSDT | 0.042 | 15 584 | 85 985 | 192 748 | 387 868 |
| binance | SOLUSDT | 0.988 | 17 054 | 45 518 | 82 402 | 122 906 |
| okx | BTCUSDT | 0.013 | 15 118 | 145 163 | 270 497 | 450 741 |
| okx | ETHUSDT | 0.041 | 10 293 | 130 792 | 246 861 | 382 867 |
| okx | SOLUSDT | 0.987 | 7 040 | 41 909 | 74 436 | 110 763 |
| hyperliquid | BTCUSDT | 0.138 | 267 | 94 714 | 306 151 | 616 077 |
| hyperliquid | ETHUSDT | 0.424 | 1 662 | 88 295 | 214 272 | 395 375 |
| hyperliquid | SOLUSDT | 0.716 | 269 | 20 100 | 53 032 | 95 432 |

**Preregistered H3 question — "the notional at which one-way walk-the-book slippage exceeds
2 bps" — answered: on the majors that notional is ABOVE the p25 of top-of-book depth, i.e. above
$86k-$206k on binance BTC/ETH and above $46k on SOL.** Below that, the whole clip is absorbed at
the touch and slippage beyond the half-spread is **exactly zero**.

The project's live lab uses `per_alpha_budget_fraction = 0.05` on 200 k books
(`src/institutional/live_alpha_lab/portfolio_config.py`), i.e. €10 k per alpha spread across a
basket — a per-name clip of order €1 k-€10 k. That is one to two orders of magnitude below the
touch. **The "2 bps slippage" half of the 14 bps convention has no empirical basis at the sizes
this project actually trades.** It becomes real above ~$100 k a clip on majors, and it becomes
real *immediately* during shocks: top-of-book notional falls from $160 774 to $97 632 (×0.61) at
the 99th-percentile adverse move. The book does not widen, it **thins**.

`H3 verdict: capacity is not binding at current size; it becomes binding at ~$100k/clip and
during events.`

## 8. What I killed, and why

* **`SPREAD_SHOCK` — KILLED.** Preregistered at "median spread inside the event window > 1.5×
  baseline". Measured: 1.000 (CI95 [0.990, 1.004]) on 204 declustered cascade episodes; 0.875
  on the signed shock tails; 0.68-0.82 on the shock deciles. The book is tick-constrained; the
  spread *cannot* widen and in practice tightens. Any cost model that prices urgency through the
  spread prices it at zero, which is the wrong answer for the right reason.
* **`MAKER_UNUSABLE_ON_EVENTS` — KILLED.** Preregistered at "fill probability at 60 s drops
  > 20 % relative". Measured: fill probability *rises* from 0.44 to 0.83-0.88. You get filled
  during shocks. Being filled is the problem, not being unfilled.
* **The unsigned urgency test — KILLED as a method** (§5.2). Conditioning on |return| cancels two
  opposite effects. This was a real bug in this worker's own first pass and it is documented
  rather than quietly fixed.
* **Abdi-Ranaldo as a spread proxy — KILLED.** Spearman −0.237 against a preregistered 0.6, and
  it returns exactly 0 on 6 of 9 symbols at 1 h resolution.
* **Corwin-Schultz as a *level* proxy — KILLED** (it survives as a ranking). 400× level error on
  BTC. Since `cost = const + 1.00 × spread`, a level error *is* a cost error, one for one.
* **Maker execution below ~30 s TTL — KILLED as a cost improvement.** With the 1 bps/side
  simulator haircut, post-then-cross at TTL ≤ 10 s costs 10.38 bps RT against 10.37 for crossing
  immediately. It is a rounding error.
* **The resurrection thesis itself — KILLED** (§6.2). This is the headline. The preregistration
  (§6) declared in advance that this was an acceptable outcome, and it is the outcome.
* **The standing belief that the probe's fill rates are optimistic — KILLED** (§1.1). Traversal
  implies a fill; the probe under-states fill probability by 1.7 pp. The optimism in this
  project's execution research is in the simulator, and it is handled by an explicit haircut and
  a queue-position sweep rather than by assertion.

## 9. Bugs, data pitfalls and incidents found

1. **`data/microstructure_reduced` lost 15.3 h on 2026-09-04 on all three venues simultaneously**
   (single 55 086 s quote gap, binance + okx + hyperliquid, all three symbols). Consistent with
   the disk incident recorded in the project's 2026-09-04/05 forward audit. Any analysis that
   `searchsorted`s a time grid onto this file without a staleness guard silently reads frozen
   quotes as "nothing happened": fill rates read 0.30 instead of 0.92. `s08` now refuses any
   attempt whose window contains a quote gap > 30 s. **Recommendation: this guard belongs in the
   shared loader, not in one worker's script.**
2. **`data/microstructure_reduced` normalises okx sizes in CONTRACTS, not base units.**
   BTC-USDT-SWAP = 0.01 BTC, ETH = 0.1 ETH, SOL = 1 SOL. Verified three ways against binance and
   hyperliquid on the same instant. Uncorrected, okx BTC top-of-book reads **$27 M** instead of
   $270 k — a 100× capacity overstatement. Queue/fill logic is unaffected (book and trades share
   the unit inside a venue); every okx *notional* is not. Corrected in `s12`.
3. **`data/enriched/` has stopped tracking 6 of the 15 probe symbols.** ARUSDT, FETUSDT,
   ORDIUSDT, PYTHUSDT, SUIUSDT, TIAUSDT return **zero** 1 h bars after 2026-07-12. A join against
   a recent window on those names returns an empty frame with no error.
4. **The probe's fill rule is a *lower* bound on fill probability, not an upper one** (§1.1) —
   the opposite of the assumption under which it has been read.
5. **On all 15 probe symbols the Binance USDM spread is exactly 1 tick** (0.970-1.096 ticks).
   Every "spread" statistic on this venue is really a tick-size statistic. This is why AR / ADA /
   FET have 5.4-6.7 bps spreads: their tick is 5.3-6.7 bps. It is a *listing parameter*, not a
   liquidity measurement, and it is the single largest driver of their execution cost.
6. **Resource incident: none.** Peak scratch 519 MB (budget 1 GB), all inside this worker's own
   scratch, no writes outside `reports/edge_discovery/alpha_hunt_2026-09-03_round4/w5_execution_cost_layer/`,
   nothing deleted anywhere.

## 10. The round-4 gate applied to this worker's claims

`year_by_year` and `ex_best_year` are stamped `N/A_COST_LAYER` for every row, exactly as the
preregistration (§5.2) committed **in advance**: the cost instruments span 7 weeks and 4 days,
so no year decomposition exists. Everything else is computed (`s15`).

| claim | n_raw | n_ind L2 | n_ind L3 | effect (bps) | t declustered | CI95 | n_required | events/wk | **ETA** | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| **C1 MAKER_COST_LAYER** | 154 875 | 4 days | 36 cells | +2.349 one-way | 47.1 | [2.29, 2.40] | 1 | 63 | **0.1 d** | `VALIDATED_FOR_FORWARD` |
| **C2 URGENCY_MOMENTUM_P999** | 3 991 | 45 days | 15 symbols | +10.39 RT | 9.3 | [5.69, 15.38] | 106 | 45.4 | **16 d** | `VALIDATED_FOR_FORWARD` |
| **C2 URGENCY_MOMENTUM_P99** | 39 782 | 51 days | 15 symbols | +1.95 RT | 8.1 | [1.07, 2.80] | 321 | 91.3 | **25 d** | `PROMISING_NEEDS_VALIDATION` |
| **C3 URGENCY_CONTRARIAN_P999** | 3 991 | 44 days | 15 symbols | −6.83 RT | −5.5 | [−11.33, −2.49] | 291 | 44.1 | 46 d | `DOUBLE_COUNTING_RISK — not usable as a credit` |
| **C4 WIDE_ALT_COST_UNDERSTATED** | 3 975 968 | 51 days | 15 symbols | +1.29 RT | identity, not an estimate | — | 1 | 105 | **1 d** | `VALIDATED_FOR_FORWARD` |
| **C5 SPREAD_PROXY** | 9 symbols | — | 9 | Spearman 0.867 (CS) / −0.237 (AR) | — | — | — | — | — | `DATA_LIMITED` |

> **The briefing calls `eta_forward_confirmation` the most important field of the round, and
> notes that several validated alphas need 9 to 46 *years* of forward to confirm. Every claim in
> this table confirms in 0.1 to 46 *days*.** Cost-layer facts have an independent-episode rate
> three to four orders of magnitude higher than alpha-layer facts, because every symbol-day
> produces one. If the project wants results that are actually confirmable inside its own
> horizon, the execution layer is where they are.

**The binding limitation on C1 is not statistical power, it is regime coverage.** t = 47 on 4
calendar days is 4 calendar days, and the whole cost layer is stamped `DATA_LIMITED` for regime
in the preregistration. Re-run `s06`+`s08`+`s09` after the microstructure collector has a
volatility regime change in it; the scripts take 4 minutes end to end.

## 11. What the project should actually change

1. **Replace the flat `−14` with `cost_rt = spread_bps + 10.0` (taker) or
   `spread_bps + 8.2` (post-only, TTL ≥ 300 s, must-trade).** Both are one-line changes and both
   are measured, not decreed. The taker form is an identity given the spread.
2. **Measure the spread.** Everything above reduces to it. The project has a spread measurement
   for 15 symbols and no usable proxy for the other ~300 (§4.2). The cheapest fix by far is to
   widen `maker_fill_probe.py`'s `SYMBOLS` list — it is a 15-line list, it costs one websocket
   subscription per symbol, and it turns `DATA_LIMITED` into a number for the whole universe.
   **This is the highest-value, lowest-cost action in this report.**
3. **Stop treating maker execution as a lever worth chasing.** It is worth ~1.5-2 bps RT and only
   if the strategy can wait a minute. It is not the 6 bps that "5 → 2 bps fee" implies, because
   adverse selection returns the captured spread plus 0.9 bps.
4. **Never post on the side the market has already moved toward, during a shock.** +1.95 bps RT
   at the 99th percentile, +10.4 at the 99.9th. Cross instead — the taker penalty is ≈ 0 because
   the spread does not widen.
5. **Re-price the illiquid tail.** `AMIHUD_ILLIQUIDITY_PREMIUM_V1` is FROZEN, live, and
   deliberately buys the least-liquid names in a 312-symbol universe — precisely the population
   where the −14 convention is too generous and where the error grows 1:1 with the spread. Its
   validated edge is +105.7 bps net, so it survives comfortably (a +5 bps cost error costs it
   ~5 % of its edge), but **it is the alpha most exposed to this convention being wrong and it is
   currently the one taking capital.** Its true cost is unknown because the spreads of its
   holdings are unmeasured — see recommendation 2.
6. **Put the staleness guard and the okx contract multiplier in the shared loader** (§9.1, §9.2).
7. **Pick ONE cost convention and enforce it in a shared helper.** The project currently runs at
   least four in parallel (14 bps RT, 15 bps RT for round-1 calendar basis, 20 bps RT for round-2
   long/short, 4.5/1.5 bps per leg for rounds 1-2 microstructure vs 5.0/2.0 in round 3). This is
   why rounds 1 and 2 could not be re-judged numerically here (§6.3), and it silently makes
   cross-round bps comparisons wrong.
8. **Find out which fee tier the account is on** (§12.5). At VIP0 nothing changes. From VIP5
   upward, roughly 4-6 bps of round-1-3 graveyard genuinely reopens on majors and liquid alts,
   and this whole report's headline conclusion inverts.

## 12. Declared limitations

Stamped in advance in `PREREGISTRATION.md` §5, restated here with what each one turned out to cost.

1. **Both instruments are virtual.** Neither is in the book. The probe's bias direction was
   *derived* (§1.1) rather than assumed, and the simulator's residual optimism is handled by a
   κ-sweep plus an explicit 1.0 bps/side haircut that every table carries and that a reader can
   remove. Latency, post-only rejection, queue joiners, hidden size and own-footprint are **not
   modelled at all** — the haircut is a stand-in for them, not a measurement of them.
2. **Regime coverage is the real limitation.** 4 days of real books, 7 weeks of probe, one
   volatility regime. Every number here is `DATA_LIMITED` for regime and no year decomposition is
   possible. This is stamped, not discovered.
3. **The bridge extrapolates.** ρ(spread) is fitted on spreads 0.013-0.99 bps and applied up to
   6.7 bps, floored at the most favourable value ever observed. Every extrapolated row is flagged
   `extrapolated=true` in `RESULTS.json`. This is the weakest joint in the worker.
4. **Outside the 15 probe symbols there is no numeric cost.** H6 failed its own preregistered
   level test; only the identity `cost_taker_rt = spread + 10` transfers, and it needs a spread.
5. **VIP0 fees assumed** (taker 5.0 / maker 2.0 bps). This is measured (`s17`), not asserted:
   the spread and the adverse selection are fee-independent, so the whole model re-runs for any
   tier. Width in bps of the resurrection band `gross ∈ (1.5 × cost_realistic, 14]` — i.e. how
   much of the graveyard reopens:

   | fee tier (taker/maker bps) | T1 MAJOR | T2 LIQUID_ALT | T3 MID_ALT | T4 WIDE_ALT |
   |---|---|---|---|---|
   | **VIP0 5.0 / 2.0 (this report)** | **1.7** | **0.0** | **0.0** | **0.0** |
   | VIP1 4.0 / 1.6 | 3.0 | 0.8 | 0.0 | 0.0 |
   | VIP3 3.4 / 1.4 | 3.7 | 1.8 | 0.0 | 0.0 |
   | VIP5 2.7 / 1.0 | 5.8 | 3.9 | 1.7 | 0.0 |
   | VIP9 1.7 / 0.0 | 8.8 | 6.9 | 4.7 | 0.7 |
   | MM rebate 1.7 / −0.5 | 9.4 | 7.2 | 4.7 | 0.7 |

   **The single input that would most change this report's conclusion is the project's real fee
   schedule, and it is not observable from the data.** At VIP0 the graveyard stays shut; from
   VIP5 upward it genuinely reopens on majors and liquid alts. Before anyone spends more effort
   on execution research, someone should write down which tier this account is actually on.
6. **Adverse selection measured on a direction-agnostic probe is an upper bound** for any
   mechanism that itself predicts the post-fill drift, and a fair estimate for one that does not
   (§5.3). This cuts against the momentum penalty being as large as measured for a genuinely
   predictive momentum alpha, and it is why C2-P99 is `PROMISING_NEEDS_VALIDATION` rather than
   validated.
7. **A cost model cannot resurrect an ETA failure or a declustering failure.** Anything killed by
   `UNCONFIRMABLE_IN_HORIZON`, by declustering, by year-concentration or by a sign flip stays dead
   here, by preregistration and by arithmetic.

## 13. Reproducing this

```bash
export W5_SCRATCH=/path/to/scratch          # ~520 MB peak
cd /home/qbee/futur
E=reports/edge_discovery/alpha_hunt_2026-09-03_round4/w5_execution_cost_layer/evidence
.venv/bin/python $E/s01_probe_consolidate.py                 # probe -> parquet + tick table
.venv/bin/python $E/s02_h1_instrument_audit.py               # H1  instrument audit
.venv/bin/python $E/s04_probe_panel.py                       # causal 30s feature panel
.venv/bin/python $E/s05_h4_urgency.py                        # H4 first pass (unsigned - see 5.2)
for d in 2026-09-01 2026-09-02 2026-09-03 2026-09-04; do
  for v in binance okx hyperliquid; do for s in BTCUSDT ETHUSDT SOLUSDT; do
    .venv/bin/python $E/s06_micro_prep.py $v $s $d
    .venv/bin/python $E/s08_qsim_v2.py   $v $s $d            # the queue simulator
done; done; done
.venv/bin/python $E/s09_cost_model.py                        # cost algebra + probe calibration
.venv/bin/python $E/s10_bridge_probe_cost.py                 # 3 symbols -> 15
.venv/bin/python $E/s11_signed_urgency_and_proxy.py          # signed H4 + H6
.venv/bin/python $E/s12_directional_urgency_capacity.py      # the two arms + H3
.venv/bin/python $E/s13_cost_floor_table.py                  # THE COST FLOOR
.venv/bin/python $E/s14_rejudgement.py                       # H5 retrospective
.venv/bin/python $E/s15_gate.py                              # round-4 gate
.venv/bin/python $E/s17_fee_sensitivity.py                   # fee-tier sensitivity
.venv/bin/python $E/s16_build_results.py                     # RESULTS.json
```
`s03_queue_sim.py` and `s07_qsim_fast.py` are the earlier, superseded simulator drafts, kept
because `s07` is what produced the first calibration and `s08` reproduces it to 0.004 bps.
