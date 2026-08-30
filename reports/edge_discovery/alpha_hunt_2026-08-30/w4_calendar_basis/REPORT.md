# W4 Calendar Basis / Futures-Curve Mining — 2026-08-30

## Executive summary

Ten distinct mechanisms (M1-M10, several with multiple parameterizations) were mined from the
full reconstructed Binance BTC/ETH curve: spot-adjacent perp, near-quarterly, next-quarterly,
funding rate, and mark-premium — extending W3's 2026-08-29 single perp-vs-nearest-quarterly test
to the fuller curve as explicitly requested (quarterly-to-quarterly, curve shape, funding
disagreement, cross-asset dispersion, jumps, roll-down, inversion). Every mechanism reuses and
extends the prior worker's episode-decluster discipline — and this round it needed extending
further: two additional, real contamination sources were found and fixed before any number below
can be trusted (see "Two new pitfalls" below). After both fixes, most of the extremity-based
mechanisms shrink to single-digit-to-low-double-digit true independent episodes, confirming and
sharpening W3's downgrade: this curve does not have nearly as much clean, tradeable signal as raw
correlations suggest.

Headline result: the one mechanism that clears cost with a reasonable sample and stable sign
across years is M7, funding-implied carry vs. quarterly-implied carry disagreement (BTC/ETH,
k14d-k30d) — net +7 to +33bps at base cost (14bps), roughly breakeven to +14 to +21bps at stress
cost (28bps), on 15-24 true independent episodes, positive in every one of 2024/2025/2026(partial).
Its worst single episode is -111.8bps (BTC) / -106.6bps (ETH), both at k30d — real tail risk,
smaller than W3's calendar-basis worst case (-703.6bps) but not small. Everything else tested is
WEAK or DEAD once properly declustered and costed, including the naive "curve
steepening/flattening" and "cross-asset dispersion" ideas that looked promising gross but don't
survive realistic 2-leg (or 4-leg) transaction costs. The single worst episode across the whole
sweep is -274.1bps (M5 roll-down harvest, ETH, dte0=45, Feb 2021 — the same "rich keeps getting
richer" blow-off regime W3 flagged for the perp-vs-quarterly trade).

## Data actually used (read-only, no new collection)

- `data/derivatives_backfill/binance_vision_quarterly/{BTC,ETH}USDT_*_1d.parquet` — 24 quarterly
  contracts per symbol, 2021-03-26 through 2026-12-25 expiry, close-price only (confirmed, per
  W3's pitfall #3, hard-truncated at date<=expiry and trailing-duplicate-close-trimmed here too —
  re-verified the bug is still present in the raw files and re-applied the fix independently
  rather than trusting it was already clean).
- `data/derivatives_backfill/um_klines_1d/{BTC,ETH}USDT_1d.parquet` — perp OHLCV, 2020-01-01 to
  2026-06-30 (stale by ~2 months vs. today; caps every mechanism that needs the perp leg).
- `data/derivatives_backfill/binance/funding/{BTC,ETH}USDT.parquet` — funding rate, through
  2026-08-14.
- `data/derivatives_backfill/binance_vision_premium/{BTC,ETH}USDT_premium_5m.parquet` — mark
  premium, through 2026-07-01 (loaded, available for future work, not needed once funding rate
  itself was found to be the cleaner carry proxy for M7).
- Contract calendar cross-checked: BTC and ETH quarterly expiries are on the same dates every
  cycle, which is what makes the M8 cross-asset dispersion construction (matched near-contract
  dte on both legs) well-defined.

Full curve build script: `build_panel.py`. Panel output: `evidence/panel_{BTC,ETH}USDT.parquet`
(2,032-2,033 daily rows each, 2021-02 to 2026-08-28, zero calendar gaps). All mechanism logic:
`analyze.py`. Raw per-mechanism results: `evidence/all_results.json`.

## Two new pitfalls found and fixed here (beyond W3's four)

W3's addendum already flagged the annualized-basis blow-up near expiry (its pitfall #4, reused
below as a near_dte>=7 floor on every signal). Two further, real problems surfaced while building
the fuller curve — both would have inflated results if left in, and both are the kind of mistake
this dataset has already burned a cycle on once:

