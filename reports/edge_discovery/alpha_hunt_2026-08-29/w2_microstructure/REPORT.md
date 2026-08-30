# W2 Microstructure Alpha Hunt — A1 / A3 / A4 / A5 / A6

Date of analysis: 2026-08-29/30. Scope: tick-level L2 book-event and trade
data in `/home/qbee/futur-data-v2/data/market_physics_v3/raw/`, 4 venues
(binance, bybit, okx, hyperliquid) x 3 symbols (BTCUSDT, ETHUSDT, SOLUSDT).
This is an **exploratory fast-triage pass, not a full walk-forward
pipeline run**: single-pass conditional-bucket statistics on a handful of
sample days, no purged CV, no multiple-testing correction, no live
execution simulation. Treat every number below as indicative, not as a
backtested Sharpe.

## Executive summary

None of the five microstructure mechanisms in scope (A1 cross-venue price
discovery, A3 queue depletion hazard, A4 liquidity resilience, A5 toxic
flow, A6 liquidity shock propagation) produced an edge that clears
realistic trading costs on this data. All five show a **real, sign-stable,
economically-sensible conditional relationship** between a microstructure
feature and forward returns — this is not "no signal at all" — but in
every case the magnitude at the horizons available (250ms-30s) is small
relative to the dominant cost on these venues, which is the taker fee
(~4-5bps/side), not the spread (BTCUSDT quotes are within one tick,
~0.01-0.02bps, on binance/okx/bybit essentially all the time). Verdicts:
**A1 WEAK, A3 WEAK (bybit only, binance/okx blocked on this specific
feature), A4 WEAK (largest and most robust effect found, still sub-cost),
A5 WEAK for BTC/ETH (SOL/bybit result is sign-unstable and should be
discarded), A6 DEAD** (as tested; a more careful directional
reformulation is flagged as unfinished, not ruled out). The single most
interesting lead for follow-up is A4's refill-after-sweep signal on
bybit's ask side (~0.5-0.9bps effect at 500ms/2s), which is the largest,
most consistent number in this entire pass but still short of a
round-trip cost.

**Important data-coverage correction**: the brief describes the raw data
as covering `date=2026-08-15..2026-08-29`. In fact only **5 calendar
dates exist on disk**: 2026-08-15, 16, 17, 28, 29 — a contiguous 3-day
block followed, after an 11-day gap, by a contiguous ~1.5-day block (29 is
the partial current day). There is no continuous 2-week window. This
happens to be useful for the requested sign-stability check: period
**A_early = 08-15/16/17** and period **B_late = 08-28/29** are genuinely
independent, non-adjacent samples, which is arguably a *better*
stability test than two adjacent halves of one continuous run — but it
means "2 weeks of data" is not an accurate description of the sample, and
every triage below is really "3 days vs 1.5 days," not "week 1 vs week
2."

## Data, schema, and methodology notes

- Schema source: `/home/qbee/futur-data-v2/market_physics_v3/schema.py`
  (`BookEvent`, `TradeEvent`, `DerivativeEvent` dataclasses, `asdict`-
  serialized to JSONL by `collectors/writer.py`). Reused as-is, no
  guessing.
- Trades: loaded in full (all 4 venues x 3 symbols x 5 dates = 3.23M
  trades, ~1GB total) — small enough to load entirely, no sampling
  needed. Used for A1 and A5.
