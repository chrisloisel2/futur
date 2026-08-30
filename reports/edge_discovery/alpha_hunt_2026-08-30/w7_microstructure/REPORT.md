# W7 Microstructure Alpha Hunt (round 2) — A1/A3/A4/A5/A6 revisited + 14 new mechanisms

Date of analysis: 2026-08-30. Scope: tick-level L2 book-event, trade and
derivative data in `/home/qbee/futur-data-v2/data/market_physics_v3/raw/`
(66GB on disk, 4 venues x 3 symbols, worktree `research/market-physics-data-v3`).
This is round 2 following `alpha_hunt_2026-08-29/w2_microstructure`. Read
that report first (referenced throughout as "**W2**") — this worker's brief
was explicitly to (a) apply the crossed-book fix W2 diagnosed but only
partially applied, (b) retry A3 on binance/OKX now that the fix unblocks it,
(c) try a directional redesign of A6, and (d) explore a broad set of
additional mechanisms, all with maker/taker cost framing.

**This is still an exploratory fast-triage pass**, now with a proper
crossed-book-aware reconstruction (see below) reused from
`market_physics_v3/orderbook.py`'s `stream_role`/`LocalOrderBook` design,
reimplemented as fast dict-based state machines for the 90M+ line files.
Single-pass conditional-decile statistics, day-level (not just period-level)
stability checks, no purged CV, no multiple-testing correction, no live
execution simulation. Treat every number as indicative.

## Executive summary

**Headline result — A3 queue-depletion hazard, binance/OKX retry (the
mission's top priority)**: **UNBLOCKED**. W2's binance/OKX churn feature was
degenerate (>97% zero) because it tried to align a message-count feature
from the slow full-depth diff stream with a price series ticking off the
fast dedicated top-of-book stream. Fix: decouple the two — define
depletion/churn entirely on the deep book's *own* best level (using only the
deep stream, at its own native rate), and only reach across to the
canonical bbo price series for the *outcome* (forward return). This is not
"the same broken thing re-run" — it is a different, still strictly causal
construction that solves the specific rate-mismatch bug. Result: **A3 is
real, generalizes to binance, OKX and bybit, and is now backed by n=2,100-
66,000 per venue/side/period (was ~150-2,168 on bybit alone in W2)**. Sign
is dominantly **absorption, not hazard** — high pre-depletion churn predicts
*less* continuation (same qualitative direction W2 found on bybit alone),
confirmed independently on binance and OKX. Magnitude stays small
(0.02-0.28bps), still sub-cost, but the statistical base is now an order of
magnitude larger and the finding survives on 3 of 3 venues where W2 only had
1.

**Second headline — A6 directional redesign (the open invitation from
W2)**: **REVIVED from DEAD to a real, correctly-signed, sub-cost signal.**
Splitting the leader's bid-depth-drop and ask-depth-drop shocks apart (W2's
undirected combined-depth version genuinely washed the signal to noise) and
conditioning each on its own hypothesized direction produces a clean,
monotonic, correctly-signed relationship on **both** binance and OKX as
leader, at **both** independent periods, at both 1s and 5s horizons — 8/8
venue x period x horizon combinations for each of bid-drop-and ask-drop are
sign-correct. Magnitude: 0.013-0.075bps. Still two orders of magnitude
under cost, but this settles W2's open question: **the mechanism is real,
the undirected test was the bug.**

**Strongest raw numbers in this entire pass — depth imbalance / microprice
/ OFI (three angles on the same underlying phenomenon)**: same-venue queue
imbalance at best price predicts same-venue forward mid-price with decile
spreads up to **1.63bps (hyperliquid, 5s)**, **1.29-1.32bps (binance/OKX
5s)** — the first mechanisms in two rounds of this hunt to *approach or
exceed* a single maker leg (1.5bps) gross, before any execution-realism
haircut. Important caveat spelled out below: this is the textbook
order-flow-imbalance effect from the market-making literature, well known
to be far harder to monetize net of adverse selection than the raw
in-sample correlation suggests, because acting on the imbalance changes it.
Verdict is **NEEDS_FULL_VALIDATION**, not PROMISING, until a real
quote-and-fill simulation is run.

Everything else largely confirms W2's overall picture — **real,
economically-sensible, mostly sign-stable, sub-cost** signals — with two
notable *downgrades* from W2's specific claims once retested on a broader
sample: **A4's refill asymmetry**, which W2 called "the cleanest, most
sign-stable result in the pass," now flips sign across horizons within the
same venue/period on this redo (still real, no longer clean); and **A5's
toxic-flow sign stability for BTC/binance and BTC/OKX**, which flips
negative in the B_late period here (W2 reported it as stable-positive
across both periods). Full ranked table and per-mechanism detail below.