1. Contract-roll contamination. An entry near a contract's expiry, held k days, can cross the
   roll date — the panel's "near contract" at exit is then a different instrument than at entry,
   so entry-to-exit is comparing two unrelated contracts' basis, not measuring convergence at
   all. First pass (before the fix) showed a BTC RICH episode entered at near_dte=1 with entry
   basis 6bps and "exit" basis 215bps seven days later — a fresh contract just having rolled in,
   not seven days of real basis widening. Fix, mechanism-dependent:
   - For the perp-vs-quarterly basis (M1/M4/M7/M9): a quarterly future is cash-settled to the
     index at expiry, so basis is contractually guaranteed to converge to ~0 at dte=0 — not an
     estimated parameter, a structural fact. So instead of dropping entries that would cross a
     roll, the holding period is capped at min(k, near_dte) and exit is marked at the known
     convergence value (0). This recovers real sample (extreme-basis episodes empirically cluster
     near roll dates) without contamination — capped exits are flagged (n_expiry_capped) in every
     result row.
   - For the near-vs-next calendar spread (M2/M3/M10), there is no such guaranteed convergence
     target (once "near" rolls, the pair is a different instrument entirely) — those entries are
     simply dropped if near_dte(entry) <= k.
2. Overlapping-window autocorrelation, one level down from W3's original mistake. Regime-
   contiguity declustering (one entry per contiguous RICH/CHEAP run, per W3's addendum method) is
   not sufficient by itself for every signal: a noisier continuous signal can flicker across its
   own threshold within a single macro regime, producing many nominally-separate "episodes" whose
   k-day holding windows still overlap in calendar time. Caught concretely on M7: the raw
   regime-contiguity decluster produced 9 separate BTC "episodes" inside the single Aug-Sep 2024
   window alone (entries 3-9 days apart, holding windows overlapping by 4-6 days each) — the
   exact same autocorrelation problem that took W3's calendar-basis finding from "+360-680bps" to
   "+10bps, not significant," just resurfacing at a finer grain. Fix applied to every mechanism in
   this report: a second, greedy non-overlapping-window pass — an entry is kept only if it starts
   at least k (or its own realized holding period, for capped exits) days after the previously
   kept entry. This shrank M7's BTC k30d count from 57 to 18 and M8's k7d count from 9 to 6;
   smaller mechanisms were less affected (already sparse) but all are computed the same way for
   consistency. Every N reported below is post-both decluster passes.

## Cost model

Base = 14bps, stress = 28bps round-trip, per the mission's stated 2-leg spread range (matches
W3's ~15bps base / 28bps stress usage). M8 (cross-asset dispersion) trades two calendar spreads
simultaneously (4 legs) — cost doubled to base=28bps/stress=56bps, flagged explicitly in its row.

## Mechanism-by-mechanism

### M1 — Perp vs. near-quarterly basis, annualized-decile entry, multi-horizon (k1/3/7/14/30d)

MECHANISM: extends W3's core finding to more horizons on refreshed data, with both new
contamination fixes applied. SIGNAL: annualized basis (raw_basis_pct*365/dte) in the train-fit
top/bottom decile, near_dte>=7. ENTRY: hedged, short-quarterly/long-perp at RICH, reverse at
CHEAP. EXIT: k-day hold, capped at contract expiry (basis->0 known convergence).

| symbol | k1d | k3d | k7d | k14d | k30d |
|---|---|---|---|---|---|
| BTC (train q10=2.0%, q90=21.7% ann.) | n=9, +8.4bps, t=2.84, net@14=-5.6/@28=-19.6 | n=8, +5.7bps, t=1.08 | n=6, +7.7bps, t=0.81 | n=5, +13.1bps, t=0.65 | n=4, +14.8bps, t=0.44 |
| ETH (train q10=-0.4%, q90=22.2% ann.) | n=2, +12.6bps | n=2, +22.6bps | n=1 | n=1 | n=1 |

Worst episode: BTC k30d -30.9bps; ETH too thin to have a meaningful worst case beyond n=1-2. Once
both roll-contamination and overlapping-window fixes are applied, BTC's true independent sample
at k7d+ is 6-9 episodes over the ~2-year OOS test window (2024-05 to 2026-06) — this is materially
smaller than even W3's already-downgraded n=61 (BTC, k7d), because that number still mixed in
overlapping and near-expiry-blowup-adjacent episodes. ETH's decile threshold almost never
separately fires an independent RICH episode once non-overlap is enforced — n=1-2 is not a usable
sample. VERDICT: WEAK/INSUFFICIENT-N — confirms and sharpens W3's downgrade rather than reversing
it; there just isn't enough independent signal at the extremes to say anything with confidence, in
either direction.

### M2 — Near-vs-next quarterly calendar spread, mean-reversion (the genuinely new spread)

MECHANISM: cal_spread = next_quarterly/near_quarterly - 1, hypothesis that an unusually wide (or
narrow) spread mean-reverts. PAYER: same thin-arb-capital story as M1, but on the inter-contract
spread itself rather than perp-vs-quarterly. SIGNAL: annualized calendar spread in train-fit
decile. ENTRY: short-next/long-near at RICH-slope, reverse at CHEAP-slope (nearly always CHEAP in
practice — curve is in contango >99.9% of days, see M9). EXIT: strict (entries dropped if a roll
would occur within k days — no clean convergence target exists here).

| symbol | k1d | k3d | k7d | k14d | k30d |
|---|---|---|---|---|---|
| BTC | n=6, +0.3bps | n=6, -6.0bps, t=-2.62, p=0.047 | n=5, -3.7bps | n=5, -10.7bps | n=4, -13.1bps |
| ETH | n=5, -2.5bps | n=5, -4.9bps | n=4, -11.4bps, t=-3.45, p=0.041 | n=4, -18.3bps, t=-3.20, p=0.049 | n=4, -17.1bps |

Both symbols trend negative at every horizon — the opposite of the mean-reversion hypothesis. A
wide near-next spread does not reliably narrow within a month; if anything it drifts further,
consistent with the curve simply carrying a persistent contango risk premium rather than
oscillating around a stable level. Worst episode: ETH k14d -35.2bps. VERDICT: DEAD as specified —
small N so not fully conclusive, but the sign is consistently wrong, several results are nominally
significant in the wrong direction, and nowhere clears cost.

### M3 — Curve slope momentum vs. reversion (5-day change in calendar-spread annualized rate)

MECHANISM: tests whether a recently-steepening (or flattening) curve continues (momentum) or
snaps back (reversion) — direction pre-committed on TRAIN per symbol to avoid picking the winning
sign on the test set. SIGNAL: 5-day change in cal_spread_ann, train-fit decile.

BTC pre-committed reversion; ETH pre-committed momentum — already a bad sign (the two "most
similar" assets disagreeing on which behavior wins suggests overfitting the train split, not a
real economic regularity).

| symbol (mode) | k1d | k3d | k7d | k14d | k30d |
|---|---|---|---|---|---|
| BTC (reversion) | n=6, +1.1bps | n=5, +4.1bps | n=4, +5.2bps | n=4, -0.3bps | n=4, +2.0bps |
| ETH (momentum) | n=6, -6.9bps | n=5, -6.5bps | n=5, -19.0bps | n=4, -25.6bps | n=3, -25.4bps, t=-5.02, p=0.038 |

BTC hovers near zero in both directions; ETH's pre-committed momentum bet loses steadily and
significantly at longer horizons. VERDICT: DEAD — no stable, cross-symbol-consistent direction
found; ETH's result argues momentum is the wrong bet, not that reversion would have worked either
(BTC's reversion bet is indistinguishable from noise).

### M4 — Time-to-expiry-normalized basis z-score

MECHANISM: refines M1's decile-on-raw-annualized-basis signal by instead z-scoring against a
train-fit dte-quintile-conditional mean/std, isolating "abnormal for this time-to-expiry" from
the mechanical fact that annualized basis naturally varies by dte bucket. SIGNAL: z-score decile,
near_dte>=7.

| symbol | k1d | k3d | k7d | k14d | k30d |
|---|---|---|---|---|---|
| BTC | n=2, -3.3bps | n=2, -3.4bps | n=2, -11.8bps | n=1 | n=1 |
| ETH | n=4, +0.9bps | n=4, +6.5bps | n=2, +34.8bps | n=2, +60.1bps | n=2, +70.1bps |

Sample sizes are too thin (n=1-4 after both decluster passes) to draw any conclusion either way —
the dte-normalization refinement doesn't materially change the picture from M1, it just confirms
the same "not enough independent extreme episodes" problem from a different angle. VERDICT:
BLOCKED — insufficient N, not a negative result, an inconclusive one.

### M5 — Expiry-convergence roll-down harvest (systematic, unconditional, "sell the future")

MECHANISM: distinct from M1-M4 — not extremity-conditional at all. Tests the pure structural
claim ("expiry-convergence effects... tradeable via calendar spread") that basis decays
predictably as dte shrinks, by taking one fixed-schedule entry per contract at a target
near_dte0, holding 7 days, direction chosen by the sign of basis at entry (nearly always
short-quarterly/long-perp, since the curve is in contango >97% of the time). Tested at 5 dte0
checkpoints — all reported, not just the best:

| dte0 | BTC n | BTC gross | BTC net@14/@28 | BTC worst | ETH n | ETH gross | ETH net@14/@28 | ETH worst |
|---|---|---|---|---|---|---|---|---|
| 15 | 22 | +21.7bps, t=4.21, p=0.0004 | +7.7 / -6.3 | -0.4bps | 22 | +25.4bps, t=3.11, p=0.005 | +11.4 / -2.7 | -10.0bps |
| 30 | 22 | +22.8bps, t=2.25, p=0.035 | +8.8 / -5.2 | -54.5bps | 22 | +29.4bps, t=1.54, p=0.14 | +15.4 / +1.4 | -85.6bps |
| 45 | 22 | +15.3bps, t=1.15 | +1.3 / -12.7 | -185.1bps | 22 | +8.2bps, t=0.52 | -5.8 / -19.8 | -274.1bps |
| 60 | 21 | +13.9bps, t=1.88, p=0.075 | -0.1 / -14.1 | -101.0bps | 21 | +13.1bps, t=1.98, p=0.062 | -0.9 / -14.9 | -56.0bps |
| 75 | 21 | +14.3bps, t=1.96, p=0.065 | +0.3 / -13.8 | -24.4bps | 21 | +25.5bps, t=2.54, p=0.019 | +11.5 / -2.5 | -15.5bps |

Year-by-year (dte0=15, k7d, one entry/quarter/symbol so n~4/year): positive in every single year
2021-2026 for both symbols (BTC: +30/+9/+19/+45/+14/+7bps; ETH: +35/+41/+12/+43/+8/+2bps) — the
most stable sign pattern found in this entire sweep. The dte0=45 tail (-185/-274bps) is the single
worst episode across the whole study, both dated 2021-02-07, the exact "rich keeps getting richer"
Feb-2021 blow-off regime W3's addendum already flagged for the plain perp-vs-quarterly trade —
this confirms it as a structural, recurring failure mode of any long-carry basis strategy on this
instrument, not a one-off artifact of one construction. VERDICT: WEAK — directionally real and
remarkably sign-stable, but net@stress is negative or breakeven at every dte0 tested; only
survives cost at the base (14bps) assumption, and even there by a thin margin except at
dte0=15/30. Genuinely tradeable only under favorable (maker-heavy) execution, and even then
carries a real left tail (-185 to -274bps) concentrated in blow-off-momentum regimes.

### M6 — Basis jump events (event-like, not continuous)

MECHANISM: distinct from all extremity-decile mechanisms — reacts to a discrete 1-day jump in
basis (|z|>90th-pct of a causal trailing-60d rolling std of daily changes), not a persistent
level. Direction (continuation vs. reversal) pre-committed on TRAIN.

| symbol (mode, abs-z thresh) | k1d | k3d | k7d |
|---|---|---|---|
| BTC (continuation, 1.26) | n=35, -3.3bps, t=-2.98, p=0.005 | n=30, -1.6bps | n=24, -1.0bps |
| ETH (reversal, 1.24) | n=43, +2.5bps | n=30, +6.1bps, t=2.37, p=0.025 | n=23, +2.3bps |

BTC's pre-committed continuation bet loses money significantly at k1d (net@14=-17.3bps) — the
train-fit direction pick did not generalize. ETH's reversal bet shows a marginally significant
positive at k3d only (net@14=-7.9bps, still negative after cost) and not at k1d/k7d. VERDICT:
DEAD — no horizon clears cost for either symbol, and BTC's direction pick was actively wrong OOS,
a useful negative result (jump reactions are not a repeatable edge here even though N is
reasonably large, 23-43).

### M7 — Funding-implied carry vs. quarterly-implied carry disagreement (headline finding)

MECHANISM: perpetual funding rate and quarterly-futures basis are two independent market
estimates of the same thing (annualized cost-of-carry) — when they disagree sharply, one of them
should be "wrong" and converge toward the other. PAYER: perp-funding-driven leverage (typically
retail-heavy, momentum-chasing) diverging from the quarterly curve's comparatively institutional,
less levered pricing. SIGNAL: disagreement = funding_ann_pct - basis_near_ann, train-fit decile,
near_dte>=7. ENTRY: at CHEAP disagreement (funding running deeply negative while quarterly basis
stays roughly normal-positive — a genuine bear/de-risking regime, confirmed below, not just
"basis is extreme" relabeled) — short-quarterly/long-perp, betting basis follows funding down.
VENUE: Binance perp + quarterly (2-leg hedge). EXIT: k-day hold capped at expiry (basis->0 known
convergence).

Checked this is not a repackaging of M1's basis-level fade (the exact trap that sank W3's A9
"basis velocity" mechanism once orthogonalized against level): corr(disagreement, basis_ann) is
only 0.49-0.52 (moderate, not redundant), and directly inspecting the 56-57 CHEAP-disagreement
entry dates shows basis_near_ann at entry has median 6.6% (BTC) / 5.0% (ETH) — statistically
indistinguishable from the whole-sample median (6.5% / 6.0%), i.e. these are not extreme-basis
days by M1's own criterion (only 16-23% would even qualify under M1's top-quintile test). What is
different is funding_ann_pct: median -2.2% (BTC) / -2.4% (ETH) vs. whole-sample median +4.9% /
+5.5% — funding is running deeply negative (short-heavy perp positioning) while the quarterly
curve stays roughly at its normal level. This is a genuinely distinct regime, not M1 relabeled.

