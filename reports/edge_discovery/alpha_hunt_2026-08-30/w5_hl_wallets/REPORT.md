# W5 — Hyperliquid wallet identity: mining beyond A11

## Executive summary

**Data**: `data/hyperliquid/{trades,l2,ctxs,twap}` — 36,510,524 trades, 12 coins (BTC, ETH,
HYPE, SOL, XRP, DOGE, LINK, SUI, ADA, BNB, AVAX, LTC), 213,246 unique wallets, 2026-07-18 →
2026-08-29 (42 days). Same underlying dataset as A11 (`alpha_hunt_2026-08-29/w5_options_wallet_exec`),
mined further for genuinely different constructions rather than re-running A11 verbatim.

**Core artifact**: a 73,021,048-row long-format wallet-trade ledger (one row per trade × wallet
role: buyer +1 / seller -1), each row carrying its own forward markout at 7 horizons
(1s/5s/30s/60s/300s/900s/3600s), computed as a trade's own forward price move from the coin's own
public trade tape (`np.searchsorted` per coin — this is a trade's own forward return, never a
wallet score, so it carries no leakage risk by itself). Building this in DuckDB directly
(`ASOF JOIN`) OOM-killed twice on this shared, memory-constrained machine (other alpha-hunt
workers running concurrently); rebuilt with a lean per-coin numpy pass instead — see Engineering
notes at the end.

**17 parameterized mechanisms tried across 15 named IDs** (M1–M15, several with 2+ splits/variants).
**None clears PROMISING outright.** One (M3, wallet timing-skill fade) and two of its close
relatives (M7, M14b) show large raw numbers that clear the 4–9bps HL cost band, but all three sit
on the same fault line: the 16-day TEST window saw an exceptional, broad rally (all 12 coins
+9.5% to +43.9%), and every one of these mechanisms is a **structurally long-biased fade** (the
only slice compatible with `SHORT_REJECTED`). Once compared to the population baseline (same
regime, same drift), the excess edge shrinks a lot (M3) or the number is simply unstable
across the two test sub-halves (M14a/M14b decay 3–23x from first half to second). None is
called PROMISING; the most interesting is labeled **NEEDS_FULL_VALIDATION** pending a
non-rallying period.