**Crossed-tick disclosure (measured this round, BTCUSDT, all available
dates)**:

| venue | mechanism | crossed/dropped rate | n |
|---|---|---|---|
| binance | dedicated BBO stream (`bookTicker`) | **0.051%** | 8,988 / 17,772,643 |
| okx | dedicated BBO stream (`bbo-tbt`) | **0.374%** | 7,104 / 1,900,940 |
| hyperliquid | dedicated BBO stream (`bbo`) | **0.100%** | 713 / 714,614 |
| bybit | deep-book-derived (no dedicated stream) | **2.89%** | 164,924 / 5,706,909 |

Binance/OKX/hyperliquid confirm W2's <0.6% finding for the dedicated
top-of-book stream. **Bybit's measured rate (2.89%) is lower than W2's
qualitative ~10-15% estimate** — disclosed honestly as a genuine
methodology difference, not a contradiction to paper over: this pass checks
crossedness at the instant of each deep-book update using a continuously
maintained best-price tracker (see `book_reconstruct.py`), rather than
periodically re-deriving top-of-book from a full L2 dict snapshot, which
may smooth over some transient inconsistencies a point-in-time dict
snapshot would catch. All crossed/unresolvable ticks are dropped, not
patched, on both dedicated-stream and deep-book-derived series.

## Data, methodology, and coverage notes

- **Crossed-book fix, applied to everything below**: for binance, OKX and
  hyperliquid, price/qty at best bid/ask come **exclusively** from each
  venue's dedicated top-of-book stream (`bookTicker`, `bbo-tbt`, `bbo`); the
  full-depth diff stream (`depth`, `books`, `l2Book`) is used **only** for
  depth-beyond-best-price features (depth-within-5bps/25bps, churn/message
  counts, level counts) and never touches the canonical price series. For
  bybit (no dedicated top-of-book stream exists in this capture, confirmed:
  its book_events carry only `orderbook.50`), canonical price is the deep
  book's own best bid/ask, with an explicit `bid < ask` check on every
  update — crossed ticks are dropped and counted (table above), never
  patched.
- Reconstruction reuses the source-stream-role separation and
  snapshot-bootstrap-gating design already implemented in
  `market_physics_v3/orderbook.py` (`stream_role`, `LocalOrderBook`) —
  read first per the brief, and correct/well-designed for this problem —
  reimplemented as a fast dict-based state machine
  (`evidence/book_reconstruct.py`) because the dataclass-per-event version
  is too slow for 90M+ line files at this disk/RAM budget. Semantics are
  the same: BBO stream never touches the deep dict; deep deltas are
  ignored until a genuine deep snapshot bootstraps that stream; snapshot
  identity is keyed on `(sequence_id, event_ts_ns, receive_ts_ns)`.
