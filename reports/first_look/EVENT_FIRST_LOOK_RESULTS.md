# Event first look — results (one look, 2026-09-10)

LOOK_LEDGER seq 7 · prereg `reports/first_look/event_reaction_v1_PREREG.md` (commit b74bcc3, pushed) ·
snapshot sha256 `6e957851e452395f…` (tape at b0d483b, 7 449 rows) · harness pinned · family `news`,
threshold_t(4) = 2.2414 one-sided · budget 6 → 2 · run 14:01:23 → 14:32:51 UTC.

Statistic = side × (asset − BTCUSDT) in bps, entry = open of the first 1-min bar ≥ publication + 60 s
(+15 min for H2), cluster-robust SE by publication day. Verdicts are those of the prereg, in its order.

| H | mechanism | primary | n | mean | median | t | wall | N_eff / top-1 | tradable | pre-pub drift | placebo −48 h | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | `event_reaction_v1` | 60m | 142 | -3 | 14 | -0.04 | 84 | 51 / 0.17 | 1.00 | 77 | 55 | **REJECTED_NO_GROSS** |
| H2 | `event_listing_reversal_v1` | 6h | 141 | 202 | 169 | 3.25 | 84 | 64 / 0.07 | 0.00 | -78 | -187 | **INDECIDABLE** |
| H3 | `event_delisting_pressure_v1` | 60m | 56 | 631 | 67 | 2.44 | 59 | 22 / 0.13 | 0.82 | -11 | 3 | **INDECIDABLE** |
| H4 | `event_cross_venue_lag_v1` | 15m | 272 | -0 | 1 | -0.03 | 59 | 81 / 0.09 | 1.00 | 36 | 0 | **REJECTED_NO_GROSS** |

Sensitivities (descriptive, never selected on):
- H1 — 60m: -3 (med +14, t -0.04) · 15m: +59 (med +19, t 1.70) · 6h: -133 (med -155, t -1.66)
- H2 — 6h: +202 (med +169, t 3.25) · 60m: +82 (med +20, t 2.30) · 24h: +332 (med +342, t 3.08)
- H3 — 60m: +631 (med +67, t 2.44) · 6h: +1077 (med +275, t 4.08) · 24h: +1246 (med +681, t 3.44)
- H4 — 15m: -0 (med +1, t -0.03) · 5m: +9 (med -1, t 1.35) · 60m: +12 (med -0, t 0.66)

By year (mean at the primary horizon): H1 2019: +55 (n=2), 2020: +114 (n=32), 2022: -293 (n=2), 2023: -57 (n=58), 2024: +12 (n=33), 2025: -42 (n=15) · H2 2019: -42 (n=2), 2020: +44 (n=31), 2022: +391 (n=2), 2023: +224 (n=58), 2024: +246 (n=33), 2025: +357 (n=15) · H3 2020: +16 (n=1), 2022: -331 (n=4), 2023: +1408 (n=3), 2024: +788 (n=14), 2025: +519 (n=22), 2026: +829 (n=12) · H4 2022: +160 (n=2), 2023: +13 (n=50), 2024: -35 (n=42), 2025: +23 (n=61), 2026: -8 (n=117).

Funnel: H1/H2 351 candidates → 209 without a Binance spot market 24 h before (the perp is the
first Binance market) → 142 measured. H3 79 → 22 without market → 1 missing entry bar → 56.
H4 941 → 668 without a Binance market → 272. Vision: 2 378 daily files fetched, 2 278 × 404, 0 errors.

## What the two rejections say

**H1 — no continuation after 60 s.** The listing pump is fully inside the first minute: for POWR the
bar opening right after publication is already +1 253 bps, TROY +2 411 bps. At entry (60–120 s) the
move is done; over the next hour the mean is −2 bps. The fast part of the event is arbitraged by
keyword bots in seconds. **H4 — no cross-venue lag.** Binance does not react after the official
OKX/Bybit timestamp (−0.3 bps at 15 min, SE 9.6: a tight zero); if anything the Binance market
moved *before* (+36 bps in the prior hour vs 0.3 in the placebo): the information is public before
the venue's own timestamp. Both are clean, well-powered zeros.

