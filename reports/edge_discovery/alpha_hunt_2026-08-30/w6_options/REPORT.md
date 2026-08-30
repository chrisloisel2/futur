# W6 — Options surface mining, round 2 (17 mechanisms beyond A14)

Round-2 sweep of `data/options_backfill/deribit` (16.27M BTC trades, 2023-01→2026-07-17,
per-trade IV/strike/expiry/direction/block flag + DVOL), joined causally against BTC perp
price (`data/enriched/BTCUSDT_1h_enriched.parquet`, Binance, hourly, 2017→2026-08-30) and
Binance perp premium (`data/derivatives_backfill/binance_vision_premium/BTCUSDT_premium_5m.parquet`).
Read-only throughout; no OI anywhere in this data and no ETH trade-level data — both
confirmed absent (again) and every metric that would need them was skipped explicitly
(noted per-mechanism below), never approximated. **17 mechanisms tried**, all genuinely
distinct from A14 (IV-shock→RV) and from the closed `OPTIONS_POSITIONING_4H` protocol
(NO_EDGE_DEFINITIF, all-flow/4h-bucket/return-target) — none reproduce either prior
construction; see each mechanism's "distinct from" note.

**Methodology note on confound control**: A14 caught that IV-shock signals partly reflect
"today was already volatile → tomorrow tends to stay volatile" (RV clustering), and reported
partial IC after controlling for `rv(t)`. Every RV-target mechanism below gets the same
treatment: a rank-residualized partial Spearman IC controlling for same-day (daily panel) or
trailing-24h (hourly panel) realized vol. **This materially changed several verdicts** —
two mechanisms that looked strong on raw IC (M5, M7) collapsed or flipped sign under control
and were downgraded; one (M2) looked like it might collapse but instead retained the
strongest confound-checked signal in this batch.

## Ranked table