| rank | mechanism | horizon | wallets | events (test) | gross bps | net bps @4bps / @9bps | stability | capacity | confidence | status |
|---|---|---|---|---|---:|---:|---|---|---|---|
| 1 | M3 — wallet timing-skill "fade the sell" (long-only slice) | 300s | 1,769 (top5% cohort) | 790,574 | +9.55 | +5.55 / +0.55 | sign-stable both test halves; excess-over-baseline shrinks to +6.42 | large (n in the hundreds of thousands) | wallet-level p=1.4e-226, CI clear of 0 — but confounded with test-window drift | **NEEDS_FULL_VALIDATION** |
| 2 | M14b — informed + rising-OI, fade-the-sell | 300s | 410 (top1% cohort) | 29,564 | +21.05 | +17.05 / +12.05 | **NOT stable**: h1 -35.9 to h2 -7.2 (~5x decay) | medium | biggest raw number in the sweep, least validated | **NEEDS_FULL_VALIDATION / likely regime artifact** |
| 3 | M7 — anti-informed (bottom-1%) fade-the-sell (long-only slice) | 300s | 409 (bottom1% cohort) | 57,400 | +10.57 | +6.57 / +1.57 | sign-stable both halves (-7.09/-4.30) | medium | overlaps M3's mechanism, same drift confound | WEAK / likely not independent of #1 |
| 4 | M14a — informed + positive-funding, fade-the-sell | 300s | 410 | 56,137 | +13.55 | +9.55 / +4.55 | **NOT stable**: h1 -21.6 to h2 -7.4, sign flips at 60s | medium | same drift confound as #1/#2/#3 | WEAK / regime artifact |
| 5 | M1 — wallet markout score (magnitude), split A (~A11 replication) | 60s | 410 (top1%) | 128,428 | +1.58 | -2.42 / -7.42 | sign-stable both halves (+0.61/+2.22); wallet-level p=0.26, CI crosses 0 | medium | replicates A11 closely | WEAK (as A11 already found) |
| 6 | M13 — informed + book-imbalance conditioning ("with the book") | 60s | 410 | 66,612 | +2.99 | -1.01 / -6.01 | sign-stable both halves (+1.28/+4.10) | medium | clean, sensible, but sub-cost | WEAK |
| 7 | M1 — same score, split B (train ends 08-07, longer test) | 60s | 351 (top1%) | 142,928 | +0.29 | -3.71 / -8.71 | **NOT stable**: h1 +1.84 to h2 -0.05 | medium | shows the A11-style edge is split-sensitive | WEAK — exposes fragility of #5 |
| 8 | M6 — informed cohort net-flow aggregation (15-min buckets, IC) | next 15m | 410 | 5,291 buckets | IC=0.023 (p=0.10) | n/a (IC-based) | **NOT stable**: h1 IC=+0.045 to h2 IC=-0.012 | large (basket-level) | quintile spread suggestive (-2.8→+6.2bps) but IC itself unstable | WEAK/inconclusive |
| 9 | M12 — synchronized informed wallets (2-wallet cluster, 60s window) | 60s | ⊂top1% | 22,488 | +2.87 | -1.13 / -6.13 | roughly stable (h1 +1.14/h2 +4.05) | small | non-monotonic across cluster sizes (3-4 flips negative; 5+ wildly unstable, likely tail artifact) | WEAK/mixed |
| 10 | M9 — wallet disagreement/dispersion → forward vol expansion | next 5m | all wallets | 123,028 buckets | IC=0.043 (pooled p=1.6e-50) | n/a | **NOT stable**: h1 IC=0.002 (p=0.69, null) to h2 IC=0.056 (p=3.9e-50) | large | pooled significance driven entirely by 2nd half | WEAK/inconclusive |
| 11 | M2 — wallet win-rate score (not magnitude) | 60s | 410 (top1%) | 80,943 | -4.76 | worse | negative/near-zero across almost every split×pct combo | medium | win-rate is a poor skill proxy | DEAD |
| 12 | M5 — whale cohort (size only, not skill-ranked) | 60s | 410 | 21,057,119 | +0.41 | -3.59 / -8.59 | sign-stable, decaying (h1 0.48→h2 0.38) | huge | confirms size ≠ skill (contrast to M1) | WEAK, sub-cost |
| 13 | M8 — wallet crowding (all wallets, 5-min buckets, market-wide) | next 5m | all wallets | up to 89,145/bucket-type | +0.0003 to +0.42 | deeply sub-cost | sign-stable, monotonic in crowding | huge | real but 10-20x too small | DEAD, sub-cost |
| 14 | M4a — size anomaly, symbol-relative (taker-corrected) | 60s/300s | all wallets | 36.5M | ~flat 0.4-0.9bps across all z-buckets | sub-cost | no monotonic pattern by z-bucket | huge | no size-surprise signal detected | DEAD |
| 15 | M4b — size anomaly, wallet's-own-history-relative | 60s/300s | 55k+ elig. | 37.3M | -0.33 to +0.20bps | sub-cost | no clean pattern | huge | no signal | DEAD |
| 16 | M10 — wallet position build-up (streak length, informed cohort) | 60s | ⊂top1% | 129,977 | non-monotonic, 0.86-4.40bps | sub-cost throughout | full-streak (largest n) is the WEAKEST and unstable | medium | opposite of hypothesized | DEAD |
| 17 | M11 — wallet reversal (flip vs continuation, all wallets) | 60s | all wallets | 72.5M | flip -0.04, cont +0.01 | sub-cost | **NOT stable**: flip h1 -0.14 to h2 +0.01 | huge | negligible, unstable | DEAD |
| 18 | M15 — wallet flow before liquidation-proxy (OI-drop z≤-3) events | pre-15m | ⊂top1% | 7,348 events (2,519 w/ informed activity) | corr=-0.007, sign agreement 51.9% | n/a | null | n/a | no detectable pre-positioning | DEAD |