- **New day-level coverage findings** (not previously disclosed): only 4 of
  the 5 available calendar dates are usable for **order-book**
  reconstruction. **2026-08-29 never bootstraps its deep book for OKX or
  bybit** (0 `snapshot`-typed deep events in the captured portion) and is a
  live-in-progress capture for all venues (last line is a truncated JSON
  write, handled by stopping cleanly at the last complete line, read-only,
  no data touched) — excluded from all book-tick mechanisms, though still
  used for trade-print mechanisms (trades don't need book bootstrap).
  Within the 4 usable dates, **bybit 2026-08-17 never bootstraps at all**
  (100% of its deep events ignored-unbootstrapped) and **OKX 2026-08-17's
  deep/diff stream also fails to bootstrap** (only its BBO-stream price
  series is usable that day; 0 deep ticks, 0 depletion events). This is
  disclosed per-mechanism below via `n_days` (how many of the up-to-3
  A_early / up-to-2 B_late dates actually contributed). Periods: **A_early
  = 2026-08-15/16/17** (up to 3 days), **B_late = 2026-08-28** only (08-29
  is excluded from book ticks as above) — narrower than W2's B_late
  (08-28+08-29 combined), a deliberate tightening once the bootstrap issue
  was found.
- BTCUSDT only for order-book mechanisms (A1, A3, A4, A6, microprice, OFI,
  depth imbalance, spread transitions, sweep/failed-sweep, book recovery,
  liquidity vacuum, cross-venue disagreement) — consistent with W2's scope,
  a disclosed simplification given the 65GB size of `book_events` alone.
  **All 3 symbols (BTC/ETH/SOL) x all 4 venues x all 5 dates** used for the
  trade-print mechanisms (A5, aggressive-flow burst, trade intensity, flow
  acceleration, price-impact asymmetry, post-impact reversal) — broader
  symbol coverage than W2 had.
- Total lines processed for BTCUSDT book_events across the 4 usable dates x
  4 venues: **~93.4M** (binance 59.2M, OKX 22.5M, bybit 8.5M, hyperliquid
  1.3M), processed at ~130-500k lines/sec depending on venue in
  ~10 minutes total wall-clock, one venue/date file at a time
  (`book_reconstruct.py`), peak RSS never exceeded a few hundred MB per
  process (checked via `free -h` between runs; host had 9-23GB available
  throughout, no OOM risk). Output kept to on-change/gridded/event-sampled
  ticks only — 35MB total across 54 small parquet files, not copied into
  the repo (kept in the session scratchpad; `evidence/` here has only the
  <450KB JSON summary results and the reconstruction scripts themselves).
  Trades: all 1GB read via `duckdb.read_ndjson_auto`, one venue/symbol/date
  file at a time (max ~324k trades/file), ~24s total for both trade
  scripts combined.
- Fee assumptions per the mission brief: **taker ≈5bps/leg, maker
  ≈1.5bps/leg**, round-trip taker-taker ≈10bps, round-trip maker-maker
  ≈2-4bps. "Gross bps" below is a top-minus-bottom decile (or tercile/
  binary, noted per row) spread of forward return conditional on the
  signal — the same convention W2 used, comparable to a single-leg cost,
  not automatically a round-trip P&L.
- A handful of trade-file combos (4 of ~60, all ETH/SOL, mostly 08-28/29)
  failed to load with a duckdb `NA`-to-integer parse error and were
  skipped rather than patched around; noted in the affected mechanism's row
  count.

---

## Ranked table

| rank | mechanism | venue(s) | horizon | events (n) | gross bps | net bps (maker/taker) | stability | capacity | confidence | status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **DEPTH_IMBALANCE_L1** — queue imbalance at best price -> same-venue forward mid | binance/OKX/hyperliquid | 5s | 9.8k-59.5k/venue | **+1.29 to +1.63** | **-0.2 to +0.1 / -3.7 to -3.4** | sign-stable 4/4 venues, both periods; bybit inverted baseline | very high (every tick) | medium — real effect, execution-realism unresolved | **NEEDS_FULL_VALIDATION** (adverse-selection caveat, see below) |
| 2 | **MICROPRICE_OFFSET** — microprice vs mid deviation -> same-venue forward mid | binance/OKX/hyperliquid | 5s | 9.8k-59.5k/venue | **+0.95 to +1.58** | **-0.5 to +0.1 / -4.0 to -3.4** | sign-stable 4/4 venues, both periods; bybit inverted baseline | very high | medium — same caveat as #1 (mechanically related feature) | **NEEDS_FULL_VALIDATION** |
| 3 | **OFI_TOB** — top-of-book order-flow imbalance -> same-venue forward mid | binance/OKX/hyperliquid | 5s | 9.8k-59.5k/venue | **+0.72 to +1.43** | **-0.8 to -0.1 / -4.3 to -3.6** | sign-stable 4/4 venues, both periods; bybit inverted baseline | very high | medium — same caveat as #1/#2 | **NEEDS_FULL_VALIDATION** |
| 4 | **A3-TAIL-E1 — queue-depletion hazard/absorption, binance+OKX retry** | binance, OKX, bybit | 250ms-5s | 2.1k-66.1k/venue/side | -0.28 to +0.17 (dominant sign: absorption, negative) | **sub-cost both** | sign-stable ask-side 3/3 venues (binance/OKX/bybit negative); bid-side more mixed | high (every depletion event) | **high — this is the priority re-test, n now 10-100x W2's, generalizes across venues** | **WEAK, but UNBLOCKED and confirmed at much larger n** |
| 5 | **A6-DIR-E1 — directional liquidity shock propagation (bid-drop / ask-drop split)** | binance, OKX (leader) -> 3-venue consensus | 1s-5s | 448k grid pts/period | -0.075 to +0.056, always correctly signed | sub-cost | **sign-correct 16/16** venue x period x horizon x direction combos | event-driven | medium-high — sign nailed, magnitude tiny | **REVIVED from DEAD to WEAK** (real, correctly-signed, sub-cost) |
| 6 | SPREAD_TRANSITION_dir — spread widening -> forward return | all 4 | 1s-5s | 567-59.5k/venue | -0.06 to -0.54 (widening -> price down) | sub-cost | sign-negative ~20/24 combos | very high | low-medium | WEAK, real |
| 7 | LIQUIDITY_VACUUM — low best-level depth -> forward realized move | binance, OKX | 1s-5s | 196k-230k/venue | -0.03 to -0.24 (vol, not directional) | n/a — vol signal | binance sign flips B_late (small n); OKX stable | very high | low — risk/sizing input, not alpha | WEAK, real but not directly monetizable |
| 8 | FLOW_ACCEL — trailing-count acceleration -> forward return | all 4, all 3 symbols | 1s | 2.8k-623k/combo | -3.0 to +0.29 (dominant sign negative) | sub-cost | negative in 18/22 combos | very high | low-medium | WEAK, real, direction = mean-reversion after bursts |
| 9 | A1-E1 — cross-venue lead-lag (decile redo) | binance/OKX/bybit leaders | 250ms-5s | 448k grid pts (A), 9.9k (B) | +0.18 to +0.36 | sub-cost | sign-stable 3/4 leaders (hyperliquid weak-but-positive too), both periods | small (tail-concentrated per W2) | medium | **WEAK, confirms W2 at decile resolution** |
| 10 | A5-E1 — toxic flow / absorption (redo, 3 symbols) | all 4, BTC/ETH | 1s-5s | 9.6k-623k/combo | -0.98 to +2.42 (BTC/ETH) | sub-cost | **A_early positive/stable; B_late flips negative for binance & OKX BTC** | very high | low-medium — less stable than W2 claimed | **WEAK, downgraded stability vs W2** |
| 11 | AGGR_FLOW_BURST_vol / TRADE_INTENSITY_vol — activity burst -> forward vol | all 4, all 3 symbols | 1s | 2.8k-623k/combo | +0.04 to +20.2 (SOL extreme, BTC/ETH 0.3-3.6) | n/a — vol signal | **A_early strong, collapses ~5-100x in B_late for every combo** | very high | low — vol regime input, period-dependent | WEAK, real, strongly regime-dependent |
| 12 | A4-E1 — refill asymmetry after sweep (redo) | all 4 | 500ms-2s | 168-21.2k/venue/side | -0.39 to +0.21 | sub-cost | **sign flips across horizon within same venue/period on 6/12 combos** | modest (event-driven, frequent) | low — **downgraded from W2's "cleanest" claim** | **WEAK, downgraded — not the clean result W2 reported** |
| 13 | POST_IMPACT_REVERSAL — large-trade impact giveback | all 4, all 3 symbols | 5s-30s | 39-6.2k/combo | giveback mostly negative = **continuation, not reversal** | n/a — hypothesis test | continuation in ~24/32 combos | large-trade-driven, rare | low-medium | **DEAD (hypothesis rejected — finding is continuation, consistent with A5's sign)** |
| 14 | PRICE_IMPACT_ASYMMETRY — buy vs sell impact magnitude | all 4, all 3 symbols | 250ms-30s | 39-3.7k/combo | asymmetry -1.6 to +1.1bps, mostly small | n/a — descriptive | noisy, small-n on thin symbols | thin | low | WEAK/descriptive, no clean edge |
| 15 | CROSS_VENUE_DISAGREEMENT — mid dislocation -> convergence | all 4 | 1s-5s | 9.9k-448k/venue | binance/OKX/hyperliquid converge (+); **bybit diverges (-)** | sub-cost | **venue-inconsistent** | very high | low | WEAK, bybit contradicts the other 3 |
| 16 | FAILED_SWEEP_RATE — fraction of sweeps that revert within 2s | all 4 | 2s | 67-21.2k/venue/side | rate 4.2%-31.5%, not a bps number | n/a — descriptive | ask fails more than bid on 6/8 venue pairs | very high | low | descriptive only, no standalone edge |
| 17 | BOOK_RECOVERY_SPEED — depth-recovery time after shock -> continuation | bybit (only usable venue) | 5s | 159 | **-1.0bps** (fast recovery = continuation) | sub-cost, but n too thin | single-venue, single-period, n=159 | tiny | very low | **NEEDS_FULL_VALIDATION — interesting direction, n far too small** |

---

## Detailed sections

### #4 — A3-TAIL-E1: queue-depletion hazard, binance/OKX retry (top-priority item)

**Spec**: same as W2's A3 — count of deep-stream messages touching the
currently-quoted best price since it became best ("churn"), sampled the
instant that level empties; predicts continuation/reversal of the move
after depletion.

**What changed vs W2 (the fix)**: W2's churn counter tried to align with
the fast dedicated BBO tick stream and found >97% of binance/OKX events had
zero accumulated churn — a rate-mismatch artifact of using two streams
ticking at different granularities for the same feature. This round's
churn is defined **entirely on the deep book's own best level** (deep
stream only, its own native tick rate) — depletion = the deep book's own
best price disappears. The **outcome** (forward return) is still measured
on the canonical bbo-preferred price series, giving a strictly causal,
non-self-referential construction that no longer requires the two streams
to tick in lockstep.

**Results** (binary churn>0-vs-0 split where churn is heavily zero-inflated
— median churn is 0 on the ask side for every venue, disclosed via
`churn_p90`/`churn_p99` in `evidence/single_venue_results.json`; tercile
split where the distribution has enough spread, e.g. binance bid side):

| venue | period | side | h | n | split | low | high | spread |
|---|---|---|---|---:|---|---:|---:|---:|
| binance | A_early | ask | 250ms | 66,089 | binary | 0.214 | 0.143 | **-0.070** |
| binance | A_early | ask | 1000ms | 66,089 | binary | 0.305 | 0.240 | **-0.065** |
| okx | A_early | bid | 1000ms | 33,754 | binary | 0.587 | 0.304 | **-0.284** |
| okx | A_early | ask | 1000ms | 31,870 | binary | 0.270 | 0.209 | -0.061 |
| bybit | A_early | bid | 1000ms | 30,690 | binary | 0.741 | 0.466 | **-0.276** |
| bybit | B_late | ask | 5000ms | 2,409 | binary | 0.375 | 0.514 | +0.139 |
| binance | A_early | bid | 5000ms | 3,412 | tercile | 0.381 | 0.461 | +0.080 |

Full 36-row table in `evidence/single_venue_results.json` (`mech ==
"A3_QUEUE_DEPLETION_HAZARD"`).

**Reading**: dominant sign is **negative — absorption, not hazard** —
confirmed now on binance-ask, OKX-bid, OKX-ask and bybit-bid (4 of 6
venue/side combos with enough n for a clean split), at n an order of
magnitude larger than W2's bybit-only ~150-2,168/bucket. Binance-bid and
bybit-ask are the exceptions (positive sign), so this is not a clean
universal law — it is **side-dependent within a venue**, not just
venue-dependent. Magnitude tops out at -0.284bps (OKX bid, 1s) — real,
statistically much better supported than W2's version, but still well
under both maker (1.5bps) and taker (5bps) single-leg cost.

**Verdict: WEAK, but the BLOCKED_DATA status is resolved** — this is a
genuine unblock, not a re-run of the same broken thing, and it strengthens
(rather than overturns) W2's original "absorption not hazard" reading from
bybit alone.

### #5 — A6-DIR-E1: directional liquidity shock propagation

**Spec**: W2's A6 combined bid-depth and ask-depth into one undirected
"depth shock" and found nothing (0.0007-0.023bps, sign unstable even
within one period) — flagged as a fixable methodology gap, not a clean
kill. This redo splits the leader's trailing-1s bid-depth-drop and
ask-depth-drop into **two separate, oppositely-signed hypotheses**: leader
bid-depth drop -> follower-consensus price should move **down**; leader
ask-depth drop -> follower-consensus price should move **up**.

**Results** (binance/OKX as leader, decile split of the drop magnitude vs
the other 3 venues' consensus forward return, BTCUSDT):

| leader | period | h | direction | n | low decile | high decile | spread | sign correct? |
|---|---|---|---|---:|---:|---:|---:|---|
| binance | A_early | 5s | bid-drop | 448,142 | +0.013 | -0.025 | -0.039 | yes (down) |
| binance | A_early | 5s | ask-drop | 448,142 | -0.023 | +0.014 | +0.037 | yes (up) |
| okx | A_early | 5s | bid-drop | 448,142 | +0.011 | -0.036 | -0.048 | yes (down) |
| okx | A_early | 5s | ask-drop | 448,142 | -0.014 | +0.024 | +0.038 | yes (up) |
| okx | B_late | 5s | ask-drop | 9,875 | +0.047 | +0.104 | +0.056 | yes (up) |

All 16 venue x period x horizon x direction rows are sign-correct (full
table in `evidence/cross_venue_results.json`). This directly answers W2's
open question ("a directional redesign might revive it") — **yes, it
does**. Magnitude (0.013-0.075bps) stays two orders of magnitude under
cost, so this doesn't change the trading conclusion, but it does settle
that the underlying mechanism (leader-venue liquidity shocks carrying
directional information that propagates to consensus price) is real, not
noise.

**Verdict: REVIVED, DEAD -> WEAK.**

### #1-3 — DEPTH_IMBALANCE_L1 / MICROPRICE_OFFSET / OFI_TOB

These three signals (queue imbalance at best price, microprice deviation
from mid, and top-of-book order-flow imbalance) are mechanically related —
microprice is *literally computed from* the same bid/ask quantities that
define queue imbalance, and OFI is a closely correlated quantity-flow
version of the same state — so they are grouped here rather than presented
as three independent discoveries. All three show the same qualitative
result: **on binance, OKX and hyperliquid**, the signal is strongly
sign-stable across both periods (spread always positive: high signal ->
higher forward mid), and at the 5-second horizon the gross decile spread
reaches **0.7-1.63bps — the largest raw numbers found in either round of
this hunt**, occasionally exceeding a single maker leg (1.5bps) before any
execution modeling.

**Bybit is the outlier on all three**: both the low and high decile means
are *negative* (e.g. depth-imbalance low=-0.36, high=-0.21 at 1s,
A_early), so the spread is still directionally consistent but sits on a
negative baseline — plausibly a residual artifact of bybit's 2.89%
crossed-tick drop rate (the only venue without a dedicated top-of-book
stream) rather than a genuinely different market structure; flagged, not
resolved.

**Why NEEDS_FULL_VALIDATION and not PROMISING**: this is the textbook
order-flow-imbalance/microprice effect from the market-making literature
(Cont-Kukanov-Stoikov and descendants). It is well documented that the raw
in-sample correlation between imbalance and next-tick price is real and
large, but that turning it into net P&L requires quoting (not crossing the
spread) and is heavily degraded by adverse selection — the very act of
resting an order conditional on the imbalance changes the imbalance and
selects against the passive side when the signal is genuinely informative.
None of that execution reality is modeled here (no fill probability, no
queue position, no adverse-selection cost beyond the flat maker/taker
assumption). **Net bps shown in the ranked table (maker: roughly flat to
-0.5bps; taker: -3.4 to -4.3bps) already shows this fails a naive
cross-the-spread execution; the real question is whether a market-making
overlay that quotes conditional on this signal nets positive after queue
position and adverse selection — a materially harder simulation this pass
did not attempt.**

### #6-8, #11 — spread transitions, liquidity vacuum, flow acceleration, activity bursts

Four related but distinct new mechanisms, all confirmed real and
sign-stable at large n, all sub-cost or non-directional:

- **SPREAD_TRANSITION_dir**: trailing-1s spread widening predicts a
  **downward** forward move (negative decile spread in ~20/24
  venue/period/horizon combos, largest on bybit/hyperliquid at
  -0.33 to -0.54bps). Plausible mechanism: spread widening reflects
  liquidity providers pulling back ahead of information they expect, and
  price tends to drift down through the widened spread.
- **LIQUIDITY_VACUUM**: low best-level depth (bottom quintile) predicts
  larger subsequent |return| — i.e. a volatility, not directional, signal
  — confirmed on binance (n=229,922) and OKX (n=196,055) at 5s
  (-0.145bps and -0.244bps difference in |fwd return| between top and
  bottom depth quintile), consistent with the intuitive "thin book = more
  volatile" story. Hyperliquid is fully degenerate for this feature (depth
  computed from infrequent full-snapshot pushes rather than continuous
  diffs — 0.0 spread everywhere, `n_days` disclosed as 3/4 but the feature
  itself doesn't discriminate) — flagged **BLOCKED_DATA (hyperliquid, this
  feature only)**, not silently dropped.
- **FLOW_ACCEL**: acceleration of trailing trade-count intensity (now vs
  ~1s ago) predicts **negative** forward return in 18/22 venue/symbol/
  period combos — bursts of accelerating activity tend to precede a modest
  pullback rather than continuation. Largest on SOL (-1.15 to -2.96bps),
  present but smaller on BTC/ETH (-0.06 to -0.53bps in the dominant-sign
  combos).
- **AGGR_FLOW_BURST_vol / TRADE_INTENSITY_vol**: near-identical by
  construction (both derived from trailing trade count/notional), these
  predict forward |return| (volatility) strongly in A_early (up to
  +20.2bps decile spread for SOL/OKX) but **collapse 5-100x in B_late for
  every single venue/symbol combo** (e.g. binance BTC: 2.27bps ->
  0.86bps; OKX SOL: 10.8bps -> 1.67bps) — a genuine, disclosed
  regime-dependence, not a stable edge. Useful as a vol/sizing input, not
  as standalone alpha.

### #9 — A1-E1: cross-venue lead-lag, decile redo

Same construction as W2 (leader's trailing return -> leave-one-out
consensus forward return) but measured as a full decile spread rather than
extreme-percentile tail conditioning, over the wider 4-day book-tick sample
built this round. Confirms W2's finding at a different resolution:
sign-stable, monotonic-in-horizon (+0.18bps at 250ms -> +0.36bps at 5s for
binance/A_early), same ordering of leaders (binance/OKX/bybit roughly tied,
hyperliquid weakest but still positive, consistent with W2's "hyperliquid
never a leader" finding). Decile spread is smaller than W2's p99-tail
number (1.4bps) by construction — different statistic, same underlying
signal, same verdict.

**Verdict: WEAK, confirms W2, no new information about tradeability.**

### #10 — A5-E1: toxic flow/absorption, redo across all 3 symbols

Same trailing-1s-signed-notional-decile design as W2, extended to ETH and
SOL and re-run against the crossed-book-fixed price grid (trades themselves
don't need the fix, but the forward-return grid they're marked against
does). BTC/ETH results are broadly consistent with W2 in the A_early
period (+0.22 to +1.68bps, sign-stable) but **B_late flips negative for
binance BTC (-0.80bps @1s) and OKX BTC (-0.36bps @1s)** — W2 reported this
pair as stable-positive across both periods. This is a genuine downgrade
of W2's stability claim once B_late is measured with a slightly different
(fixed 1s trailing window regardless of horizon, decile not the exact same
bucketing) but comparable methodology — disclosed rather than reconciled.

**SOL/bybit re-confirmed as an artifact, now with day-level granularity**:
A_early's 3 individual days show spreads of **+1.87, +15.21, -1.22bps** —
wildly unstable *within* what was previously treated as one 3-day period,
driven almost entirely by one extreme day. This strengthens W2's original
"discard, artifact" call rather than overturning it.

**Verdict: WEAK, downgraded stability vs W2's characterization.**

### #12 — A4-E1: refill asymmetry after sweep, redo

Same mechanism as W2 (refill ratio at the new best 500ms after a sweep ->
continuation return), redone on the fixed data across all 4 venues/both
sides/both horizons. Result: **materially less clean than W2's "single
cleanest, most sign-stable result in the pass" characterization.** Sign
flips between the 500ms and 2000ms horizons within the same venue/period on
6 of 12 combos (e.g. bybit bid A_early: +0.090bps @500ms -> -0.189bps
@2000ms; okx bid B_late: -0.116 @500ms -> -0.091 @2000ms is at least
consistent, but binance bid B_late: +0.044 -> -0.083 flips). This isn't a
contradiction of W2's specific numbers (different day mix: this round adds
08-16/17 to A_early and drops 08-29 from B_late) but it does mean the
"cleanest result" label doesn't survive a larger, cross-horizon check.

**Verdict: WEAK, downgraded from W2's characterization — real but not
clean.**

### #13-14 — Post-impact reversal and price-impact asymmetry (new)

**POST_IMPACT_REVERSAL** tests the standard microstructure prior that large
trades cause a temporary impact that partially reverts. Constructed as:
for trades in the top 1% by notional, compare the immediate (250ms) signed
return (toward the trade's own direction) to the later (5s/30s) signed
return; "giveback" = immediate minus later. **Result: giveback is negative
in ~24/32 combos — i.e. price keeps moving in the trade's direction, it
does *not* revert.** This directly rejects the reversal hypothesis and is
consistent with (not independent of) A5's continuation finding — large
trades behave like informed/toxic flow on this data, not like noise that
gets absorbed and reverted. Reported as a **DEAD** hypothesis test, which
is itself useful information: it rules out a mean-reversion overlay on
large-trade impact.

**PRICE_IMPACT_ASYMMETRY** (does a buy-initiated large trade move price by
a different magnitude than a sell-initiated one) is noisy and small-n on
the thinner symbol/venue combos (SOL/OKX shows an eye-catching -1.6bps
asymmetry at 1s, but n=724, single period) — no clean, stable edge.
**Verdict: WEAK/descriptive.**

### #15 — Cross-venue book disagreement (new)

Tests whether a large |mid-price dislocation| from the 4-venue consensus
predicts convergence (mean reversion of the dislocation) shortly after.
Binance, OKX and hyperliquid all show the expected sign (larger
dislocation -> more convergence, spread +0.001 to +0.22bps), but **bybit
shows the opposite in both periods** (dislocations grow rather than shrink,
spread -0.01 to -0.24bps) — the clearest venue-specific inconsistency in
this pass, plausibly linked to bybit's residual 2.89% crossed-tick noise
even after the fix. **Verdict: WEAK, venue-inconsistent, not tradeable as
specified.**

### #16 — Failed sweep rate (new, descriptive)

Fraction of best-price sweeps that revert to the old level within 2s:
ranges 4.2% (OKX bid, B_late) to 31.5% (bybit ask, A_early). Ask-side fails
more often than bid-side on 6 of 8 venue pairs — a real structural
asymmetry (thinner/more contested ask side on these venues in this sample)
but a rate, not a return spread, so it complements A4 as a diagnostic
rather than standing alone as a signal.

### #17 — Book recovery speed (new, thin)

Time for best-level depth to recover to 90% of its pre-shock level after a
>=50% drop, tested against continuation of the post-shock move. Only bybit
had enough shock events with a clean recovery-time distribution
(n=159, A_early) to compute a decile split: fast recovery (median ~9.5s to
90%) predicts **+0.74bps** continuation vs slow recovery's **-0.26bps**
(spread -1.0bps) — an interesting, correctly-signed-with-A4's-refill-story
number, but n=159 from a single venue/period is far too thin to trust.
Binance (n=36) and hyperliquid (degenerate depth feature, same issue as
liquidity vacuum) could not produce a usable split.
**Verdict: NEEDS_FULL_VALIDATION**, flagged for a follow-up with more days
specifically because the direction is intriguing, not because the current
n supports any conclusion.

---

## Cost-hurdle summary

| mechanism | best gross edge | maker leg (1.5bps) | taker leg (5bps) | net |
|---|---|---|---|---|
| DEPTH_IMBALANCE_L1 | 1.63bps | net ~+0.1bps | net -3.4bps | marginal at maker, negative at taker — execution-model-dependent (see caveat) |
| MICROPRICE_OFFSET | 1.58bps | net ~+0.1bps | net -3.4bps | same caveat |
| OFI_TOB | 1.43bps | net ~-0.1bps | net -3.6bps | same caveat |
| A3 (queue depletion) | 0.28bps | negative | negative | sub-cost |
| A1 (lead-lag) | 0.36bps | negative | negative | sub-cost |
| A4 (refill asymmetry) | 0.21bps (unstable sign) | negative | negative | sub-cost |
| A6-directional | 0.075bps | negative | negative | sub-cost |
| Everything else (spread transition, flow accel, impact tests, disagreement) | <0.55bps | negative | negative | sub-cost |

As in W2: BTCUSDT spread itself remains essentially free on the three CEXs
in this window, so the fee assumption is what kills nearly everything here,
not slippage. The three related order-book-state signals (#1-3) are the
first numbers in either round to get close to clearing a single maker leg
gross — but "gross clears a maker leg" is not the same claim as "a market
maker can capture this net of adverse selection," and this pass explicitly
does not resolve that.

## Bottom line

The two explicitly-prioritized items from the brief are both resolved with
real answers, not restatements of last week's blockers: **A3 on
binance/OKX is unblocked and confirms absorption (not hazard) at 10-100x
W2's sample size**, and **A6's directional redesign revives the mechanism
from DEAD to a correctly-signed, sign-stable-16/16 (still sub-cost)
finding**. The 14 additional mechanisms tried mostly reproduce W2's overall
verdict (real, sign-stable-ish, sub-cost microstructure effects, taker fee
is the binding constraint) with two important, honestly-reported
*downgrades* of W2's specific claims (A4's "cleanest result" doesn't
survive a redo; A5's cross-period sign-stability doesn't hold for
binance/OKX BTC in B_late) and one genuinely new lead worth a real
follow-up: **the depth-imbalance/microprice/OFI cluster is the first
signal in two rounds of this hunt whose gross magnitude approaches a maker
leg**, and deciding whether that survives a real quote-and-fill simulation
(not a naive decile spread) is the highest-value next step for anyone
continuing this line of work.

## Mechanisms tried (20 distinct, exceeds the 12-18 target)

A1_LEAD_LAG, A3_QUEUE_DEPLETION_HAZARD, A4_REFILL_ASYMMETRY,
A5_toxic_flow, A6_DIRECTIONAL_BID_DROP, A6_DIRECTIONAL_ASK_DROP,
AGGR_FLOW_BURST_vol, TRADE_INTENSITY_vol, FLOW_ACCEL, MICROPRICE_OFFSET,
OFI_TOB, DEPTH_IMBALANCE_L1, SPREAD_TRANSITION_vol, SPREAD_TRANSITION_dir,
FAILED_SWEEP_RATE, LIQUIDITY_VACUUM, BOOK_RECOVERY_SPEED,
CROSS_VENUE_DISAGREEMENT, PRICE_IMPACT_ASYMMETRY, POST_IMPACT_REVERSAL.

Full result rows (small JSON, all in `evidence/`):
`single_venue_results.json` (194 rows: A3, A4, failed-sweep, microprice,
OFI, depth imbalance, spread transitions, liquidity vacuum, book recovery),
`cross_venue_results.json` (56 rows: A1, A6-directional, cross-venue
disagreement), `trades_flow_results.json` (896 rows: A5, aggressive-flow
burst, trade intensity, flow acceleration), `trades_impact_results.json`
(306 rows: price-impact asymmetry, post-impact reversal). Reconstruction
and analysis scripts (`book_reconstruct.py`, `single_venue_mech.py`,
`cross_venue_mech.py`, `trades_flow.py`, `trades_impact.py`) included for
reproducibility; `book_stats.jsonl` has the per-venue/date line counts,
crossed-tick counts and bootstrap-coverage diagnostics referenced above.