| rank | mechanism | metric | N | net-of-cost | stability | confidence | status |
|---|---|---|---|---|---|---|---|
| 1 | **M2** — RV/IV spread level → own forward mean-reversion | partial IC −0.39 (raw −0.64) | 1,291 days | no vehicle (variance-swap style, not present here) | sign-stable both halves (−0.67/−0.63 raw) | high (confound survives at 1.7x A14's own pass bar) | **PROMISING**, discovery-stage |
| 2 | **M6** — Far-OTM put share → forward RV (crash-hedge demand) | partial IC +0.16 (raw +0.22–0.24) | 1,294 days | no vehicle | sign-stable (+0.22/+0.22) | medium-high | **PROMISING**, discovery-stage |
| 3 | **M17** — Hourly block-trade count → RV at 4h/24h | partial IC +0.10 (raw +0.22–0.31) | 31,050 hours | no vehicle | sign-stable (+0.23/+0.22) | medium (small effect, huge n) | **PROMISING**, discovery-stage — extends A14's hourly block signal to longer horizons |
| 4 | **M8** — Cross-expiry relative IV change → forward RV | partial IC +0.08 (raw +0.16–0.31) | 1,293 days | no vehicle | sign-stable (+0.18/+0.13) | low-medium (borderline confound survival) | **WEAK/PROMISING borderline** — flag, don't lean on it alone |
| 5 | M9 — Monthly-expiry proximity → RV/IV | Kruskal p=0.015; IV-crush IC −0.08 | 1,294 days (42 in extreme bucket) | no vehicle | not a chronological split (cross-sectional) | low | **WEAK** — suggestive post-expiry IV-crush pattern, n too small to trust |
| 6 | M5 — Block-trade activity level → forward RV | raw IC +0.09–0.14, partial −0.11 (**sign-flipped**) | 1,290 days | — | sign-stable raw but magnitude collapses h2 (0.21→0.02) | low | **WEAK** — confound-killed, textbook example |
| 7 | M7 — Term-structure level (near−far ATM IV) → forward RV | raw IC +0.24–0.37, partial +0.06 | 1,294 days | — | sign-stable raw | low | **WEAK** — mostly RV clustering relabeled |
| 8 | M10 — IV regime switch (tercile) → forward RV | low→high vs none p=0.012 | n=9 switch days | — | n/a | very low | **WEAK** — real cohorts too small (n=9) |
| 9 | M12 — Rolling 3d/7d cumulative flow → forward RV/returns | best \|IC\|=0.15 (cum_flow_7d→rv_fwd5d) | 1,260–1,292 | — | not tested (best cell only) | low | **WEAK** — returns legs null, RV leg modest |
| 10 | M13 — Options flow → Binance perp basis/premium | IC +0.067, p=0.017 | 1,272 days | — | sign-stable, weak | low | **WEAK** |
| 11 | M15 — Put/call volume ratio level → forward RV | raw IC +0.07–0.09, partial +0.03 (n.s., p=0.21) | 1,294 days | — | — | low | **WEAK** — confound-killed |
| 12 | M1 — DVOL shock → own forward change (vol-of-vol mean reversion) | IC −0.06 (fwd3d), −0.01 (fwd1d) | 1,285–1,287 days | — | — | — | **DEAD** |
| 13 | M3 — Block-trade flow → forward perp returns | IC −0.02 to −0.02, p≈0.55 | 1,290 days | n/a (no signal) | — | — | **DEAD** |
| 14 | M4 — Large non-block flow → forward perp returns | IC ≈0.001–0.003, p≈0.9–0.97 | 1,294 days | n/a | — | — | **DEAD** |
| 15 | M11 — BTC move → IV repricing (leverage + magnitude + fade) | leverage IC −0.03 (n.s.); magnitude −0.05 (p=0.06); fade IC −0.09 (n=130, n.s.) | 1,293 days | — | — | — | **DEAD** |
| 16 | M14 — DVOL shock → forward perp returns (directional) | IC +0.05/+0.09, weak/mixed | 1,288 days | — | — | — | **DEAD** |
| 17 | M16 — Hourly ATM-IV shock → next-hour return/RV | IC −0.005 to −0.008, n.s. | 31,047 hours | — | — | — | **DEAD** |

None of the 17 has a real execution vehicle in this dataset — every PROMISING result here is
an RV-forecasting or spread-dynamics signal, structurally identical to A14's situation
(discovery-stage information content, no options greeks/execution capability in this
environment to harvest it directly). The only mechanisms tested with a genuine, already-built
execution vehicle (the perp leg, ≈5bps one-way/≈10bps round-trip) were the two directional
flow tests, M3 and M4 — both came back dead on signal before cost was even a question.

---

## Data recap (unchanged from A14's audit, not rediscovered)

`data/options_backfill/deribit/trades/BTC/*.parquet`: 43 monthly files, 16,265,833 trades,
2023-01-01 → 2026-07-17, columns `ts, price, mark_price, iv, index_price, direction, amount,
expiry, strike, cp, is_block`. IV cleaned to `(1,300)` before any ATM/term aggregation (0.51%
of rows have garbage IV ≥300, mostly deep-ITM/illiquid one-off prints; excluded, not imputed).
`features/BTC_daily.parquet` (1,294 days) and `DVOL_BTC_1d.parquet`/`DVOL_ETH_1d.parquet`
used as-is where noted. Perp price: `data/enriched/BTCUSDT_1h_enriched.parquet` (Binance,
hourly, 2017-08→2026-08-30) — chosen over the raw `derivatives_backfill/bybit` hourly klines
because it's the project's canonical enriched panel and extends well past the options data's
end date, giving clean forward-outcome runway. Basis proxy:
`derivatives_backfill/binance_vision_premium/BTCUSDT_premium_5m.parquet` (Binance perp premium
index, 2021→2026-07). **Confirmed again: no open interest anywhere in this data, no ETH
trade-level data.** Every mechanism needing true OI-weighted strikes (a real GEX/max-pain/pin
proxy) was skipped explicitly rather than faked; see M9.

**Causality discipline**: every daily-panel forward outcome is computed from perp bars with
timestamp ≥ the bucket's UTC end (`date + 1 day`), never `nearest`. Every hourly-panel outcome
uses bars ≥ `hour + 1h`. Deribit (options) and Binance (perp/premium) are different
venues/clocks (hard rule 7) — joins are on UTC calendar day/hour, a coarse but consistent
grid, never sub-second aligned.

---

## Mechanism detail

### M1 — DVOL shock → own forward change (vol-of-vol mean reversion) — DEAD

**Hypothesis**: a DVOL spike today mean-reverts tomorrow (distinct from A14, which used DVOL
shocks to forecast the *underlying's* RV, not DVOL's own dynamics).
`d_dvol(t) → dvol(t+1)-dvol(t)`: IC −0.013 (p=0.63, n=1287). `→ dvol(t+3)-dvol(t)`: IC −0.060
(p=0.031, n=1285) — technically significant at fwd3 but far too small to act on and not
replicated at fwd1. **DEAD.** No execution vehicle either way (would need variance swaps on
DVOL itself, absent here).

### M2 — RV/IV spread level → own mean reversion / forward RV — PROMISING (strongest finding)

**Hypothesis**: `spread(t) = atm_iv(t) − sameday_realized_vol(t)` (the current variance-risk-
premium level) mean-reverts and/or predicts forward RV — distinct from A14 (IV *shocks*) and
from `reports/options/VOL_RISK_PREMIUM.md` (which measured the premium's unconditional
existence, not whether its day-to-day level times its own forward change).

Raw: `spread(t) → spread(t+3)-spread(t)`: IC −0.643 (p<1e-16, n=1291). This raw number is
**mostly mechanical** — because IV moves far less day-to-day than realized vol does, `spread(t)`
is close to `-sameday_rv(t)` in disguise, confirmed by a baseline check using `-sameday_rv(t)`
alone as the predictor: IC −0.568, nearly identical to the full spread's −0.643. **But** after
formally partialling out `sameday_rv(t)` (rank-residualized), a large incremental **partial IC
of −0.388 remains (n=1291, p<1e-16)** — stronger survival than A14's own confound-passing bar
(+0.224 partial). Sign-stable across both time-split halves (raw −0.672 / −0.627).
`spread(t) → rv_fwd1d` directly: IC −0.162 (p<1e-16). **Verdict: PROMISING, confound-checked,
discovery-stage.** No execution vehicle in this dataset (would need to trade realized-vs-
implied variance directly — variance swaps / synthetic var — not present; the existing project
VRP short-variance overlay is the natural home for this, same recommendation as A14, not
executed here per scope).

### M3 — Block-trade net flow → forward perp returns (daily) — DEAD

**Hypothesis**: Deribit block trades (the official institutional execution channel,
`is_block=True`) carry directional information. Distinct from the closed
`OPTIONS_POSITIONING_4H` protocol (which used *all* flow, not block-restricted, at 4h buckets
with z-score normalization) by restricting to the institutional-channel subset at daily
horizon, both raw and z-scored.

`block_flow(t) → ret_fwd1d`: IC −0.017 (p=0.55, n=1290). `→ ret_fwd3d`: IC −0.025 (p=0.37).
Z-scored version: IC −0.017 (p=0.56). **DEAD** — no directional signal in the institutional
block channel at daily horizon.

### M4 — Large non-block trade flow → forward perp returns (daily) — DEAD

**Hypothesis**: trades that don't clear Deribit's official block threshold but are still large
(top-5%-by-notional of a day's non-block trades) might carry similar informed flow to M3.
`large_flow(t) → ret_fwd1d`: IC +0.0009 (p=0.97, n=1294). `→ ret_fwd3d`: IC +0.0033 (p=0.91).
**DEAD** — flatly null, no better than M3's official block channel.

### M5 — Block-trade activity level → forward RV — WEAK (confound-killed, sign-flipped)

**Hypothesis**: days with unusually high block-trade activity (count/notional, not sign)
precede higher RV — distinct from A14's `block_share` (a *share*-of-total metric) since this
uses raw activity level.

Raw: `block_count → rv_fwd1d` IC +0.140 (p<1e-16), `block_notional → rv_fwd1d` IC +0.091
(p=0.001). Time-split: h1 IC +0.213 (p<1e-16), h2 IC +0.024 (p=0.54) — **magnitude collapses
in half 2**, an instability the raw IC alone hides. Confound check: partial IC controlling for
`sameday_rv` = **−0.114 (p=3.8e-5)** — the sign flips negative once same-day vol is held
constant. **Verdict: WEAK.** A clean textbook confound-kill: the raw positive relationship was
almost entirely "block trading happens more on already-volatile days, and volatile days
cluster," not new information from block activity itself.

### M6 — Far-OTM put activity share → forward RV (crash-hedging demand) — PROMISING

**Hypothesis**: the daily notional share concentrated in far-OTM puts (strike ≤0.85x spot) is
a tail-hedging-demand gauge; rising crash-protection buying should precede RV expansion.
Distinct from A14's `top_strike_share` (ATM concentration, opposite tail of the strike
distribution).

Raw: `otm_put_share → rv_fwd1d` IC +0.237, `→ rv_fwd3d` IC +0.216 (both p<1e-16, n=1294).
Confound check: partial IC controlling for `sameday_rv` = **+0.158 (p<1e-16)** — survives
comfortably, a genuine incremental signal beyond vol clustering. Time-split: h1 +0.216, h2
+0.223 — essentially identical, excellent stability. `→ ret_fwd1d`: IC −0.006, n.s. — no
directional edge, RV-only as expected of a hedging-demand proxy. **Verdict: PROMISING,
discovery-stage.** No execution vehicle (RV-forecasting signal, same limitation as A14).

### M7 — Term-structure level (near-30d minus far-90/180d ATM IV) → forward RV — WEAK (confound-killed)

**Hypothesis**: backwardation (`term_slope>0`, near IV above far IV) signals near-term stress
and should precede higher RV; contango signals calm. Trade-flow-implied median IV per
expiry-bucket, explicitly **not OI-weighted** — flagged as a trade-flow proxy, not a true
OI-weighted term structure.

Raw: `term_slope → rv_fwd1d` IC +0.375, `→ rv_fwd3d` IC +0.244 (both p<1e-16, n=1294) — looked
like the strongest candidate on raw numbers alone. Confound check: partial IC controlling for
`sameday_rv` = **+0.060 (p=0.03)** — collapses to near-nothing. **Verdict: WEAK.** Near-far
term structure moves mostly *with* contemporaneous realized vol (both spike together during
stress), so most of its apparent forward-RV predictive power is vol clustering wearing a
term-structure label, not new information about what happens next.

### M8 — Cross-expiry relative IV change (near-far divergence) → forward RV — WEAK/PROMISING borderline

**Hypothesis**: when near-dated IV moves *more* than far-dated IV on the same day (a term-
structure-steepening shock), that divergence itself — not either leg's shock alone — predicts
forward RV. A term-structure-specific RV trade, distinct from A14's single-IV shock and from
M7's level test.

Raw: `d(near_iv)-d(far_iv) → rv_fwd1d` IC +0.307, `→ rv_fwd3d` IC +0.157 (p<1e-16, n=1293).
Confound check: partial IC controlling for `sameday_rv` = **+0.082 (p=0.003)** — barely clears
a meaningful-effect bar, right at the edge. Time-split sign-stable (+0.184/+0.125) but h2
roughly a third weaker than h1. Bonus: the divergence itself mean-reverts (`slope(t+3)-slope(t)`
IC −0.248 vs `rel_term_change`, p<1e-16), i.e. steepening shocks partially unwind within 3 days.
**Verdict: WEAK-to-borderline-PROMISING** — real enough to flag, not solid enough to lean on
alone; treat as a corroborating signal alongside M6/M2, not a standalone candidate.

### M9 — Monthly-expiry-proximity effect (pinning / IV crush, no OI) — WEAK

**Hypothesis**: as BTC approaches Deribit's standard monthly expiry (last Friday of month,
08:00 UTC — a fixed calendar rule computed independently of the data, not from "nearest
listed expiry" which is data-dependent). **Important construction note**: naively using
"nearest ANY listed expiry" is useless here — Deribit lists ~daily (0DTE-style) expiries, so
`min_days_to_expiry` across all trades is ~0 (mean 0.0097 days, i.e. minutes) on essentially
*every single day* of the sample; that naive version was tried first, found broken, and
discarded before this cleaner calendar-based version.

Bucketed by days-to-next-monthly-expiry: the `0-1d` bucket (n=42) shows notably *lower*
forward RV (mean 21.5 vs ~40-43 in all other buckets) and a sharp ATM-IV drop the next day
(mean d_atm_iv −2.30 vs ±0.5 elsewhere) — consistent with a real post-expiry IV-crush/vol-
compression pattern. Kruskal-Wallis across all 6 buckets: p=0.015 (marginal, not <0.01).
IV-crush IC (`days_to_expiry → atm_iv level`): −0.080 (p=0.004) — weak but signed correctly
(IV is *lower* closer to expiry, i.e. compresses toward expiry, consistent with the crush
pattern). **Verdict: WEAK** — the pattern is directionally coherent and worth a second look
with more data, but n=42 in the driving bucket and p=0.015 (not below the stricter 0.01 bar
used elsewhere) mean it isn't solid yet. True pin-risk/max-pain analysis needs OI —
**explicitly SKIPPED**, not approximated.

### M10 — IV regime switch (tercile low/mid/high) → forward RV/returns — WEAK

**Hypothesis**: a regime *transition* (jumping tercile-to-tercile) carries different forward-RV
information than persisting in a regime or than a single-day shock magnitude (A14's
construction) — tests the discreteness of the switch itself.

`low→high` switch vs no-switch cohort: mean `rv_fwd3d` 55.4 vs 42.2, Mann-Whitney p=0.012.
`high→low` vs no-switch: 42.3 vs 42.2, p=0.41 (null). **Verdict: WEAK** — the low→high result
is suggestively large but the switch cohort is only **n=9 days** in the whole 1,294-day
sample (terciles computed in-sample, a further caveat flagged for any live use) — nowhere near
enough to trust a p=0.012 on 9 observations.

### M11 — BTC move → IV repricing (leverage effect + magnitude + overreaction fade) — DEAD

**Hypothesis**, three sub-tests, reverse causality from A14: (a) leverage effect — does a
signed down-move raise next-day ATM IV more than an equal up-move (classic equity-style
leverage effect, untested here before); (b) magnitude — does `|move|` predict `|repricing|`
regardless of sign; (c) conditional on the top-decile biggest-move days, does the resulting
IV repricing then reverse the following day (overreaction fade, a short-vol-after-spike timing
trade).

(a) `sameday_ret → d_atm_iv_fwd1`: IC −0.030 (p=0.28, n=1293) — directionally consistent with
a leverage effect (negative sign) but not significant. (b) `|sameday_ret| → |d_atm_iv_fwd1|`:
IC −0.052 (p=0.06, n=1293) — **wrong-signed** (bigger moves associated with *smaller*
repricing magnitude, opposite of the naive expectation) and only marginal. (c) conditional
fade on the n=130 top-decile-move cohort: IC −0.092 (p=0.30) — not significant, too few
observations. **Verdict: DEAD** across all three constructions — none clears significance,
and (b)'s sign is the wrong direction for the intuitive story anyway.

### M12 — Rolling 3d/7d cumulative options flow → forward RV/returns (positioning momentum) — WEAK

**Hypothesis**: sustained multi-day positioning buildup (not a single-day flow reading, which
is the closed `OPTIONS_POSITIONING_4H` protocol's construction) predicts subsequent direction
or vol — a momentum/persistence variant.

6 combinations tested (3d/7d x {net_flow, block_flow} x {returns, RV}). Returns legs: all null
(`cum_flow_3d→ret_fwd3d` IC +0.004, `cum_flow_7d→ret_fwd5d` IC +0.003, `cum_block_flow_3d/7d`
IC −0.014/+0.032, all p>0.25). RV legs: `cum_flow_3d→rv_fwd3d` IC −0.076 (p=0.006),
`cum_flow_7d→rv_fwd5d` IC −0.147 (p<1e-16) — modest but real, sign says *heavier* cumulative
positioning precedes *lower* forward RV (a calming/exhaustion story, not a stress story).
**Verdict: WEAK** — the RV leg is worth a note but not strong enough to promote standalone,
and no confound control was run on this one (time-budget triage, flagged rather than silently
omitted); the returns legs are flatly dead.

### M13 — Options flow → forward perp basis/premium change (Binance) — WEAK

**Hypothesis**: aggregate Deribit options flow predicts subsequent widening/narrowing of the
Binance BTCUSDT perp premium the next day — tests whether options positioning leaks into a
*different* venue's pricing, not perp returns directly.

`net_flow_all → basis_change_fwd1`: IC +0.067 (p=0.017, n=1272). `block_flow`: IC +0.029
(p=0.30, n.s.). `cum_flow_7d`: IC +0.013 (p=0.65, n.s.). **Verdict: WEAK** — one of three cuts
clears p<0.05 at a small effect size, the other two don't; not solid, and cross-venue day-level
alignment (Deribit vs Binance, hard rule 7) adds noise on top.

### M14 — DVOL shock → forward perp returns (directional) — DEAD

**Hypothesis**: A14 used DVOL shocks to forecast the underlying's realized VOL (IC
+0.128/+0.148). Here the same DVOL-shock signal is tested against *directional* forward
returns instead.

`d_dvol → ret_fwd1d`: IC +0.045 (p=0.10, n=1288). `→ ret_fwd3d`: IC +0.087 (p=0.002) —
technically significant at fwd3 but small and not replicated at fwd1; `|d_dvol| → ret_fwd1d`:
IC +0.005, n.s. **Verdict: DEAD** — no reliable directional signal, consistent with A14's own
finding that its directional ICs all had signs flipping between time-split halves.

### M15 — Put/call volume ratio level (not delta) → forward RV — WEAK (confound-killed)

**Hypothesis**: the absolute *level* of put/call volume ratio (a standing positioning-skew
gauge) predicts forward RV, distinct from A14 (day-over-day *delta* of related flow fields)
and from the closed positioning protocol (targeted returns, not RV, and used deltas).

Raw: `pc_volume_ratio → rv_fwd1d` IC +0.089 (p=0.001), `→ rv_fwd3d` IC +0.071 (p=0.011).
Confound check: partial IC controlling for `sameday_rv` = **+0.035 (p=0.21, n.s.)**.
**Verdict: WEAK** — confound-killed, same pattern as M5/M7: the raw level correlates with
current market stress, and once that's held constant nothing significant remains.

### M16 — Hourly ATM-IV shock (not flow) → next-hour perp return/RV — DEAD

**Hypothesis**: A14's hourly test used flow variables (`notional_z`, `n_block`,
`block_share_hr`, `cp_imbalance`) to forecast next-hour `|return|`. This tests the hourly
IV-*level* shock itself (`d_atm_iv` within the hour), which A14 never tested at hourly
granularity (only daily `d_atm_iv_traded`).