Rows are ranked by raw gross bps for legibility, **not** by confidence — the ranking's top three
are exactly the ones flagged least trustworthy in the narrative below. If ranked by
confidence-adjusted merit instead, M13 (clean but sub-cost) and M1-split-A (replicates A11
faithfully) would sit above M14b/M14a/M7.

---

## Causality enforcement — how "strictly past-only" was verified per mechanism

**The core discipline, used everywhere a wallet is SCORED (M1, M2, M3, M5, M6, M7, M10, M12,
M13, M14, M15):** a single global chronological cutoff, `split_ms`. A wallet's score is computed
*only* from its trades with `t0 < split_ms` (SPLIT_A = 2026-08-13 00:00 UTC, matching A11 for
comparability; SPLIT_B = 2026-08-07 00:00 UTC as a genuinely different lookback for M1's
robustness check). Every trade used to *evaluate* a cohort has `t0 >= split_ms`. Because TRAIN
and TEST are disjoint, non-overlapping wall-clock intervals with TRAIN entirely first, **no test
trade — including the very trade being evaluated — can ever enter the wallet's own score.** This
was verified directly by construction (the SQL `WHERE t0 < {split_ms}` / `WHERE t0 >= {split_ms}`
clauses are mutually exclusive and exhaustive over time, not over row identity), not just assumed.

**M3 (timing skill)** adds one more causal layer inside TRAIN itself: the rolling price
mean/std used to z-score each TRAIN trade is computed with
`.rolling(500).mean().shift(1)` — the `.shift(1)` strictly excludes the trade's own price from
its own baseline, so the z-score at trade *i* uses only trades *i-500..i-1*. Verified by
inspecting that `valid_z` count in the build log is always slightly less than `n` (a handful of
trades near the start of each coin's history have no full window yet and are correctly dropped,
not zero-filled).

**M4a, M8, M9, M11 (no wallet cohort at all — pure event/bucket features)**: these compute a
feature from data strictly *before* the timestamp being evaluated (a `ROWS BETWEEN 500 PRECEDING
AND 1 PRECEDING` window for M4a's size z-score; bucket *b*'s crowding/entropy feature vs bucket
*b+1*'s realized outcome for M8/M9; `LAG(dir)` — the wallet's *immediately preceding* trade — for
M11). None of these involve a persistent wallet identity/score, so there is no train/test split
to speak of; causality is enforced purely by the strict "feature ends before outcome begins"
timestamp ordering, verified by construction of the `PRECEDING`/`LAG` window frames (DuckDB
raises no rows where the frame would include the current or a future row given these frame
specs).