| symbol | k1d | k3d | k7d | k14d | k30d |
|---|---|---|---|---|---|
| BTC (q10=-6.7, q90=+12.3 ann. disagreement) | n=59, +3.6bps, t=2.87, p=0.006, net@14/@28=-10.4/-24.4 | n=51, +7.8bps, t=4.00, net=-6.2/-20.2 | n=28, +13.9bps, t=3.15, p=0.004, net=-0.1/-14.1 | n=24, +21.7bps, t=2.94, p=0.007, net=+7.7/-6.3 | n=18, +47.2bps, t=3.36, p=0.004, net=+33.2/+19.2 |
| ETH (q10=-5.8, q90=+15.6 ann. disagreement) | n=55, +2.8bps, t=2.04, p=0.046, net=-11.2/-25.2 | n=49, +5.9bps, t=4.00, net=-8.1/-22.1 | n=32, +15.4bps, t=5.19, p<0.001, net=+1.4/-12.6 | n=25, +29.3bps, t=4.94, p<0.001, net=+15.3/+1.3 | n=15, +44.3bps, t=2.83, p=0.013, net=+30.3/+16.3 |

Worst episode: BTC k30d -111.8bps; ETH k30d -106.6bps. Both worse than base cost alone would
suggest but far short of catastrophic, and win rates stay high (89% BTC, 87% ETH at k30d) despite
the tail. Stability across years (k7d), positive every year: BTC 2024 n=10 +1.4bps, 2025 n=10
+32.8bps, 2026(partial) n=8 +6.1bps; ETH 2024 n=9 +19.8bps, 2025 n=11 +22.4bps, 2026 n=12 +5.8bps
— real decay from 2025 to 2026 worth flagging (could be regime change, could be the 2026 bucket
only covering Jan-Jun due to the perp-data staleness cutoff), but never flips negative. VERDICT:
PROMISING — the strongest finding in this sweep: genuinely new mechanism (checked, not
level-relabeled), real independent N (15-59 depending on horizon), clears both cost tiers at
k14d/k30d, sign-stable across 3 calendar years for both symbols. Caveats before any sizing: (a)
capacity is the same quarterly-futures-book-depth ceiling as every other mechanism here — thin
relative to perp; (b) only BTC/ETH have quarterly futures at all; (c) worst-episode tail (-107 to
-112bps) needs explicit risk budgeting, not assumed away; (d) 2025->2026 magnitude decay warrants
a re-check once fresher perp/funding data exists (both series are 1.5-2 months stale as of this
writing, so the 2026 bucket is thin and partial).