`d_atm_iv → ret_fwd1h`: IC −0.008 (p=0.17, n=31047). `→ rv_fwd1h`: IC −0.005 (p=0.42).
`|d_atm_iv| → rv_fwd1h`: IC −0.045 (p<1e-16) — statistically significant purely from the huge
n, but the sign is wrong for a "bigger IV shock → more forward vol" story, and the magnitude
is negligible. **Verdict: DEAD.** Caveat: hourly ATM trade counts are sparse (median well
under 10 trades/hour in the ±7% moneyness band), a real data-density limitation for any
hourly IV-level signal, separate from the finding itself.

### M17 — Hourly block-trade count/notional → forward RV at 4h/24h — PROMISING (modest, robust)

**Hypothesis**: A14's hourly test forecast only 1h-forward `|return|`. This tests whether the
same hourly block-trade signal has predictive power at *longer* RV horizons (4h, 24h) — does
block-flow information decay fast (pure microstructure noise) or persist (genuine
information)?

Raw: `block_count → rv_fwd4h` IC +0.308, `→ rv_fwd24h` IC +0.221 (both p<1e-16, n=31050).
`block_notional → rv_fwd24h` IC +0.106 (p<1e-16, n=21227 — fewer because zero-block hours are
excluded from the notional cut). Confound check: partial IC controlling for a matching
`trail_rv_24h` (24h trailing realized vol) = **+0.100 (p<1e-16)** — smaller than the raw
number but still highly significant given n=31,050. Time-split: h1 +0.229, h2 +0.217 —
essentially identical, excellent stability. **Verdict: PROMISING, discovery-stage** — modest
effect size but very robust given the sample size; this is the same underlying economic
mechanism as A14's hourly block signal, extended to show the information persists out to a
full day rather than decaying within the hour, which is itself a useful characterization of
*how* the A14 signal behaves at longer horizons (not a separate discovery of new alpha, more
a robustness/decay-curve extension of an already-PROMISING A14 sub-finding).

