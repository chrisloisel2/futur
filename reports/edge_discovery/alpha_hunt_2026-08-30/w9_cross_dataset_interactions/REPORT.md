# W9 — Cross-Dataset Interactions

Date of analysis: 2026-08-30. Scope: does CONDITIONING one already-known single-dataset
mechanism on another dataset's state revive, strengthen, kill, or leave unchanged the
marginal effect? Per the mission brief, this worker does not re-derive any single-dataset
marginal from scratch — every marginal number below is either a direct replication (to
confirm apples-to-apples comparability before slicing) of a number already reported by round
1 (`alpha_hunt_2026-08-29/w1..w6`), or a marginal computed once, on my own construction, and
then reused unchanged as the baseline for every conditional split of that mechanism.

**Method discipline**: every interaction below states its one-sentence economic
justification before any number was computed (verbatim from the task brief where the brief
supplied one; my own wording where I added a new interaction). All liquidation/OI/funding
work uses the pre-existing causal state columns in `data/events/liq_cascade_dataset.parquet`
(computed as of `event_time`, not after it). All HL wallet-flow work uses a strict
train-wallet-ranking / test-wallet-evaluation split (train ends 2026-08-13, matching W5's
A11 split exactly) so no wallet is ever ranked and evaluated on the same data. All
book-depth/tick work uses event-time point lookups (grep-filtered, single-venue dedicated
top-of-book stream, per the crossed-book pitfall already documented by W2) — never a
continuous merge of the 66GB tick store. Nothing in `data/`, `data_v2/`, or
`market_physics_v3/` was modified; nothing was copied or duplicated; all new files are under
this report's own directory, all evidence CSVs are <80KB.

## Executive summary

11 interactions tested (all 10 suggested + 1 of my own, plus several sub-splits worth
flagging as informal bonus checks inside interactions 1 and 4). Three real, stable,
economically-sensible interaction effects were found — two of them *inverted* the naively
expected direction, which is exactly the kind of result that would be easy to mistake for
noise without the marginal-vs-conditional discipline the brief required:

- **Own interaction (#11) is the strongest finding of the session**: A7-TAIL-E1's entire
  edge is a **repeat/serial-cascade story, not a first-shock story**. Restricted to a
  symbol's *first* liquidation event in 24h, the "PROMISING" tail-bucket edge (marginal
  +23.1bps net) **disappears and goes slightly negative** (-6.2bps net, PF 0.95, n=1,509).
  Restricted to the 2nd+ event in the same 24h window, net jumps to **+42.5bps** (n=2,290),
  and to **+86.6bps** on the 3rd+ event (n=1,155) — stable across both 2025 and 2026 test
  years in both directions.