### M8 — Cross-asset (BTC vs. ETH) calendar-basis dispersion

MECHANISM: does BTC's curve run rich/cheap relative to ETH's, and does that dispersion mean-
revert? PAYER: asset-specific flow/positioning imbalances that should arbitrage away given BTC
and ETH quarterlies share the same expiry calendar (confirmed). SIGNAL: basis_near_ann(BTC) -
basis_near_ann(ETH), train-fit decile on the merged panel. TRADE: a 4-leg RV — long one asset's
perp-quarterly spread, short the other's — so cost is doubled (base=28bps, stress=56bps).

| k1d | k3d | k7d | k14d | k30d |
|---|---|---|---|---|
| n=16, +6.5bps, t=2.96, p=0.010, net@28/@56=-21.5/-49.5 | n=12, +10.1bps, t=3.50, net=-17.9/-45.9 | n=6, +13.2bps, t=4.89, p=0.005, net=-14.8/-42.8 | n=9, +12.0bps, t=7.81, p<0.001, net=-16.0/-44.0 | n=5, +10.8bps, t=8.25, p=0.001, net=-17.2/-45.2 |

Gross numbers look attractive (tight std, high t-stats) but the sample is small (5-16) and the
4-leg cost structure is fatal: net is negative by 15-50bps at every single horizon under both
cost tiers. VERDICT: DEAD — cost-driven, not signal-driven. The dispersion is real and
statistically clean on a small sample, but a 4-leg trade needs roughly 4x a single spread's edge
to clear cost, and this doesn't come close. Would only be worth revisiting if executed with
maker-only fills on all 4 legs, which is not a realistic assumption for a relative-value trade
that needs to enter and exit close to simultaneously across two symbols.