## What the two INDECIDABLE say — and the "trop beau" check

**H2 — listing pump fade (+202 bps at 6 h, t 3.25, 70 % positive, N_eff 64, top-1 7 %, consistent
2023–2025, 24 h: +332, t 3.08).** Passes gross, cost wall (84 bps, spot), threshold, concentration,
leak and placebo. Fails **only** tradability: the measured market is spot and a short on spot needs
borrow. Instrument check on the two largest contributions (raw 1-min bars): POWR pumps +1 253 bps in
the publication minute, is back to +54 at 15 min, −2 275 at 60 min, −3 742 at 6 h; TROY +2 411 →
+2 065 at 15 min → −140 at 6 h. Timestamps are right, the path is real. Context: the placebo is
**−187 bps** (the same window two days earlier shows a +1.9 % run-up vs BTC) and the pre-publication
hour is +78 bps: Binance announces perps on assets that were already running. The fade is the
unwinding of that run-up plus the headline pump. Economic payer: the keyword bot and the retail
buyer of the first minutes. Not executable as specified; executable variants are new hypotheses.

**H3 — delisting forced pressure (+631 bps at 60 min, median 67, t 2.44 > 2.24; 6 h: +1 077,
t 4.08; 24 h: +1 246, t 3.44).** Passes gross, cost wall (59 bps), threshold, tradability (46 of 56
on the perp), leak (−11) and placebo (+3, a clean zero). Fails **concentration**: N_eff 21.6 < 30,
top-1 13 % > 10 %; the median at 60 min (67 bps) barely clears the wall while the mean is carried
by DREP (−69 % in the hour), AIA (−36 %), CVP, NEIROETH, BEAM. Instrument check: DREP's crash starts
in the publication minute (−532 bps at +1, −1 031 at +2, −3 176 at +5, −7 686 at +60), AIA likewise
(−977 at +1, −4 837 at +10). The residual after our entry is large and real: forced selling continues
for an hour, not a minute. Caveats that the prereg could not price: these are micro-caps whose perp
is being wound down; the declared 4 bps spread + 4 bps slippage is not the cost of a 200 k$ short in
DREP; capacity is the binding constraint, not the edge.

Instrument defect found (I22): `trading_start_ts` in the tape is the date in the title parsed at
00:00, so it precedes the publication timestamp by 6–10 h for most listings. It was not used by this
look. For any perp-at-launch hypothesis the launch time must come from `fapi/v1/exchangeInfo`
`onboardDate`, not from the title.

## Status after the look

- 0 promotion. 2 × REJECTED_NO_GROSS (H1, H4), 2 × INDECIDABLE (H2, H3). No forward seal exists.
- The four mechanisms carry their verdict in `mechanisms/<id>/results/verdict.json`.
- The frozen tape is burned for these four hypotheses at these horizons. Any new hypothesis on it
  must be preregistered and consumes budget (2 tests left).

## Candidates written from this look (drafts, not sealed — `reports/loop/hypotheses/H-EVENT.md`)

1. **H3 forward** — same hypothesis, new Binance delisting announcements from 2026-09-11, one look
   when ≥ 30 new events have a Binance perp (≈ 18 months at the 2025 rate). Costs 1 test at the look.
2. **H2-executable on untouched events** — the 210 Binance perp listings whose perp was the first
   Binance market (never resolved, never priced): short the perp from launch (`onboardDate`) + 15 min
   to +6 h. Different events, different market; the direction is informed by H2, which the prereg
   must say. Costs 1 test.
3. **H3 cross-venue** — Bybit/OKX delisting announcements (403 with asset) measured on the Binance
   market of the same asset. Weaker mechanism (arbitrage, not forced flow on the measured venue),
   more events. Costs 1 test.