**M4b (wallet's own history)**: reuses the SPLIT_A discipline — a wallet's own TRAIN-period
median notional (not train markout) scores its TEST trades' size ratio.

**M13/M14 (book state / funding-OI conditioning)**: `ASOF JOIN ... ON e.t0 >= r.time_ms` —
backward-looking, i.e. the L2/ctxs snapshot *at or immediately before* the trade's own
timestamp. This is the market's own already-public state at the time of the trade, not future
information; verified by the join direction (confirmed empirically on a toy example before use,
see Engineering notes) and by construction of the inequality (`>=`, never `<=`).

**M15 (pre-liquidation flow)**: the informed cohort's net flow is summed over
`[event_ms - 15min, event_ms)` — strictly before the event's own timestamp — via a DuckDB range
join, and restricted to `t0 >= SPLIT_A` (TEST only) so that cohort membership (fixed from TRAIN)
can never be informed by trades occurring around a TRAIN-period event.

**Explicit verification performed**: for M1/M2/M3/M5/M6/M7/M10/M12/M13/M14/M15, cohort wcode
sets were built once from the TRAIN-only parquet (`wallet_scores_A.parquet` /
`wallet_scores_B.parquet` / `wallet_timing_scores.parquet`) and then joined against a
*separately filtered* TEST-only view of the ledger — the two never share a code path that could
accidentally leak a `t0 >= split_ms` row into the scoring query. No mechanism in this report
scores a wallet using any trade at or after the timestamp being evaluated.

---

## Mechanism narratives

### M1 — Wallet markout score (magnitude), two splits (~A11, extended)

Same construction as A11 (mean markout of TRAIN trades ranks wallets), extended with a
**second, different train/test split** to test robustness to the choice of cutoff. SPLIT_A
(train ends 2026-08-13, matches A11) reproduces A11's own finding closely: top1% gross
`markout_60s` = **+1.58bps**, sign-stable across both TEST sub-halves (+0.61 / +2.22),
wallet-level Mann-Whitney p=0.26, bootstrap CI [-0.96, +3.40] crossing zero — essentially the
same WEAK/inconclusive verdict A11 reached independently.

SPLIT_B (train ends 2026-08-07, a full week earlier — shorter train, longer test) is **weaker
and NOT sign-stable**: top1% gross `markout_60s` = +0.29bps, with h1=+1.84 flipping to h2=-0.05.
This is a genuinely new finding: **the already-thin A11-style edge is sensitive to which period
gets used as train vs test**, which further undermines confidence that it reflects a persistent
property of specific wallets rather than a period-specific artifact. Non-linearity check
(0.5/1/2/5% cuts): no clean monotonic improvement into the most extreme tail — bottom-0.5%
cohorts sometimes show *larger* positive numbers than top-0.5% (small-n noise from wallet-notional
concentration, same caveat A11 already flagged).

### M2 — Wallet win-rate score (different score construction)

Instead of the magnitude-weighted mean markout, score wallets by TRAIN win-rate (fraction of
trades with `markout_60s > 0`). Result: **uniformly negative or near-zero** across nearly every
cohort size and both splits (top1% split A = -4.76bps, split B = -6.46bps). **DEAD** — win-rate
is evidently a poor skill proxy on this data (a wallet can have a high hit-rate while losing on
average, classic small-wins/big-losses pattern), confirming magnitude-based scoring (M1) is
directionally the right idea even though still weak.

### M3 — Wallet timing skill ("buys the dip / sells the rip"), and its fade

Hypothesis: does a wallet's historical tendency to trade at favorable local prices (relative to a
trailing, causal 500-trade rolling window) predict its future markout? Score = TRAIN-period mean
of `dir x (-z)` where z is the causal price z-score. Result was the **opposite of hypothesized**:
the cohort that historically bought dips/sold rips shows **negative, highly significant** forward
markout (top5%: gross60=-3.00bps, both halves negative, wallet-level Mann-Whitney p=6.1e-54);
the "anti-timed" (momentum-chasing) cohort shows small positive markout. At short horizons,
continuation dominates mean-reversion here, so "buying below the recent local average" is
actually the wrong side, not the right one.

Splitting by the wallet's own trade direction reveals the standout number of the sweep: **the
top-timing cohort's SELL trades** (dir=-1) show markout of -5.27 to -14.68bps depending on
cohort size and horizon — meaning **fading them (buying right after they sell) is long-only,
SHORT_REJECTED-compliant, and profitable on paper**: top5% cohort, gross +9.55bps@300s
(+14.68bps@900s), sign-stable in both test sub-halves, wallet-level p=1.4e-226, notional
concentration 57-91% in top 10 wallets depending on cohort width (comparable to A11's own 54%
caveat). Net of 4-9bps cost: **+5.55/+0.55bps@300s, +10.68/+5.68bps@900s** — clears both cost
bounds at the longer horizons.