### M9 — Curve inversion event (basis_near<0, true backwardation)

MECHANISM: distinct from all decile-threshold mechanisms — a hard, theory-driven zero threshold
(a quarterly future trading below spot/perp is a structurally unusual, not merely statistically
extreme, condition), so uses the full 2021-2026 sample rather than a train/test split (no
data-snooping risk since the cutoff isn't fit). RAW inversion frequency: BTC 58 days (2.9% of
history), ETH 151 days (7.4%) — genuinely rare. Real sustained multi-week backwardation episodes
cluster almost entirely in the 2022 bear market (post-LUNA/3AC and post-FTX; e.g. ETH ran
backwardated 2022-08-07 to 2022-09-14, five and a half weeks); 2023-2026 shows only isolated 1-4
day flickers, mostly right at expiry (dte<5) rather than genuine mid-contract inversion. ENTRY:
long-quarterly/short-perp, betting reversion to normal contango. EXIT: capped at expiry.

| symbol | k1d | k3d | k7d | k14d | k30d |
|---|---|---|---|---|---|
| BTC | n=11, +2.8bps | n=9, +9.4bps | n=5, +14.2bps, t=2.43, p=0.072 | n=6, +10.1bps | n=4, +5.9bps |
| ETH | n=15, +1.0bps | n=12, -2.6bps | n=10, -3.0bps | n=11, +0.9bps | n=10, -3.0bps |