---

## What this round adds to A14 / the standing options picture

- **A14 remains the single strongest options signal found across both rounds** (confound-
  controlled partial IC +0.224 daily / +0.113 hourly, per the 2026-08-29 report) — nothing
  here beats it outright.
- **M2 (RV-IV spread mean reversion) is the strongest NEW finding this round** — its
  confound-checked partial IC (−0.39) is actually larger in magnitude than A14's own daily
  partial IC (+0.22), though it targets a different outcome (the spread's own forward change,
  not the underlying's RV) and — like A14 — has no execution vehicle in this dataset.
- **M6 (crash-hedging demand share) and M17 (block flow persistence at longer RV horizons)**
  are solid, confound-checked, stable secondary findings.
- **Two clean confound-kills (M5, M7, M15)** are useful negative results in their own right —
  they demonstrate the same "IV/flow co-moves with contemporaneous vol, so naive forward-RV
  correlations are partly RV clustering in disguise" trap that A14 correctly avoided, caught
  here on three more candidate constructions before they could be reported as real.
- **All directional-return mechanisms tried (M3, M4, M11, M14, M16) came back DEAD or
  wrong-signed** — reinforcing A14's own conclusion (all its directional ICs flip sign
  across time-split halves) and the standing closed-protocol verdict that options flow does
  not cleanly forecast BTC perp *direction* in this dataset, across every construction tried
  in either round. Volatility forecasting, not direction, is where this data's information
  content lives.
- **Every finding here is discovery-stage with no execution vehicle**, exactly like A14 — the
  practical next step, if pursued, is the same one already flagged for A14: route into the
  existing VRP short-variance overlay as a conditioning signal, not build a new standalone
  sleeve. That routing work is out of scope for this worker.

## Mechanisms explicitly skipped (needed OI, not approximated)

- True RR25/BF25 risk-reversal/butterfly (needs OI-weighted strikes, not just trade-implied
  median IV per moneyness band) — skipped, M7/M8 use trade-flow proxies instead and say so.
- GEX / dealer-gamma-exposure proxy — needs OI to weight strikes by dealer positioning;
  skipped entirely, no approximation attempted.
- True pin-risk / max-pain (needs OI concentration at strikes near expiry) — skipped; M9
  substitutes a pure expiry-calendar-proximity test instead, explicitly flagged as weaker.
- Anything requiring ETH options trade-level data — confirmed absent again (only ETH DVOL
  exists); not attempted.

## Files

- `evidence/w6_mechanism_results.json` — full per-mechanism results (hypothesis, data source,
  metric, main IC table, confound partial-IC where applicable, time-split stability, verdict,
  notes) for all 17 mechanisms, ~24KB.