- Book events: BTCUSDT only, 4 venues, the two full sample days 08-15
  (period A) and 08-28 (period B) — chosen as the *smallest* file for
  each venue within each period (08-15 is smaller than 08-16/17 for
  every venue; 08-28 is a full day vs. 08-29's partial day). Reconstructed
  a compact top-of-book (best bid/ask price+qty, "on change" ticks only)
  time series per venue/period; ~1.8M ticks total, streamed in pure
  Python (`json.loads` line-by-line, ~250-300k lines/sec, whole exercise
  ran in ~90 seconds).
- **Key data-quality finding, load-bearing for A3/A4/A6 methodology**:
  binance and OKX publish *two independent* book streams that were both
  captured — a dedicated top-of-book tick stream (binance `bookTicker`,
  OKX `bbo-tbt`) and a full L2 diff stream (`depth` / `books`). Naively
  merging both into one `{price: qty}` dict to reconstruct top-of-book
  produces a **crossed book (bid >= ask) on 28-56% of ticks**, because the
  two streams are logged from independent websocket connections with
  independent jitter, so a late-arriving message from one stream can
  resurrect a price level the other stream already retired. Root-caused
  by hand (see `book_reconstruct.py` docstring) and fixed by using the
  dedicated top-of-book stream *exclusively* for price/qty on binance/OKX
  (the exchange itself guarantees that stream is never crossed), and
  using the diff stream only as a secondary source for a message-count
  "churn" feature. This brought the crossed-tick rate down to <0.6% for
  binance/OKX/hyperliquid. Bybit has no dedicated top-of-book stream in
  this capture, so its top-of-book still has to come from L2-dict
  reconstruction of `orderbook.50`, which retains a **residual ~10-15%
  crossed-tick rate** (dropped/skipped downstream) — a real, disclosed
  limitation of the bybit numbers below, not a silent patch.
- Fee/cost assumptions (not present anywhere in the data or in
  `alpha_foundry_v5/execution.py`, which only carries a schedule
  dataclass with no populated default): **taker 4.5bps/side, maker
  1.5bps/side**, standard-tier CEX perpetual futures numbers. Spread cost
  is taken from the data itself (median ~0.013-0.016bps for BTCUSDT on
  binance/okx/bybit — essentially the minimum tick — vs. 0.13-0.32bps
  median, up to 2.5-3.2bps at the 75th percentile, on hyperliquid).
  Because spread on the three CEXs is negligible, **taker fee alone is
  the binding cost** for anything requiring an aggressive fill.
- All scripts and full result tables are on
  `w2_microstructure/*.csv` (small, <20KB each) next to this report.
  Intermediate parquet (trades, reconstructed top-of-book) live only in
  the session scratchpad, not copied into the repo, per the "no raw
  dumps in the report dir" instruction.

---

## A1 — Cross-venue price discovery

**Spec (pre-registered before testing)**
- Mechanism: one venue's trade prints move first; the other venues'
  consensus ("leave-one-out") fair value catches up with a lag.
- Payer: inventory sitting on the slower venue that gets picked off /
  arbitraged as the laggard repricing catches up.
- Why the edge could exist: venues have different taker populations,
  latency to the same underlying information, and inventory/hedging
  flow arrives asynchronously.
- Signal: leader venue's trailing 500ms log return of last-trade price,
  restricted to observations where that trailing return is nonzero
  (tick data is sparse at the sub-second grid; most 250-500ms windows
  have zero trades).
- Entry: take a position on the other venues in the direction of the
  leader's recent move.
- Exit: mark-to-market at horizon h (no explicit exit leg modeled).
- Execution venue: the lagging venue(s), taker (needs speed to front the
  catch-up).
- Expected horizon: 250ms-5s (per catalog).
- Expected capacity: small — the edge, where it exists, is concentrated
  in the rare, large "innovation" prints, which are naturally
  capacity-limited.
- Main failure mode: fee/spread erodes an already-small per-observation
  edge; latency to react to the leader's print eats most of the modest
  time budget.

**Fast triage**

Grid: 250ms last-trade-price series per venue (forward-filled from most
recent trade, no leakage), leave-one-out consensus = median of the other
three venues' price on the same grid. Correlation between leader's
trailing-500ms return and forward `loo` return, plus quintile/decile/
extreme-percentile conditional means. BTCUSDT, leader=binance shown
(bybit/okx are qualitatively similar and slightly stronger; hyperliquid
is a clear laggard, never a leader — correlation ~0.03-0.17 vs.
0.15-0.34 for the three CEXs).

| period | leader | horizon | n (nonzero trail) | corr | top-minus-bottom quintile (bps) |
|---|---|---|---|---|---|
| A_early | binance | 1000ms | 93,014 | 0.29 | 0.15 |
| A_early | binance | 5000ms | 93,010 | 0.19 | 0.33 |
| B_late | binance | 1000ms | 5,239 | 0.18 | 0.12 |
| B_late | binance | 5000ms | 5,230 | 0.16 | 0.31 |

Sign and order of magnitude hold up across period A vs. B for
binance/bybit/okx as leader at every horizon tested (250ms-5s) — genuine
stability, not a one-day fluke. Extreme-percentile check (binance leader,
BTCUSDT, period A, h=1000ms): top/bottom 10% -> 0.29bps spread; top/bottom
5% -> 0.48bps; top/bottom 2% -> 0.69bps; top/bottom 1% -> 0.86bps (n=932
each tail). At h=5000ms the top-1% spread grows to 1.37bps. So the edge
*does* concentrate in the tail as expected, but even the most extreme 1%
of leader moves only clears ~1.4bps of gross edge at the best horizon
tested (5s) — well under a single taker leg (4.5bps), let alone a round
trip.

**Verdict: WEAK.** Real, monotonic-in-extremity, sign-stable across two
independent 3-day/1.5-day windows and across three candidate leaders —
this is a textbook lead-lag effect, not noise — but the gross edge tops
out around 1-1.5bps even at the 99th percentile of conditioning, versus a
~4.5bps single taker leg cost on these venues. Would need either a much
cheaper execution channel (maker fills, netted into a larger portfolio
so no dedicated entry/exit cost) or a genuinely faster/deeper dataset
(sub-25ms, order-book-level rather than trade-print-level) to have a
chance.

---

## A3 — Queue depletion hazard

**Spec**
- Mechanism: the intensity of add/cancel/execution messages hitting the
  best bid or ask predicts when that level will fully deplete (best
  price steps away), and depletion is followed by an adverse move for
  whoever was resting there.
- Payer: passive liquidity resting at the top of book that gets
  adversely selected right as the level clears.
- Why the edge could exist: order-flow toxicity building up at a level
  (fast cancel/replace, aggressive partial fills) is a leading indicator
  that informed flow is about to finish off the level.
- Signal: count of depth-stream messages (add/modify/remove) that touch
  the currently-quoted best price, accumulated since that price last
  became best ("churn"), sampled the instant before the level empties.
- Entry: pull/avoid resting size, or flip to the other side, when churn
  is in the top tercile just before depletion.
- Exit: mark-to-market at horizon h after depletion.
- Execution venue: the depleting venue, maker-side risk management (this
  is a "don't get run over" signal, not a directional entry).
- Expected horizon: 25ms-2s (per catalog).
- Expected capacity: tiny — per-queue, per-level effect.
- Main failure mode: churn intensity is a weak proxy for genuine
  informed pressure vs. routine market-maker quote refreshing.

**Fast triage**

Only **bybit** produced a usable churn signal. Binance and OKX's
dedicated top-of-book stream (used for price, per the data-quality fix
above) updates far more often, and at finer price granularity, than the
separate depth-diff stream happens to touch the exact quoted price —
churn counts on binance/OKX are 0 for >97% of depletion events (635,961
of 792,578 ticks have `bid_churn==0`; the intensity feature basically
never accumulates before we see the same tick), so a 3-way quantile
split degenerates. This is a genuine methodology limitation, not a bug:
it comes from mixing a low-latency single-level stream with a slower
full-depth stream that don't tick at the same rate, and it is the same
underlying issue documented above for A6. Full L2-dict reconstruction
(dropping the dedicated tob stream) would fix this for binance but
reintroduces the ~30-55% crossed-book problem, so it wasn't attempted
in this fast-triage budget.

Bybit ask-side results (only side/venue with a clean 3-way split):

| period | horizon | n depletion events | low-intensity tercile (bps) | high-intensity tercile (bps) | high-low |
|---|---|---|---|---|---|
| A_early | 250ms | 791 | 0.045 | -0.099 | -0.144 |
| A_early | 5000ms | 791 | 0.111 | -0.118 | -0.230 |
| B_late | 250ms | 790 | -0.020 | -0.073 | -0.053 |
| B_late | 5000ms | 790 | 0.086 | 0.006 | -0.081 |

(bps sign convention: positive = price kept moving away from the
depleted side, i.e. the naively-expected "hazard" direction.)

Sign of high-low is negative in both periods at every horizon — but that
is the *opposite* of the pre-registered hazard hypothesis: high pre-
depletion churn predicts a smaller/negative continuation, not a larger
adverse move. Read plainly, this looks more like absorption (heavy churn
= market makers actively refreshing/defending the level, and its
eventual depletion is *less* informationally loaded) than hazard. Sign
is at least consistent across the two independent periods, but magnitude
(0.05-0.23bps) is far below any realistic cost, and n≈264 per bucket per
config is thin.

**Verdict: WEAK (bybit only) / effectively BLOCKED_DATA for binance and
OKX on this specific feature.** What would fix binance/OKX: either a
depth feed with an explicit "queue position/age" field, or capturing the
full L2 diff stream cleanly enough (proper snapshot-vs-diff
reconciliation, not naive dict merge) to get a churn count that lines up
with the same top-of-book price series used for the return calculation.

---

## A4 — Liquidity resilience (refill asymmetry after a sweep)

**Spec**
- Mechanism: after a level is swept (best price steps away with no
  immediate same-or-better replacement), how much new resting size shows
  up at the new best within a short window predicts whether the move
  continues or gets rejected.
- Payer: momentum/breakout traders who extrapolate a shock that liquidity
  providers are about to fade.
- Why the edge could exist: refill speed/size is a direct, observable
  proxy for market-maker conviction about the new price.
- Signal: refill ratio = resting qty at the new best 500ms after the
  sweep, divided by the qty that was resting at the old best right
  before it swept.
- Entry: on the side of continuation implied by the refill regime.
- Exit: mark-to-market 2s after the sweep.
- Execution venue: same venue, taker to catch the continuation (or maker
  if fading).
- Expected horizon: 100ms-30s (per catalog).
- Expected capacity: modest — one event per sweep, but sweeps are
  frequent (168-2168 events per venue/period/side in this sample).
- Main failure mode: refill measured over a fixed 500ms window may not
  match the true liquidity-provider reaction time, which likely varies
  by venue/regime.

**Fast triage**

Tercile split of refill ratio, continuation return (signed toward the
sweep direction) at h=2000ms:

| venue | period | side | n sweeps | low-refill tercile (bps) | high-refill tercile (bps) | low-high |
|---|---|---|---|---|---|---|
| binance | A_early | bid | 1,905 | 0.178 | 0.294 | -0.116 |
| binance | A_early | ask | 2,168 | 0.028 | 0.186 | -0.159 |
| binance | B_late | bid | 1,981 | -0.024 | 0.378 | -0.402 |
| binance | B_late | ask | 2,068 | -0.022 | 0.310 | -0.332 |
| okx | A_early | ask | 386 | 0.163 | 0.342 | -0.179 |
| okx | B_late | ask | 267 | 0.420 | 0.633 | -0.213 |
| bybit | A_early | ask | 410 | 0.046 | 0.824 | -0.778 |
| bybit | B_late | ask | 430 | 0.089 | 0.596 | -0.507 |

**This is the single cleanest, most sign-stable result in the whole
pass**: on every venue x period x side combination that had enough
events for a clean 3-way split (10 of 12), high refill predicts *more*
continuation, not reversion — the opposite of the naive hypothesis
("refill = rejection") but internally consistent and monotone at a
median/quintile split (checked separately: pct=0.5/0.2 splits reproduce
the same sign and growing magnitude on binance/bybit/okx; only the most
extreme 10% tail gets noisy, n≈200/bucket). Revised mechanism reading:
fast, large refill after a sweep looks like market makers *repricing
with conviction* to the new level rather than defending the old one —
refill size is a vote that the new price is fair, which is followed by
further drift in the same direction. Bybit's ask side has the largest
effect (0.5-0.9bps), roughly on the order of a single maker-fee leg
(1.5bps) but still under a full round trip and under a single taker leg
(4.5bps).

**Verdict: WEAK, but the most promising lead in this pass.** Worth a
follow-up with more symbols/dates and an actual maker-fill execution
model (this signal's natural home is "which side to make/take after a
sweep," not a pure taker momentum trade) before writing it off.

---

## A5 — Toxic flow and absorption

**Spec**
- Mechanism: a burst of one-sided aggressive (taker) flow either
  continues (informed/toxic — late followers get run over) or gets
  absorbed (price barely moves despite the flow — market makers
  profitably warehouse it).
- Payer: late market-order followers who trade in the same direction
  after the burst, if flow is toxic.
- Why the edge could exist: signed order flow imbalance is a classic,
  cheap-to-compute proxy for short-term informed trading.
- Signal: trailing 1-second sum of signed trade notional, per
  venue/symbol.
- Entry: trade with the sign of extreme trailing flow (if toxic) or fade
  it (if absorption pattern found instead).
- Exit: mark-to-market at horizon h.
- Execution venue: same venue as the flow, taker (reacting to already-
  printed trades).
- Expected horizon: 100ms-30s (per catalog).
- Expected capacity: proportional to trade frequency — largest of the
  five mechanisms tested in terms of raw n.
- Main failure mode: the flow signal correlates with the very trades used
  to define it (own-price autocorrelation), so part of the effect can be
  mechanical rather than predictive of *future new* information.

**Fast triage**

Decile split of trailing 1s signed notional, forward return at the same
venue, own price series:

| symbol | venue | period | horizon | n | top-minus-bottom decile (bps) |
|---|---|---|---|---|---|
| BTCUSDT | binance | A_early | 1000ms | 349,647 | 1.75 |
| BTCUSDT | binance | B_late | 1000ms | 15,219 | 0.94 |
| ETHUSDT | bybit | A_early | 5000ms | 622,720 | 2.93 |
| ETHUSDT | bybit | B_late | 5000ms | 36,773 | 0.79 |
| SOLUSDT | bybit | A_early | 1000ms | 138,863 | 11.19 |
| SOLUSDT | bybit | B_late | 1000ms | 19,340 | **-8.49** |

Sign is **positive (continuation/toxic, not absorption) and stable
across both independent periods for every BTC and ETH venue tested**
(binance, bybit, okx) — genuine, replicable signal. Magnitude is modest:
0.5-3bps top-minus-bottom at 1-5s horizons for BTC/ETH, i.e. again short
of a full round-trip cost and roughly comparable to a single taker leg
at best (ETH/bybit, 5s horizon).

**SOLUSDT/bybit is a hard exception and should be discarded as a
finding**: period A shows the same continuation pattern, greatly
amplified (11.2bps); period B shows a **sign flip to strong reversal**
(-8.5 to -10.5bps at 1-5s, correlation flips from +0.28/+0.65 to
-0.47/-0.71). This is exactly the kind of one-regime artifact the task
asked to watch for — almost certainly driven by a handful of large
SOL moves in the short B_late window rather than a stable relationship,
and it should not be treated as a SOL-specific edge in either direction
without a much longer sample.

**Verdict: WEAK for BTC/ETH** (real, sign-stable, sub-round-trip-cost,
roughly single-taker-leg-cost at best). **SOL/bybit: sign-unstable,
discard, flag for a longer sample before drawing any conclusion.**

---

## A6 — Liquidity shock propagation

**Spec**
- Mechanism: a spread-widening or depth-drop shock on a leader venue
  propagates to follower venues' liquidity conditions and to the
  cross-venue consensus price, with a measurable lag.
- Payer: slow cross-venue market makers/arbitrageurs who haven't yet
  repriced their own quotes to match the leader's new liquidity state.
- Why the edge could exist: liquidity shocks (not just price prints)
  carry information about local order-flow pressure that hasn't fully
  fed through to price yet.
- Signal: leader venue's trailing 1s change in spread_bps (widening) or
  trailing 1s % change in best-level depth notional (drop), thresholded
  at the 95th percentile.
- Entry: trade the consensus (loo) price in the direction implied by the
  shock.
- Exit: mark-to-market at horizon h.
- Execution venue: follower venues, taker.
- Expected horizon: 100ms-30s (per catalog).
- Expected capacity: event-driven, moderate.
- Main failure mode (realized, see below): combining bid-side and
  ask-side depth into one undirected "depth_notional" shock destroys the
  directional information the mechanism actually needs — a leader
  bid-depth drop and a leader ask-depth drop should predict opposite
  price directions, and averaging them together should wash the effect
  toward zero regardless of whether the underlying mechanism is real.

**Fast triage**

250ms grid, BTCUSDT, leader = binance or OKX, forward loo-consensus
return of the other three venues at the 95th-percentile shock vs.
unconditional:

| period | leader | shock | horizon | shocked mean (bps) | unconditional mean (bps) | diff (bps) |
|---|---|---|---|---|---|---|
| A_early | binance | spread widening | 5000ms | -0.0005 | 0.0002 | -0.0007 |
| A_early | okx | depth drop | 5000ms | -0.0079 | -0.0001 | -0.0078 |
| B_late | binance | spread widening | 5000ms | 0.0274 | 0.0351 | -0.0076 |
| B_late | okx | depth drop | 5000ms | 0.0115 | 0.0345 | -0.0230 |

Effect sizes are 0.0007-0.023bps — two to three orders of magnitude below
even the spread cost, let alone the taker fee. The `depth_drop` shock's
sign is not even stable within a single period across horizons for OKX
(A_early: +0.0008 at 250ms, then -0.0029, -0.0078 — flips sign inside
one period), consistent with noise rather than signal, on top of the
known methodology flaw of not separating bid-depth from ask-depth shocks.

**Verdict: DEAD as tested.** This is the one mechanism where I'd
attribute the null result partly to a **fixable methodology gap**
rather than a clean kill of the hypothesis: the undirected combined-
depth shock formulation is the wrong test. A proper version would (a)
separately test leader bid-depth-drop -> predicted downward consensus
move and leader ask-depth-drop -> predicted upward consensus move, and
(b) use the venue's genuine multi-level depth (not just best-level
qty) for the "depth" side of the signal, which requires solving the
crossed-book L2 reconstruction problem described above rather than
working around it. Neither was done in this budget; A6 should be
re-run properly before being called DEAD with full confidence, but as
tested here it produced nothing usable.

---

## BLOCKED_DATA summary

- **A3 on binance/OKX (churn/intensity feature specifically, not the
  price series)**: blocked because the dedicated top-of-book tick stream
  and the full-depth diff stream tick at different rates/granularities,
  so a message-count-at-best-price feature computed from the diff stream
  is degenerate (>97% zero) when the price series comes from the tob
  stream. Needs either a genuine per-level queue-age/message-count field
  from the venue, or a properly reconciled (snapshot-sequenced, not
  naively merged) full L2 book so churn and price come from the same
  consistent state machine.
- **A4/A6 genuine multi-level depth** (the catalog's `notional_to_move`
  / `depth_*` beyond best level): not attempted at all in this pass —
  only best-level qty was used as a depth proxy. Multi-level depth
  requires solving the crossed-book L2 reconstruction problem for
  binance/OKX (the naive dict-merge approach produces 28-56% crossed
  ticks, as documented above) — doable but out of this fast-triage
  budget.
- **General**: options/IV data (A14-adjacent) and on-chain wallet flow
  (A11/A15-adjacent) are not present anywhere in this dataset — not
  applicable to this pass but noting for completeness since the catalog
  references them elsewhere.

## Cost-hurdle summary (why every verdict landed on WEAK/DEAD)

| mechanism | best gross edge found | cost of the minimum viable trade | net |
|---|---|---|---|
| A1 | ~1.4bps (top 1% leader move, 5s horizon) | ~4.5bps (1 taker leg) | negative |
| A3 | ~0.2bps (bybit ask tercile spread) | ~4.5bps (1 taker leg) or ~1.5bps (1 maker leg) | negative |
| A4 | ~0.5-0.9bps (bybit ask, median-split refill, 2s) | ~1.5-4.5bps (1 leg) | negative to roughly breakeven only under best-case maker assumptions |
| A5 | ~2.9bps (ETH/bybit, top-bottom decile, 5s) | ~4.5-9bps (1 leg to round trip) | negative |
| A6 | ~0.02bps | ~0.02-4.5bps | negative, edge is noise-sized |

Spread itself is essentially free on BTCUSDT across binance/okx/bybit
(median ~0.013-0.016bps, one tick) in this window, so the fee assumption
(4.5bps taker / 1.5bps maker) is what's actually killing every one of
these, not slippage. Any of these mechanisms would need either (a) a
maker-fill execution model that avoids the taker leg entirely, or (b) a
much larger, more diverse sample to find whether the tail (as in A1's
1%-percentile check) keeps growing faster than costs at more extreme
conditioning than tested here.