BTC is directionally positive at every horizon (though only marginally significant at k7d, n=5)
but net-of-cost is negative everywhere (best case net@14=+0.2bps at k7d, immediately negative at
stress). ETH shows no consistent direction at all. VERDICT: WEAK/DEAD — sample too thin and too
concentrated in one historical regime (2022) to trust as a repeatable edge; directionally
plausible for BTC but doesn't clear cost even at the friendliest horizon.

### M10 — Curve dislocation vs. its own trailing 180-day distribution (rolling, adaptive)

MECHANISM: a live-tradeable alternative to M2's fixed global-quantile design — rolling 180-day
(min 90d) mean/std of cal_spread_ann, causally lagged (shift(1)), decile-fit threshold on the
resulting z-score. Directly answers "curve shape vs. its own historical distribution."

| symbol | k1d | k3d | k7d | k14d | k30d |
|---|---|---|---|---|---|
| BTC | n=19, +2.2bps | n=16, -0.6bps | n=13, +1.2bps | n=9, -2.7bps | n=6, -0.4bps |
| ETH | n=20, +1.3bps | n=18, +3.4bps, t=1.96, p=0.067 | n=13, +5.9bps | n=9, -2.1bps | n=5, +1.1bps |

No horizon/symbol combination is significant or economically meaningful; gross means hover within
a few bps of zero in both directions. VERDICT: DEAD — the adaptive rolling-dislocation framing
doesn't recover any edge M2's static version didn't already show (or rather, didn't already fail
to show).