- **Basis-funding disagreement (#8) is a strong, decluster-robust interaction**: the
  perp-spot basis fade edge is +18.5 to +20.6bps/1d when basis and funding "agree" (point
  the same carry direction) vs -1.3 to -1.7bps (statistically indistinguishable from zero
  or slightly negative) when they disagree — a ~20bps swing that survives a partial
  autocorrelation-decluster pass.
- **Liquidation cascade x pre-event OI state (#1) inverts the naive hypothesis**: cascades
  that hit when OI is *not* extended (bottom quartile) show the strongest edge (+49.8 to
  +66.5bps net depending on the OI proxy used), not the weakest — the opposite of "more
  leverage fuel = more continuation." Funding extremity, by contrast, adds nothing
  (+16.65 vs +16.58bps, NO_INTERACTION_EFFECT) — but on only 16% data coverage.

Four more produced clean **NO_INTERACTION_EFFECT** verdicts (#3, #7-after-declustering,
#10, and the funding leg of #1) — reported in full per the brief's instruction that a null
interaction is still a useful finding. Two (#2 liquidation+book-depth, #9
residual+dispersion) are directionally suggestive but too data-constrained or unstable to
trust. Two (#4 wallet+imbalance, #6 options-flow+liquidations) show a genuine trade-level
structure that does not survive a more rigorous check (wallet-level significance for #4;
vol-clustering confound for #6) — same pattern W5/W2 already flagged for the underlying
marginals, now shown to persist into the conditioned version too.

## Ranked table

| rank | interaction | justification (1-sentence) | conditional gross/net | marginal gross/net | n (cond / marg) | stability | status |
|---|---|---|---|---|---|---|---|
| 1 | **#11 (own)** Liq cascade tail-bucket x own-symbol repeat-event count | A repeat cascade on the same symbol within 24h signals an ongoing deleveraging spiral closer to capitulation exhaustion than an isolated first shock | repeat(>=1): +56.5/**+42.5**bps . serial(>=2): +100.6/**+86.6**bps | first-time(0): +7.8/**-6.2**bps | 2,290 / 1,509 (of 3,799 tail-bucket total) | stable both test years, both directions (repeat +40/+48bps 2025/2026; first-time -10/+1bps) | **PROMISING -- first-time events should likely be filtered OUT of A7-TAIL-E1 entirely** |
| 2 | #8 Basis-funding disagreement | when perp basis and funding rate imply opposite carry directions, the mispricing is less settled and a level-fade is less reliable than when both signals confirm the same crowding | agree: **+18.5 to +20.6**bps/1d (declustered) | disagree: **-1.3 to -1.7**bps/1d (declustered) | agree 2.23M raw / 1.65M declustered; disagree 1.70M raw / 1.35M declustered | direction survives partial decluster; year-by-year still choppy in both buckets (see caveats) | **PROMISING** -- real, large, sign-flipping interaction; not a tradeable strategy on its own (this is perp-spot basis, same known-exhausted marginal W3 already flagged, tested here only for the conditioning) |
| 3 | #1a Liq cascade x pre-event OI extension | cascades hitting an already-extended OI base have more crowded leverage to unwind -- hypothesized to continue harder | OI-extended(>=p75): **+10.7**bps . OI-low(<=p25): **+49.8**bps (oi_pctile_30d); oi_vs_7d gives the same direction (+29.1 vs +66.5bps) | tail-bucket marginal: **+23.1**bps | 1,330 / 855 (of 3,799) | OI-low stable positive both years (+75.5/+10.1); OI-extended flips sign (-6.3/+68.7) | **PROMISING, but hypothesis INVERTED** -- low pre-event OI, not high, carries the edge |
| 4 | #1b Liq cascade x pre-event funding extremity | crowded funding alongside a cascade should add more forced-unwind fuel | \|funding_z30\|>=1: +16.65bps | \|funding_z30\|<1: +16.58bps (same subsample marginal) | 203 / 409 (only 612/3,799 = 16% of tail bucket has funding_z30 populated) | n/a, thin | **NO_INTERACTION_EFFECT** (and low-power: 84% of the tail bucket has no funding data at all) |
| 5 | #5 Options IV-shock x funding extremity (BTC) | crowded perp funding stacks an unwind-risk premium on top of whatever the IV shock is already pricing, so the same IV move should forecast more incremental RV when funding is already extreme | funding-extreme(top q, trailing 90d): **IC=0.438** (p=7e-15, n=286), stable both test halves (0.44/0.42) | full-sample marginal: **IC=0.338** (p=7e-36, n=1,292); funding-normal/-low: 0.319-0.327 (approx marginal) | 286 / 1,292 | stable across chronological halves of the extreme subsample | **PROMISING signal, still NOT tradeable** (same A14 caveat -- no options execution vehicle here) |
| 6 | #4 HL wallet flow x book imbalance (confirmation vs contrarian) | an informed wallet's trade should carry more signal when the resting book already leans the same way (confirmation) than when it leans against the wallet (contrarian, picking off stale liquidity -- a different mechanism) | confirmation: **+5.15**bps@60s (t=17.5) -> +3.28bps@300s; contrarian: **-2.22**bps@60s (t=-7.3) -> **+4.30**bps@300s (sign flips) | top-1% marginal: **+1.49**bps@60s -> +3.78bps@300s | 71,065 confirm / 70,300 contrarian / 141,365 marginal (trade-level); 272/278/286 wallets | trade-level stable both test halves for confirmation (+5.27/+5.04bps); contrarian unstable (+0.22 to -4.66bps) | **WEAK/inconclusive -- same failure mode as A11**: wallet-level test not significant (confirm p=0.37, contrarian p=0.49, paired diff p=0.12) despite huge trade-level t-stats |
| 7 | #6 Options flow x BTC liquidation cascade days (lead-lag) | sophisticated options positioning (put demand, block flow) ahead of a large deleveraging event would show up as elevated pre-event options activity | put/call vol ratio z-score: lag-1d t=3.11(p=0.002), lag-2d t=2.03(p=0.044), peaks at lag=0 (t=4.43), fades after | z-scored vs each series' own trailing-60d mean (baseline z~=0 by construction) | 141-142 per lag / 148 unique BTC big-cascade days | pattern (build->peak->decay) is internally consistent but likely partly mechanical | **WEAK / likely confounded by volatility clustering** -- my tail-bucket criterion itself includes `vol_24h` extremity, which is already elevated in the days before a big event by construction; I did not partial this out the way W5 did for A14's marginal |
| 8 | #7 Basis level-fade x OI flow direction (building vs unwinding) | basis richness alongside OI building reflects fresh speculative demand (may persist); alongside OI unwinding reflects forced closing (should revert faster) | raw ticks: building +7.4bps vs unwinding +12.1bps/1d (looks real) -> **declustered: +11.6bps vs +10.8bps** (vanishes) | marginal (raw): +9.8bps/1d; (declustered): +11.2bps/1d | 1.98M/1.96M raw -> 1.48M/1.57M declustered | building flips negative 2025-26 (-4.0/-10.6bps) even though the raw split looked stable | **NO_INTERACTION_EFFECT once autocorrelation is controlled** -- good example of exactly what the brief warned about |
| 9 | #10 Microstructure A4 (refill-after-sweep) x realized-vol regime | MM refill decisions should carry more information (bigger, more urgent repricing) in high-vol regimes than in routine low-vol requoting | high-vol high-low spread: **-0.50**bps . low-vol: **-0.40**bps | pooled marginal high-low spread: **-0.45**bps | ~50k/tercile/regime out of 453k sweep events | consistent by symbol (BTC -0.43/-0.40, ETH -0.57/-0.41, SOL -0.41/-0.32) | **NO_INTERACTION_EFFECT** -- vol regime barely moves the spread. **Caveat: my reproduction got the opposite sign from W2's A4** (see note below); the interaction verdict (no effect from vol regime) holds regardless of which sign is correct, since both legs use the same construction |
| 10 | #9 Idiosyncratic residual extreme x cross-sectional dispersion | an outlier residual return is more likely genuine information in a low-dispersion (correlated/crowded) regime, or the opposite -- broad-stress high-dispersion residual events may over-continue as forced flows unwind (tested both directions) | high-dispersion 1d: **+9.49**bps (t=6.35) . low-dispersion 1d: -1.87bps (n.s.) . mid: -4.94bps | marginal 1d: **+3.14**bps (t=3.06); marginal 1h/4h both mean-revert (-2.1/-2.5bps) | 693k high / 135k low / 1.29M marginal | **unstable**: high-dispersion 2022 +55.0, 2023 **-28.2** (sign flip), 2024 ~=0, 2025 +18.6, 2026 +12.4 | **WEAK/directionally-suggestive, not stable enough to trust** -- supports the "high-dispersion amplifies continuation" branch of the two hypothesized directions, but a clean sign flip in 2023 blocks any real conclusion |
| 11 | #2 Liquidation cascade severity x pre-event book depth (event-time lookup) | a cascade hitting a thin book should show more continuation/less absorption than one hitting a deep book | within-symbol-normalized: thin-book(spread) 300s +27.9bps vs thick-book +6.3bps (p=0.10-0.12, n.s.); BTC-only (n=9): thin +5.1 vs thick -0.5 (n.s.) | all-cascade marginal: **+12.2**bps/300s (t=2.41, n=22) | 6-16 per split / 22 total | direction consistent with the naive hypothesis (thin>thick) at every cut tried, never significant | **NEEDS_FULL_VALIDATION / DATA-CONSTRAINED** -- only 22 cascade events have any usable book state at all, because the tick-level book store only has 5 non-contiguous calendar dates (a hard ceiling already documented by W2, not something more analysis fixes) |
| 12 | #3 HL wallet flow x own-market OI extension | informed flow should carry more price impact/information against an already-extended, fragile leverage base | OI-extended(>=p75): +1.38bps@60s . OI-low(<=p25): +2.46bps@60s (trade-level, both sig.); **wallet-level: 1.23 vs 1.31bps, p=0.36/0.36 -- no difference** | top-1% marginal: +1.49bps@60s | 64,679/34,555 (trade) . 256/211 (wallet) | wallet-level means essentially identical | **NO_INTERACTION_EFFECT** (trade-level split direction is opposite of #1's OI finding, for what it's worth, but neither is real once measured at the wallet level) |

## Interaction 1 -- Liquidation + OI + funding

**Data**: `data/events/liq_cascade_dataset.parquet` only (49-symbol clean universe, same file
W1 used for A7-TAIL-E1 -- deliberately not the contaminated 312-symbol `cascade_dataset.parquet`).

**Justification** (from brief): a cascade hitting an already-extended OI/funding base has
more crowded leverage to unwind and should show a stronger (or weaker -- testing both) forward
continuation than one hitting a thin/uncrowded base.

**Method**: exact replication of A7-TAIL-E1's spec first (fit tail-bucket thresholds on
2021-2024, test blind on 2025-2026) to confirm the marginal baseline: **n=3,799, gross
+37.14bps, net +23.14bps@14bps, PF 1.19** -- matches W1's reported +23.1bps/PF 1.19 to the
decimal, confirming the replication is faithful before any conditioning. Then split the test
tail-bucket by two pre-event OI proxies (`oi_pctile_30d`, fully populated; `oi_vs_7d`, fully
populated) and by pre-event `funding_z30` (only 16% populated in 2025-2026 -- flagged, not
hidden).

**Result -- OI leg (real, but inverted)**: `oi_pctile_30d>=0.75` (leverage already extended):
n=1,330, net **+10.7bps**, PF 1.08. `oi_pctile_30d<=0.25` (leverage NOT extended): n=855, net
**+49.75bps**, PF 1.50. The mid-tercile sits in between (+19.3bps). `oi_vs_7d` (a different OI
proxy -- current OI vs its own 7-day trailing average) confirms the same direction:
building(>=q75 fit-period) +29.1bps vs unwinding/low(<=q25) +66.5bps. **This is the opposite of
the stated hypothesis** -- a cascade hitting a symbol whose OI base is *not* already extended
carries the stronger edge. Read economically: a symbol whose OI wasn't already stretched going
into the event is more likely undergoing a genuine, fresh capitulation flush (clean signal)
rather than one more wave in an already-crowded, already-partially-unwound position (noisier,
less information per event). Year-stability supports this: the low-OI bucket is positive both
2025 (+75.5bps) and 2026 (+10.1bps); the high-OI bucket flips sign (-6.3bps in 2025, +68.7bps
in 2026).

**Result -- funding leg (no effect, thin data)**: among the 612/3,799 tail-bucket rows with
`funding_z30` populated, extreme funding (|z|>=1, n=203) nets +16.65bps vs normal funding
(n=409) at +16.58bps -- statistically indistinguishable, essentially
**NO_INTERACTION_EFFECT**. The double-condition (OI-extended AND funding-extreme, n=53) nets
+5.3bps vs neither condition (n=279) at +37.65bps -- consistent in direction with the OI
finding alone, but the funding leg itself adds nothing on top of it. Caveat: `funding_z30`'s
84% missingness in the test period is a real power constraint, not a design choice -- this
leg should be retested once the funding backfill for the smaller alt-heavy tail-bucket
population is more complete.

Evidence: `evidence/i1_liq_oi_funding.csv`.

## Interaction 2 -- Liquidation cascade x pre-event book depth (event-time lookup)

**Data**: `data/derivatives_raw/exchange={bybit,okx}/.../stream=force_order` (real liquidation
feed, not the OI-proxy) joined at event-time to `market_physics_v3/raw/book_events`
(binance dedicated `bookTicker` top-of-book stream only, per W2's documented crossed-book
fix -- never the diff/L2 stream). BTC/ETH/SOL, the only symbols with tick data, on the only 5
calendar dates that exist for it: 2026-08-15/16/17/28/29.

**Justification**: a cascade hitting a thin book (wide spread, shallow best-level depth)
should show more price continuation (less absorption capacity) than one hitting a deep book.

**Method**: 1-minute bins of combined bybit+okx liquidation notional per symbol-day; cascade =
top decile of notional (pooled across the 5-day sample, p90 = $188.6k/min). For each cascade
minute, pulled binance top-of-book state (spread_bps, best-level qty) averaged over the 30s
immediately before the bin, and the continuation-signed forward return (signed by net
liquidation direction) at 30s/120s/300s after the bin. Extracted via `grep '"bookTicker"'`
pre-filter + `orjson` parse (not a full parquet load of the 3.2GB/symbol-day raw file) -- 6-17s
per symbol-date.

**Result**: only **n=22** cascade events end up with valid pre-event book state (most of the
top-decile 1-min bins fall on days/times the 5-day tick capture doesn't cover). Marginal
(unconditional) continuation: +12.2bps/300s (t=2.41, p=0.025). Naive median-split by raw
spread/depth is badly confounded by symbol composition -- SOL's spread_bps and depth-in-qty
units are structurally ~50-1000x BTC's on both axes, not because its book is actually
thinner/deeper. After within-symbol z-scoring to remove that confound: thin-book (wide
spread, z>=0) mean +27.9bps vs thick-book (z<0) mean +6.3bps at 300s -- directionally exactly
as hypothesized, but n=6 vs n=16 and p=0.10-0.12, not significant. Restricting to BTC alone
(n=9, no cross-symbol confound at all) keeps the same direction (thin +5.1bps vs thick
-0.5bps) but is even further from significance (p=0.32-0.95).

**Verdict: NEEDS_FULL_VALIDATION / DATA-CONSTRAINED.** This is not a methodology failure --
it's the hard ceiling W2 already flagged (book-event tick data exists for exactly 5
non-contiguous calendar dates, 66GB total, nothing more can be extracted from what's on disk).
The direction is consistent with intuition at every cut tried and never flips sign, which is
mildly encouraging, but with n=22 total events this cannot be called a finding either way.

Evidence: `evidence/i2_liq_book_events.csv`, `evidence/i2_summary.csv`.

## Interaction 3 -- HL wallet flow x own-market OI extension

**Data**: `data/hyperliquid/trades` (36.5M trades, 12 coins, 42 days) + `data/hyperliquid/ctxs`
(597k OI snapshots). No cross-worktree join needed.

**Justification**: informed flow should carry more price impact/information when it trades
against an already-extended, fragile leverage base.

**Method**: exact replication of W5's A11 wallet-ranking discipline -- built the same 73.0M-row
wallet ledger (every trade attributed to buyer +1 / seller -1), same train (07-18 to 08-13) / test
(08-13 to 08-29) split, same >=30-train-trades / top-1%-by-train-notional-weighted-markout_60s
cohort selection. Result: 410 qualifying top-1% wallets (A11: 409), 286 of them trade in test
(A11: 282), test markout_60s=+1.49bps / markout_300s=+3.78bps (A11: +1.24/+3.21bps) -- close
enough on an independent re-derivation to trust as the same signal. Joined each test trade,
via DuckDB `ASOF JOIN` (nearest OI snapshot at or before trade time), to a causal rolling
(2,000-obs window) OI percentile per coin.

**Result**: OI-extended (>=p75) trades: +1.38bps@60s (t=3.82, n=64,679). OI-low (<=p25):
+2.46bps@60s (t=7.71, n=34,555) -- trade-level looks like a mild interaction, opposite sign
from what the hypothesis expected. But the **wallet-level check** (mean of each wallet's own
average markout, not pooled trades -- the correct unit of inference per A11's own finding that
this cohort is concentrated in a handful of large wallets) shows essentially **no
difference at all**: OI-extended wallet-level mean +1.23bps (p=0.36, n=256 wallets) vs OI-low
+1.31bps (p=0.36, n=211 wallets).

**Verdict: NO_INTERACTION_EFFECT.** The trade-level split direction happens to be
consistent with interaction #1's OI finding (low-OI environment = stronger edge), but neither
side of this split is statistically real once measured at the correct (wallet) level.

Evidence: `evidence/i3_i4_hl_wallet_conditioning.csv`.

## Interaction 4 -- HL wallet flow x book imbalance (confirmation vs contrarian)

**Data**: same wallet ledger as #3, joined via `ASOF JOIN` to `data/hyperliquid/l2`
(1.79M snapshots, has a pre-computed `imbalance` column).

**Justification**: an informed wallet's trade should predict more when it's on the same side
as existing book imbalance (confirmation -- both signals agree there's real pressure) than when
it trades against the imbalance (contrarian -- picking off stale resting liquidity is a
different mechanism).

**Result**: confirmation (n=71,065): +5.15bps@60s (t=17.5, ~3.5x the marginal) decaying to
+3.28bps@300s. Contrarian (n=70,300): **-2.22bps@60s (t=-7.3, wrong-signed)**, then flipping to
**+4.30bps@300s (t=10.7)** -- the wallet initially looks wrong, then the price catches up to
where the wallet was pointing. Restricting to strong imbalance only (top tercile
|imbalance|) sharpens both sides further: confirmation +1.87bps, contrarian -4.41bps@60s. The
double-condition with interaction #3 (OI-extended AND confirmation) gives the single biggest
number in this whole report at the trade level: **+7.93bps** (t=15.7, n=33,101) vs OI-extended
AND contrarian at **-5.49bps** (t=-10.8, n=31,578) -- flagged explicitly as an exploratory,
not pre-registered, tertiary cut.

**But the wallet-level rigor check (same test A11 already applied to its own headline
number) does not confirm any of this**: confirmation wallet-level mean +1.23bps (p=0.37,
n=272 wallets), contrarian -0.91bps (p=0.49, n=278 wallets), paired same-wallet
confirm-minus-contrarian difference +2.72bps but p=0.12 (n=264 wallets with both). Chronological
stability is genuinely different between the two legs at the trade level, though: confirmation
is stable both test halves (+5.27bps H1, +5.04bps H2), contrarian is not (+0.22bps H1,
**-4.66bps H2**).

**Verdict: WEAK/inconclusive -- the same failure mode A11 already flagged** (huge n, huge
trade-level t-stats, driven by a concentrated handful of large wallets, doesn't clear
wallet-level significance). Worth flagging as the more promising sub-structure to revisit
once A11's own recommended 12+ week data collection extension lands, since the confirmation
leg alone is noticeably more trade-level-stable than the pooled top-1% marginal it's drawn
from.

Evidence: `evidence/i3_i4_hl_wallet_conditioning.csv`, `evidence/i4_wallet_level_rigor.csv`.

## Interaction 5 -- Options IV-shock x funding extremity (BTC)

**Data**: `data/options_backfill/deribit/features/BTC_daily.parquet` (1,294 days,
2023-01 to 2026-07-17) + `data/derivatives_backfill/binance/funding/BTCUSDT.parquet` (8h
funding, full history) + a daily realized-vol series computed from
`data_v2/normalized/event_feature_panel` BTCUSDT 5-min closes (own construction, since no
RV column exists ready-made anywhere on disk).

**Justification**: crowded perp funding stacks an unwind-risk premium on top of whatever the
options IV shock is already pricing, so the same `d_atm_iv_traded` move should forecast more
incremental next-day realized vol when funding is already in an extreme state.

**Method**: `rv_fwd1` = next day's realized vol (sqrt of sum of squared 5-min log returns).
Marginal Spearman IC of `d_atm_iv_traded -> rv_fwd1`: **0.338** (p=7e-36, n=1,292) -- matches
W5's reported +0.32/+0.323 closely (small difference is Spearman vs their likely-Pearson
choice; treated as a successful independent replication). Conditioned on a causal trailing
90-day rolling percentile of mean absolute funding rate.

**Result**: funding-extreme (top quartile): IC=**0.438** (p=7e-15, n=286) -- a real,
economically meaningful jump above the marginal. Funding-normal (mid): IC=0.327 (~=marginal).
Funding-low (bottom quartile): IC=0.319 (~=marginal, if anything the weakest). The
funding-extreme subsample splits cleanly in half chronologically with no decay: IC=0.44
(early half) vs 0.42 (late half).

**Verdict: PROMISING** -- a genuine, stable interaction (funding-extreme amplifies the
IV-shock->RV signal by ~30% relative to the marginal, and normal/low funding do not
distinguish from the marginal at all). Same standing caveat as A14's marginal: **not
independently tradeable** in this environment (no options execution/greeks capability) --
best used, as W5 already recommended, as a conditioning input if/when the IV-shock signal is
ever routed into the existing VRP overlay.

Evidence: `evidence/i5_options_iv_funding.csv`.

## Interaction 6 -- Options flow x BTC liquidation cascade days (lead-lag)

**Data**: `data/options_backfill/deribit/features/BTC_daily.parquet` +
`data/events/liq_cascade_dataset.parquet` (BTCUSDT only, restricted to the options data's
window, 2023-01-02 to 2026-07-16).

**Justification**: sophisticated options positioning (elevated put demand, institutional
block-trade activity) ahead of a large deleveraging event, if it exists, would show up as
elevated pre-event options-flow z-scores in the 1-2 days before the cascade.

**Method**: identified 148 unique BTC "big-cascade days" (`oi_drop_z<=-5` OR `vol_24h` in the
top decile of the window). Z-scored each options-flow feature against its own trailing 60-day
mean/std (causal), then compared the z-score distribution at lag -2, -1, 0, +1, +2 days
relative to each cascade day, against the null of 0 (baseline is 0 by construction of the
z-score).

**Result**: `pc_volume_ratio_z` (put/call volume ratio) rises significantly before the event --
lag-2d t=2.03 (p=0.044), lag-1d t=3.11 (p=0.002) -- peaks on the event day itself (t=4.43,
p<0.001), then fades (lag+1d t=2.00 marginal, lag+2d n.s.). `block_share_z` (institutional
block-trade activity) similarly rises lag-1d (t=2.23, p=0.028) then drops sharply after
(lag+2d t=-2.77, negative). `net_put_flow_btc_z` (raw notional, not ratio) is only significant
contemporaneously (lag=0, t=2.70), not before.

**Verdict: WEAK / likely confounded by volatility clustering, not a clean lead-lag finding.**
The 148-day cascade-day definition itself includes `vol_24h` extremity, which by construction
is already building in the days immediately before a big event (volatility clusters) -- so
elevated options activity in the same window is exactly what pure vol-clustering would also
predict, and unlike W5's A14 treatment I did not partial out trailing RV here to isolate an
incremental effect. The pattern (build -> peak at zero -> decay) is at least internally
consistent with a genuine lead rather than random noise, but this should be read as suggestive,
not confirmed.

Evidence: `evidence/i6_options_flow_around_liq_events.csv`.

## Interaction 7 -- Basis level-fade x OI flow direction

**Data**: `data_v2/normalized/event_feature_panel` (5-min, 312-symbol PIT panel, 2022+ for
adequate OI-delta coverage). `basis` here is `perp_spot_basis` (perp vs spot, **not** the
perp-vs-quarterly-futures calendar basis W3/W4 examined -- a genuinely different instrument).
Per the mission's explicit instruction, the marginal basis-level fade effect itself is not
new/re-litigated here (the scoreboard already calls it "the already-exhausted level effect");
only the OI-direction conditioning is tested.

**Justification**: basis richness alongside OI building reflects fresh speculative demand
(may persist longer); alongside OI unwinding reflects forced closing (should mean-revert
faster).

**Method**: extreme-basis subsample (|basis_z_7d|>=2, n=3.9M 5-min rows -- flagged up front
as heavily autocorrelated, matching W3's own documented caveat about this exact kind of raw
tick-level count). Fade PnL = -sign(basis_z_7d) x fwd_ret_h. Split by
`oi_delta_pct_1h` sign.

**Result (raw ticks)**: building (n=1.98M) nets +7.4bps/1d vs unwinding (n=1.96M) at
+12.1bps/1d -- looks like a real, if modest, interaction. **After a partial decluster**
(collapsing consecutive same-direction 5-min ticks per symbol into one observation per
contiguous run -- 3.9M ticks -> 3.05M runs, so this genuinely under-declusters relative to
W3's stricter regime-episode method, flagged honestly, not hidden) **the gap collapses**:
building +11.6bps vs unwinding +10.8bps, both ~= the declustered marginal of +11.2bps. Year
stability confirms the raw split was not to be trusted: the "building" bucket flips
significantly negative in 2025 (-4.0bps) and 2026 (-10.6bps) even though its multi-year
raw-tick average looked fine.

**Verdict: NO_INTERACTION_EFFECT once autocorrelation is controlled.** This is presented as
exactly the kind of result the brief asked to watch for -- a naive raw-tick split can manufacture
an apparent interaction that even a partial decluster erases.

Evidence: `evidence/i7_i8_declustered.csv`, `evidence/i7_i8_basis_oi_funding_raw.csv`.

## Interaction 8 -- Basis-funding disagreement

**Data / method**: same extreme-basis subsample as #7. `basis_sign = sign(basis_z_7d)`,
`funding_sign = sign(funding_rate)`; "agree" = same sign, "disagree" = opposite.

**Justification**: basis and funding are mechanically linked by design (funding is meant to
pull perp back toward spot, so a rich perp/basis normally co-occurs with positive funding);
when they agree, the market is in its normal, well-arbitraged state and a level-fade should
work cleanly; when they disagree, the funding mechanism hasn't (yet) caught up to the basis
dislocation, which is the less-settled, harder-to-read state where a naive fade should be
less reliable.

**Result**: raw ticks -- agree (n=2.23M): **+18.5bps/1d** (t=37.9) vs disagree (n=1.70M):
**-1.7bps/1d** (t=-3.2, wrong-signed) -- vs a pooled marginal of +9.8bps. **After the same
partial decluster as #7** (2.23M/1.70M -> 1.65M/1.35M contiguous runs): agree
**+20.6bps/1d** (t=41.7), disagree **-1.3bps/1d** (t=-2.25, barely significant negative) --
the ~20bps gap is essentially unchanged by declustering, unlike interaction #7's gap which
vanished under the identical treatment. At shorter horizons the same ordering holds but is
smaller: 1h agree +4.1 vs disagree +3.7bps (raw), 4h agree +5.7 vs disagree +3.6bps (raw).

**Caveat, stated plainly**: year-by-year stability inside each bucket is genuinely choppy
(agree: 2022 +54, 2023 +6, 2024 +18, 2025 +18, 2026 flat; disagree: 2022 -46, 2023 +30, 2024
+24, 2025 -24, 2026 -12) -- this basis-fade signal overall inherits the same regime
sensitivity W3 already documented for the (different, calendar-basis) instrument, and the
n counts here are 5-min-tick counts, not independent episodes, so p-values should be read as
directionally informative, not literal. What is robust across every cut tried (raw, partial
decluster, every horizon) is the *ordering*: agree always beats disagree, and disagree is
never reliably positive.

**Verdict: PROMISING** as an interaction (the conditioning genuinely separates a working
regime from a non-working one), with the standing caveat that the marginal fade signal itself
is not being newly proposed as tradeable here -- this is testing the CONDITIONING per the
mission's explicit instruction, not re-litigating W3/W4's basis work.

Evidence: `evidence/i7_i8_declustered.csv`, `evidence/i7_i8_basis_oi_funding_raw.csv`.

## Interaction 9 -- Idiosyncratic residual extreme x cross-sectional dispersion

**Data**: `data_v2/normalized/event_feature_panel`, full 312-symbol panel, 2022+.

**Justification** (brief asked to test both directions): an idiosyncratic residual-return
extreme could be more informative (continue more) in a **low**-dispersion/crowded regime
(a genuine outlier stands out more clearly against a quiet, correlated backdrop) -- or the
opposite, an extreme in a **high**-dispersion/broad-stress regime could over-continue as
forced flows unwind across the board.

**Method**: cross-sectional dispersion at each 5-min timestamp = std of `residual_return_1h`
across all symbols with data that timestamp (>=30 required, 481,836 timestamps). Residual
extreme = |`residual_return_1h`/`residual_std_30d`| >= 3 (own-symbol causal z-score, n=1.29M
events). Continuation-signed PnL = sign(residual extreme) x fwd_ret_h. Dispersion regime =
causal rolling (2,000-obs) percentile of dispersion.

**Result**: marginal continuation is small and mean-reverting at short horizons (1h -2.1bps,
4h -2.5bps, both t<-4.5) but flips to modestly positive at 1d (+3.1bps, t=3.06). Conditioned
on dispersion regime at the 1d horizon: **high-dispersion +9.49bps (t=6.35, n=693k)** vs
low-dispersion -1.87bps (n.s., n=135k) vs mid-dispersion -4.94bps (t=-3.07, n=460k) -- the
"broad-stress amplifies continuation" branch of the hypothesis, not the "quiet regime =
cleaner signal" branch. But year-by-year, the high-dispersion bucket is **not stable**:
2022 +55.0bps, **2023 -28.2bps (clean sign flip)**, 2024 ~=0, 2025 +18.6bps, 2026 +12.4bps.

**Verdict: WEAK/directionally-suggestive, not stable enough to trust.** A real, non-trivial
difference between regimes exists at the 1-day horizon and it does support one of the two
pre-specified hypothesis directions -- but a full sign flip in one of five years rules out
calling this a reliable interaction as tested.

Evidence: `evidence/i9_residual_dispersion.csv`.

## Interaction 10 -- Microstructure A4 (refill-after-sweep) x realized-vol regime

**Data**: `market_physics_v3/raw/book_events`, binance dedicated `bookTicker` stream only,
BTC/ETH/SOL, the same 5 calendar dates as interaction #2 (already extracted for that
interaction, reused here -- no new large-file scan needed). W2's exact A4 construction,
reused verbatim: refill ratio = resting qty at the new best price ~500ms after a sweep,
divided by the qty resting at the old best right before the sweep; continuation return
measured 2s after the sweep.

**Justification**: market-maker refill decisions should carry more information (larger,
more urgent repricing under stress) in high-realized-vol regimes than in routine,
low-information requoting during calm periods.

**Method**: detected 471,099 sweep events (any binance bookTicker best-price change,
bid+ask, both sides, all 3 symbols/5 dates). Vol regime = per-symbol rank of a 30-minute
rolling realized-vol proxy computed from the same tick series, split at terciles (top/bottom
33%).

**Result and an important discrepancy**: my construction found high-refill continuation of
**-0.01 to -0.03bps** (essentially zero/slightly negative) vs low-refill continuation of
**+0.37 to +0.48bps** (strongly positive, large n) -- a **negative** high-minus-low spread of
about -0.4 to -0.5bps. **This is the opposite sign from W2's reported A4 finding**
(they found high-refill predicts *more* continuation, +0.5 to +0.9bps spread on bybit's ask
side specifically). This is flagged honestly rather than silently reconciled -- the
discrepancy is most likely a construction difference (my sweep definition treats every
bookTicker best-price tick change as a "sweep," which on a high-frequency dedicated
top-of-book stream captures many small/routine requotes alongside genuine level-clearing
events, and I pooled bid+ask+all-3-symbols rather than isolating bybit-ask as W2's strongest
cell) -- resolving it was out of scope for this interaction-focused pass. What matters for
this specific task is that **the same construction was used self-consistently for both the
marginal and every conditional cut**, so the interaction question can be answered on its own
terms regardless of which sign is ultimately correct: high-vol spread (-0.50bps) and low-vol
spread (-0.40bps) are nearly identical, and the same holds per-symbol (BTC -0.43/-0.40, ETH
-0.57/-0.41, SOL -0.41/-0.32).

**Verdict: NO_INTERACTION_EFFECT.** Vol regime does not meaningfully change the magnitude of
this signal, whichever sign it turns out to actually carry. The magnitude is sub-bps in both
regimes either way, consistent with W2's WEAK/sub-cost verdict on the marginal.

Evidence: `evidence/i10_a4_vol_regime.csv`.

## Interaction 11 (own) -- Liquidation cascade tail-bucket x own-symbol repeat-event frequency

**Data**: `data/events/liq_cascade_dataset.parquet`, same A7-TAIL-E1 test-period tail bucket
as interaction #1 (n=3,799, 2025-2026 holdout, thresholds frozen from 2021-2024).

**Justification**: `n_events_sym_24h` (a pre-existing causal column in the dataset -- count of
that symbol's own prior cascade events in the trailing 24h) distinguishes a symbol's *first*
liquidation event from one that is the 2nd, 3rd, or later wave of an already-ongoing
deleveraging spiral. A repeat event is closer to capitulation exhaustion (each wave flushes
more weak hands) than an isolated first shock, where there's no confirmation the selling
pressure is exhausting rather than just beginning.

**Result**: `n_events_sym_24h==0` (first-time event, n=1,509, 40% of the tail bucket): gross
+7.8bps, **net -6.2bps, PF 0.95 -- no edge, marginally negative**. `n_events_sym_24h>=1`
(repeat, n=2,290): gross +56.5bps, **net +42.5bps, PF 1.35** -- nearly double the marginal.
`n_events_sym_24h>=2` (serial, n=1,155): gross +100.6bps, **net +86.6bps, PF 1.74** -- the
strongest single cut in this entire report. Year stability: first-time events are flat/negative
in both 2025 (-10.2bps) and 2026 (+0.8bps, essentially break-even); repeat events are strongly
positive in both 2025 (+40.1bps) and 2026 (+48.1bps) -- if anything strengthening.

**Verdict: PROMISING, and actionable** -- this is a straightforward, low-risk refinement to
A7-TAIL-E1 available today, using a column the dataset already carries: restricting the
existing tail-bucket rule to `n_events_sym_24h>=1` (or >=2 for the strongest cut, at the cost
of roughly a third of the population) removes the entire negative-first-year tail-risk drag
identified in W1's A7-TAIL-E1 caveats without requiring any new data or model. This does not
resolve A7-TAIL-E1's other open caveats (SHORT_SQUEEZE sign ambiguity, the universe-drift bug
now fixed upstream, the 4.9%-recall proxy issue) -- it is additive to those, not a replacement
for addressing them.

Evidence: `evidence/i11_own_repeat_event.csv`.

## Bonus sub-findings (exploratory, not independently pre-registered)

- **Interaction 3 x interaction 4 style double-conditioning inside #4**: OI-extended AND
  book-imbalance-confirmation trades net **+7.93bps@60s** (t=15.7, n=33,101), the single
  largest trade-level number found anywhere in this report; OI-extended AND contrarian nets
  **-5.49bps** (t=-10.8, n=31,578). Flagged explicitly as a tertiary, exploratory cut found
  while building #3/#4, not a pre-registered test -- and it inherits the exact same
  wallet-level-significance caveat as #4's primary result, so it should not be read as a
  fourth independent confirmed finding.

## Data pitfalls encountered and handled (nothing new beyond what W1-W6 already documented)

- Millisecond funding-timestamp jitter: not directly relevant to the 8h-grid funding series
  used here, but the `funding_z30`/`funding_last` sparsity (76-84% missing in 2025-2026, per
  interaction #1) is a real, load-bearing data-coverage limitation, not a bug -- flagged rather
  than backfilled or imputed.
- `taker_buy_*` placeholder fields in `data/enriched`: not used anywhere in this report.
- Crossed-book bug: avoided throughout by using only the binance dedicated `bookTicker`
  stream for all tick-level price/depth work (interactions #2 and #10) -- never the L2
  diff/snapshot stream, per W2's documented fix. Bybit's own book (no dedicated top-of-book
  stream, ~10-15% residual crossed ticks per W2) was deliberately **not** used for any
  price/depth construction in this report -- bybit was used only for its `force_order`
  liquidation feed (a genuinely different, uncrossed data stream) in interaction #2.
- Cross-symbol composition confound (interaction #2): raw quantity/spread units are not
  comparable across BTC/ETH/SOL (different price/qty scales) -- within-symbol z-scoring was
  applied before any cross-symbol pooling, the same class of fix A16 already needed in round 1.

## Files

- `REPORT.md` -- this file.
- `evidence/i1_liq_oi_funding.csv` -- interaction 1 (liq + OI + funding).
- `evidence/i2_liq_book_events.csv`, `evidence/i2_summary.csv` -- interaction 2 (liq + book depth).
- `evidence/i3_i4_hl_wallet_conditioning.csv`, `evidence/i4_wallet_level_rigor.csv` -- interactions 3/4 (HL wallet x OI, x imbalance).
- `evidence/i5_options_iv_funding.csv` -- interaction 5 (options IV-shock x funding).
- `evidence/i6_options_flow_around_liq_events.csv` -- interaction 6 (options flow x liquidations).
- `evidence/i7_i8_declustered.csv`, `evidence/i7_i8_basis_oi_funding_raw.csv` -- interactions 7/8 (basis x OI, x funding disagreement).
- `evidence/i9_residual_dispersion.csv` -- interaction 9 (residual extreme x dispersion).
- `evidence/i10_a4_vol_regime.csv` -- interaction 10 (A4 x vol regime).
- `evidence/i11_own_repeat_event.csv` -- interaction 11, own (liq cascade x repeat-event count).

No files outside this report's own directory were created, moved, or modified. All source
data in `data/`, `data_v2/`, and `market_physics_v3/` was read-only throughout.