**But**: the 16-day TEST window saw every single coin rally +9.5% to +43.9% — an
extraordinary, broad bull run. The unconditional baseline (*any* wallet selling during this
window, informed or not) already shows -3.42bps@300s / -6.13bps@900s purely from that drift.
Comparing the cohort against the population of other similarly-active (`n_train>=30`) sellers in
the *same* window (which faces the same drift) shrinks the excess to **+6.42bps@300s /
+8.84bps@900s** — smaller, but still real and still the best-supported finding in this report.
Because SHORT_REJECTED forces this into a structurally long-only rule, a meaningful share of the
raw backtested P&L is just directional beta during an unusually favorable window, not
demonstrated wallet-selection skill. **Verdict: NEEDS_FULL_VALIDATION** — retest in a flat or
down period before trusting the magnitude.

### M4 — Size anomaly (two variants)

**M4a (symbol-relative, taker-corrected)**: rolling 500-trade mean/std of trade size per coin
(`ROWS BETWEEN 500 PRECEDING AND 1 PRECEDING`), scored only the aggressor/taker side (using HL's
`side` field to identify which of buyer/seller was the taker — the theoretically correct
"informed order flow" side per Kyle's-lambda-style microstructure literature). Result: flat
~0.4-0.9bps across every size-surprise bucket, no monotonic relationship. **DEAD** — unusually
large taker orders carry no more directional information than typical ones on this data.

**M4b (wallet's-own-history-relative)**: bucketed TEST trades by ratio to that same wallet's own
TRAIN-period median notional. Result: -0.33 to +0.20bps, no clean pattern. **DEAD**.

### M5 — Whale activity (size only, not skill-ranked)

Cohort = top 1/2/5% by TRAIN cumulative notional (mere size, explicitly *not* the markout-ranked
cohort of M1). Result: gross60 = +0.28 to +0.41bps, tiny but sign-stable. **WEAK, sub-cost** —
this is a clean, useful negative contrast to M1: mere size does not carry the same (already thin)
signal that skill-ranking does, confirming size and skill are meaningfully different things here.

### M6 — Informed-wallet net-flow aggregation (basket-level signal)

Using the M1 top-1% cohort, bucketed their TEST-period trades into 15-min x coin windows and
tested whether the cohort's net signed notional flow in a bucket predicts the coin's own forward
return over the *next* bucket. Pooled Pearson IC = 0.023 (p=0.10, borderline), but **NOT stable**:
h1 IC=+0.045 vs h2 IC=-0.012 (sign flip). The quintile breakdown looks suggestive (mean forward
return rises monotonically from -2.8bps in the most-sold quintile to +6.2bps in the most-bought
quintile) but this pattern was not itself stability-tested, and the underlying linear IC already
failed the stability bar. **WEAK/inconclusive** — a portfolio-level construction worth revisiting
with more data, not a confirmed signal today.

### M7 — Anti-informed / consistently-bad wallets: fade, long-only slice

Per HARD RULE 3: fading a bad wallet's *buy* would require shorting (SHORT_REJECTED, not
deployable). Only the *sell*-side fade (buying after a bad wallet sells) is long-only-eligible.
Bottom-1% cohort (worst TRAIN markout score): dir=-1 (their sells) gross60=+5.43bps,
gross300=+10.57bps, sign-stable both halves (-7.09/-4.30 for their own outcome). Net of cost:
+6.57/+1.57bps@300s — clears both bounds, barely. The reciprocal dir=+1 slice (their buys) shows
markout +5.6 to +12.0bps — i.e. even the "bad" cohort's buys look good in this window, almost
certainly because the massive rally rewards buying regardless of who does it; correctly
**not deployable anyway** since fading it would be a short. This mechanism is very likely
capturing much the same underlying phenomenon as M3 (same drift-favored fade-the-sell
construction, probably overlapping membership) rather than an independent discovery. **WEAK,
same caveats as M3, not confirmed as additive.**

### M8 — Wallet crowding (market-wide, not cohort-conditioned)

5-minute buckets, all wallets: `crowding_ratio` = share of distinct wallets on the dominant side.
Forward return in the direction of the dominant side rises monotonically with crowding
(0.0003bps at 50-60% crowded to 0.42bps at >=90% crowded), sign-stable across halves. **DEAD,
sub-cost by more than an order of magnitude** — real microstructure regularity, no tradeable
size, same pattern as several DEAD entries in the prior sweep (A1, A5).

### M9 — Wallet disagreement/dispersion → forward volatility expansion

Entropy of the buy/sell wallet split within a 5-min bucket, tested against realized range-vol in
the *next* bucket. Pooled IC=0.043 (p=1.6e-50, huge n) with a clean monotonic quintile pattern
(18.0→21.4bps range-vol from low to high dispersion). But **the pooled significance is driven
entirely by the second test-period half** — h1 IC=0.002 (p=0.69, null), h2 IC=0.056 (p=3.9e-50).
**WEAK/inconclusive** — a textbook example of why single-period pooled significance isn't
sufficient on its own.

### M10 — Wallet position build-up (gradual accumulation vs one-off)

For the M1 informed cohort, streak length (0-5, via causal `LAG` chain) of same-direction TRAIN
history preceding a TEST trade. No monotonic pattern (4.30/4.24/0.86/3.08/4.40/1.06bps for
streak 0 through 5); the full 5/5 build-up streak — the largest bucket by n (99,337) — shows the
**weakest** effect and is **not stable** (h1=-0.02, h2=+1.79). **DEAD** — gradual accumulation
does not sharpen the (already weak) informed signal; if anything mildly the opposite of
hypothesized.

### M11 — Wallet reversal (flip vs continuation)

All wallets, all trades: does a direction flip vs. the wallet's own immediately preceding trade
(same coin) predict different forward markout than continuation? flip mo60=-0.043bps vs
continuation +0.013bps — both negligible, and flip is **not stable** (h1=-0.144, h2=+0.007,
sign flip). **DEAD**.

### M12 — Synchronized informed wallets (cluster size)

Within the M1 informed cohort's TEST trades, count of distinct informed wallets trading the same
side within a trailing 60s window (causal `COUNT(DISTINCT...)` window). Isolated (n=1): 0.23bps;
2-wallet cluster: 2.87bps (roughly stable, h1=1.14/h2=4.05); 3-4 wallet cluster: -3.24bps (sign
flip, inconsistent); 5+ cluster: +14.59bps but wildly unstable (h1=1.62 vs h2=44.0 — almost
certainly a small-n tail artifact, not a real effect). **WEAK/mixed** — the 2-wallet-cluster
result is the only piece worth a second look; the pattern overall is too non-monotonic to
promote.

### M13 — Informed wallet + book-state conditioning

The M1 informed cohort's trades, backward-ASOF-joined to the nearest prior L2 snapshot for that
coin. Trading **with** the visible book imbalance (buying when bid-heavy, selling when
ask-heavy) shows gross60=+2.99bps, sign-stable both halves (+1.28/+4.10); trading **against**
the book is flat/noisy (+0.07/-0.16, no stable sign). **WEAK but a clean, honest, sign-stable
conditioning result**: whatever thin signal the base informed cohort carries concentrates in
book-confirmed trades — still sub-cost in absolute terms (2.99bps < 4bps floor), but a real,
interpretable refinement of M1 rather than noise.

### M14 — Informed wallet + funding/OI state

Backward-ASOF-joined to `ctxs` (funding, open interest). Both cuts (funding>0 "crowded long" x
their sells; OI-rising "position building" x their sells) show large raw fade numbers
(+13.55bps@300s and **+21.05bps@300s** respectively — the single biggest number in this entire
sweep for OI-rising). A dedicated half-split stability check (run after the main pass,
`evidence/baselines_and_stability.json`) shows **neither is stable**: funding>0 case decays
from -21.6bps (h1) to -7.4bps (h2) at 300s and flips sign at 60s; OI-rising case decays from
-35.9bps (h1) to -7.2bps (h2), a ~5x collapse. **Verdict: NEEDS_FULL_VALIDATION, but flagged as
the least trustworthy of the "big number" mechanisms in this report** — the biggest raw figure in
the sweep is also the one with the weakest stability evidence, almost certainly because it's
picking up the most concentrated slice of the same broad-rally/OI-buildup regime rather than a
repeatable phenomenon.

### M15 — Wallet flow before liquidation-proxy (OI-drop) events

Staying in-lane per the mission (characterizing the wallet side, not redoing the cascade
mechanism itself, which is W2's domain): built an OI-drop proxy (z<=-3 on trailing-5-snapshot OI
% change, z-scored against a trailing 50-snapshot window, same style as this project's existing
A7 proxy) — 7,348 TEST-period events across 12 coins. The M1 informed cohort had any trading
activity in the strictly-preceding 15 minutes for only 2,519 of them (34% — expected, given the
cohort is ~410 wallets). Correlation between pre-event net flow and the event's own outcome
return: **-0.007** (essentially zero); sign agreement 51.9% (coin-flip level); mean outcome
return nearly identical whether pre-flow was positive or negative (8.24 vs 7.86bps). **DEAD** —
no detectable informed positioning ahead of these liquidation-proxy events, at least via this
simple net-flow measure.

---

## Overlap and independence caveat

M3, M7, and M14a/M14b are **not independent discoveries**. All four are variations on "buy
right after some wallet-cohort sells" during the same 16-day, all-coins-rally TEST window, using
overlapping or identical (M14 literally reuses M1's top-1% cohort) wallet populations. Reporting
them as four separate rows is honest about what was tried, but they should be read as **one
underlying phenomenon (a fade-the-sell rule during an atypically strong rally) surfaced four
times through different conditioning lenses**, not four independent confirmations. None should be
promoted to PROMISING on the strength of "multiple mechanisms agree" — they agree because they
are largely the same bet.

## What would need to hold up before deployment

1. **A non-rallying (flat or declining) retest window.** Every large number in this report comes
   from the same exceptional bull run; HL's own collector is still running and will eventually
   provide a genuinely different regime to test against.
2. **Stability that survives the full test window split three or more ways**, not just two
   halves — M14a/M14b already fail even the two-way split.
3. **A market-neutral (or explicitly hedged) formulation**, since every deployable slice here is
   forced long-only by SHORT_REJECTED, which conflates wallet-selection skill with directional
   beta in a strongly trending sample.

## Engineering notes (for whoever picks this up next)

- Building the wallet-trade ledger via DuckDB `ASOF JOIN` **OOM-killed the process twice**
  (anon-rss 24.6GB and 11.6GB against a `memory_limit` pragma of 20GB/7GB respectively) on this
  shared machine, where several other alpha-hunt workers were running concurrently — the pragma
  does not appear to bound DuckDB's actual RSS under `ASOF JOIN` reliably in this version
  (1.3.2). Rebuilt with a per-coin numpy `searchsorted` pass instead (see `build_ledger3.py` in
  scratchpad, not copied into this report's evidence to respect the "small files only" rule) —
  peak RSS per coin stayed under ~3GB even for BTC (11.2M trades). Downstream cohort/mechanism
  queries (which only aggregate, not `ASOF JOIN`, the 73M-row ledger) ran fine directly in
  DuckDB with `memory_limit='6GB'`.
- `ASOF JOIN` direction was verified on a toy example before use for M13/M14/M15
  (`l.t0 >= r.time_ms` = backward/most-recent-at-or-before; confirmed empirically, not assumed
  from documentation).
- The intermediate ledger parquet cache (~2.4GB, `ledger/coin=*.parquet`) lives in this session's
  scratchpad, not under `reports/`, and will be cleaned up — nothing beyond the small JSON files
  in `evidence/` and this report was written under the repo.