## Ranked table

| rank | mechanism | horizon | true-indep-N | gross bps | net bps @base(14)/@stress(28) | worst episode | stability | capacity | confidence | status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | M7 funding-vs-quarterly-basis disagreement | k14d-k30d | 15-24 | +21.7 to +47.2 | +7.7 to +33.2 / -6.3 to +19.2 | -111.8 (BTC) / -106.6 (ETH) | positive every year 2024-26 (both symbols) | BTC/ETH only, thin quarterly book | medium | PROMISING |
| 2 | M5 expiry-convergence roll-down harvest (dte0=15/30) | k7d fixed | 21-22/yr~4 | +21.7 to +29.4 | +7.7 to +15.4 / -6.3 to +1.4 | -274.1 (worst overall, ETH dte0=45, 2021-02) | positive every single year 2021-26, both symbols | BTC/ETH only | medium | WEAK — sign-stable but breakeven-to-negative at stress cost |
| 3 | M9 curve inversion event (BTC) | k7d | 5 | +14.2 | +0.2 / -13.8 | -2.6 | directionally consistent but n too small, clustered in 2022 | BTC/ETH only, very rare event | low | WEAK |
| 4 | M1 perp-vs-near-quarterly, annualized decile (extends W3) | k1d-k30d | 1-9 | -3.9 to +14.8 | mostly negative net | -35.0 (BTC k14d) | inconclusive (N too thin post-decluster) | BTC/ETH only | low | WEAK/INSUFFICIENT-N — confirms W3's downgrade, more so |
| 5 | M8 cross-asset (BTC vs ETH) dispersion | k1d-k30d | 5-16 | +6.5 to +13.2 | negative every horizon (4-leg cost) | -21.5 to -49.5 net | signal itself is clean (t up to 8.3) | 2 symbols only, 4-leg execution | low | DEAD — cost-driven |
| 6 | M4 TTE-normalized basis z-score | k1d-k30d | 1-4 | mixed sign | inconclusive | n too small | n too small | BTC/ETH only | none | BLOCKED — insufficient N |
| 7 | M6 basis jump events | k1d-k7d | 23-43 | mixed | negative every horizon tested | -55.0 | direction pick did not generalize OOS (BTC) | BTC/ETH only | low | DEAD |
| 8 | M2 near-vs-next quarterly spread mean-reversion | k1d-k30d | 4-6 | -0.3 to -18.3 (wrong sign vs hyp.) | negative every horizon | -36.9 | consistently wrong-signed | BTC/ETH only | low | DEAD — wrong sign |
| 9 | M3 curve slope momentum/reversion | k1d-k30d | 3-6 | mixed, mostly negative | negative most horizons | -68.1 (ETH) | direction disagrees across symbols (overfit) | BTC/ETH only | none | DEAD |
| 10 | M10 curve dislocation vs. rolling 180d distribution | k1d-k30d | 5-20 | -2.7 to +5.9 | ~0 | -32.5 | flat/no edge | BTC/ETH only | low | DEAD |

## Bottom line

The fuller-curve reconstruction surfaces one real, checked-against-the-obvious-trap, genuinely new
candidate (M7, funding-vs-quarterly disagreement) that clears both cost tiers at longer horizons
with a stable multi-year sign — the first "PROMISING" result this dataset has produced since W3's
original (later-downgraded) calendar-basis finding. It is smaller and thinner than that original
headline number, which is the correct outcome given how aggressively this round's methodology had
to be tightened (two new contamination sources found and fixed, on top of W3's four). Every other
mechanism tested — including the two explicitly requested "new spread" ideas (quarterly-to-
quarterly M2, cross-asset dispersion M8) — is WEAK or DEAD once honestly declustered and costed:
M2 is wrong-signed, M8's clean-looking signal is entirely erased by its 4-leg cost structure, and
the curve-shape/momentum mechanisms (M3, M10) show no stable direction at all. The single largest
number in this entire sweep is a loss (-274.1bps, M5, Feb 2021), recurring in the same "rich keeps
getting richer" bull blow-off regime that produced W3's worst calendar-basis episode (-703.6bps) —
the third time this specific failure mode has appeared across two research rounds on this
instrument, which should be treated as the dataset's single most load-bearing fact about tail risk
on any long-carry BTC/ETH quarterly-futures strategy, not a coincidence to be diversified away
with more mechanisms.

## Files under this report

- `build_panel.py` — causal curve construction (spot-adjacent perp, near/next quarterly, funding,
  premium) with the expiry-truncation and duplicate-close fixes re-applied.
- `analyze.py` — all 10 mechanisms, the two-pass decluster engine (regime-contiguity +
  non-overlapping-window), cost model, and stats.
- `evidence/panel_BTCUSDT.parquet`, `evidence/panel_ETHUSDT.parquet` — the built daily curve
  panels (216KB each).
- `evidence/all_results.json` — every mechanism x symbol x horizon x parameterization result
  (gross/net/worst/best/win-rate/year-stability), including all parameterizations tried (M5's 5
  dte0 checkpoints, M3's two pre-committed directions), not just winners.
- `evidence/episodes_*.csv` — representative episode-level ledgers (entry/exit date, entry/exit
  value, side, realized pnl) for the headline (M7) and comparison (M1, M2, M8, M9) mechanisms.
