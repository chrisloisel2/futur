# event_reaction_v1 — preregistered first look

Written and pushed BEFORE any price join. Sealed with `event_reaction_v1_FREEZE.json`
(snapshot hash, code pins). The look is executed once, by the pinned harness, after the
LOOK_LEDGER entry is written, and never re-run.

    collect → preregister → freeze → look once

## Dataset

Frozen input:
- `data_lake/events/official_event_tape.jsonl` at commit `b0d483b341a8b6949bd2141dcfa2e9ff6819bd75` (git blob `02baf00e3df9b246865742c7a0764cf5ec98d165`)
- frozen copy: `data_lake/first_look/event_reaction_v1/official_event_tape_frozen.jsonl`
- rows: 7449 · publication range 2017-07-21 → 2026-09-10
- sha256: `6e957851e452395f46a5fd7b725f7634d8e0cc9e2f17218c1658d07c2922e6c7`

No price join has been performed before this preregistration. The only data touched to write
it are the tape's own fields (source, type, title, timestamps): the selection counts below are
metadata counts, not outcomes.

Prices (fetched only at look time, by the harness): Binance Vision **1-minute klines**, daily
files, spot (`data/spot/daily/klines`) and USDS-M (`data/futures/um/daily/klines`), cached under
`data/vision_1m/` with a three-state manifest (ok / 404 / error). Benchmark: `BTCUSDT` spot.

## Families

Multiplicity family: kernel family `news`, sub-family `official_event_reaction`.
Nothing has been sealed in `news` by the research kernel before this look (run ledger: 0).
Four hypotheses are sealed together → one-sided Bonferroni within the family:

    threshold_t(4) = 2.2414   (research_kernel.multiplicity.threshold_t, α = 0.05)

The loop's lifetime bar (threshold_t(21) after this look ≈ 2.82) is reported alongside every
result as a descriptive flag; it is not the verdict criterion for this family.

## Hypotheses (one primary horizon each, direction fixed in advance)

| H | mechanism | events | market measured | side | entry | primary | sensitivities |
|---|---|---|---|---|---|---|---|
| H1 | `event_reaction_v1` | Binance USDS-M perp listing announcements | pre-existing Binance **spot** `<ASSET>USDT` | long | pub + 60 s | 60 min | 15 min, 6 h |
| H2 | `event_listing_reversal_v1` | same events | same spot market | short | pub + 60 s + 15 min | 6 h | 60 min, 24 h |
| H3 | `event_delisting_pressure_v1` | Binance asset delistings (spot "Binance Will Delist", perp "Binance Futures Will Delist") | Binance **perp** if it exists ≥ 24 h before, else spot | short | pub + 60 s | 60 min | 6 h, 24 h |
| H4 | `event_cross_venue_lag_v1` | OKX / Bybit spot or perp listing announcements | Binance perp if it exists ≥ 24 h before, else spot | long | pub + 60 s | 15 min | 5 min, 60 min |

H1 — Futures listing continuation: after an official Binance futures listing announcement, the
asset's pre-existing spot market continues up (excess of BTC) over the next hour.

H2 — Futures listing mean reversion: after the first 15 minutes of reaction, the asset gives
back part of the move over the following 6 hours.

H3 — Delisting forced pressure: after an official Binance delisting announcement, the asset
underperforms BTC over the next hour.

H4 — Cross-venue lag: after OKX or Bybit publishes a listing of an asset that already trades on
Binance, the Binance market reacts with a lag of more than 60 seconds.

## Event selection (pure functions of the record — `first_look.selection_reason`)

Allowed event types: `futures_listing`, `listing`, `delisting`, `futures_delisting`
(the tape also carries `alpha`, `launchpool`, `product_add`, `other`, `suspension`,
`tick_size_change`, `snapshot_baseline`: all excluded).

Excluded, in this order, with the reason recorded per event:
- events without `publication_ts_exchange` (snapshot-diff baselines: Coinbase, Hyperliquid)
- non-crypto titles (TradFi, bStocks, tokenized securities, quanto, stock CFDs, index perps)
- H1/H2: multi-asset titles without per-asset records ("Launch Multiple …"), coin-margined
- H3: trading-pair removal notices (only asset delistings count)
- H4: promotions and non-order-book products (Token Splash, Convert, Savings, Pre-Market,
  Launchpool, Earn, margin/collateral notices)
- asset unmappable (tape `asset`, or for Binance perp listings the symbol-form title `…ABCUSDT
  Perpetual Contract` → `ABC`); duplicate `raw_body_hash`; duplicate (source, type, asset, day)
- at look time: no Binance market of the required kind existing ≥ 24 h before publication
  (Vision daily file of D−1 exists and has a bar ≤ pub − 24 h); missing entry bar (> 2 min gap)
- events whose measured market IS the newly listed market are excluded by construction
  (the market must pre-exist by 24 h): pre-trading effects on a not-yet-open market are not
  tested here.

Candidate counts before market resolution (metadata only): H1 351, H2 351,
H3 79, H4 941. The counts after resolution are part of the result.

Symbol mapping: perp `<ASSET>USDT` (also `1000<ASSET>USDT`), spot `<ASSET>USDT` with the
`1000`/`1M` multiplier prefix stripped. Only USDT quotes.

## Horizons

Allowed: 1 min, 5 min, 15 min, 1 h, 6 h, 24 h. Each hypothesis declares ONE primary horizon
(table above) that decides the verdict; the two sensitivities are reported, never selected on.

## Metrics (per event and horizon)

- `return_bps` = 10⁴ · ln(open of exit bar / open of entry bar) on the measured market
- `benchmark_return_bps` = same on BTCUSDT spot, same bars
- `excess_return_bps` = return − benchmark; the statistic is side × excess
- `pre_excess_bps` over [pub − 60 min, pub − 1 min): the leak / already-open-trading check
- `volume_ratio` = mean 1-min volume in [entry, entry + 60 min) / mean in [pub − 60 min, pub)
- `entry_lag_s` = entry bar open − publication (60–180 s by construction)
- tradability flag: executable = long anywhere, or short on a perp; short on spot is not
  executable (no borrow assumed)
- placebo: the same event, same market, publication shifted −48 h

Entry bar: first 1-min bar with open_time ≥ entry time (next whole minute), tolerance 2 min.
Exit bar: entry bar + horizon (same tolerance). Missing → the event has no value at that horizon.

Statistics per hypothesis and horizon: N, mean (bps), median, cluster-robust SE by UTC
publication day, t = mean / SE, N_eff = (Σ|x|)² / Σx², top-1 share of the positive
contributions, share positive, by-year means (descriptive).

## Minimum gross threshold

A hypothesis is not interesting unless the **mean** gross excess move in the hypothesized
direction at the primary horizon exceeds **30 bps before costs**. The median is reported.

## Costs (P1.1 official schedule, VIP0, taker both legs; no institutional tier)

| market | fee / side | spread | slippage | round trip | wall (3×) |
|---|---|---|---|---|---|
| USDS-M perp | 5.0 bps (manifest `published_fee_schedules_2026-09-10.json`, official) | 4 | 4 | **18 bps** | **54 bps** |
| spot | 10.0 bps (Binance published spot schedule, VIP0, no BNB discount) | 4 | 4 | **28 bps** | **84 bps** |

Per hypothesis the cost is the event-weighted mean round trip of the markets actually used.
H1 and H2 are spot-only: their wall is 84 bps.

## Power (declared before the look, assumptions not measurements)

Assumed excess-return σ for alts: 60 bps at 15 min, 120 bps at 60 min, 300 bps at 6 h,
600 bps at 24 h. Minimum detectable effect at t = 2.2414: MDE = 2.2414 · σ / √N.

- H1 (60 min, N ≈ 200 after resolution): MDE ≈ 19 bps — a 30 bps effect is detectable
- H2 (6 h, N ≈ 200): MDE ≈ 48 bps — a 30 bps effect is NOT detectable; only ≥ ~48 bps can reach
  FORWARD_SEAL; a smaller true effect ends INDECIDABLE by construction
- H3 (60 min, N ≈ 60): MDE ≈ 35 bps — marginal
- H4 (15 min, N ≈ 500): MDE ≈ 6 bps — well powered

## Promotion criteria (all required, at the primary horizon)

- gross edge ≥ 30 bps (mean, hypothesized direction)
- gross edge ≥ 3 × cost (54 bps perp, 84 bps spot, event-weighted)
- t ≥ threshold_t(4) = 2.2414, cluster-robust by publication day
- not concentrated: N_eff ≥ 30 and top-1 event ≤ 10 % of the positive contributions
- executable: tradable share ≥ 80 % (entry 60–180 s after publication on an existing market)
- clear path after publication: pre-publication drift (mean side × pre_excess) < gross
- placebo (−48 h) mean < gross

→ `FORWARD_SEAL_REQUIRED`. Nothing is promoted from a first look; a forward window is sealed.

## Rejection criteria

- gross < 30 bps → `REJECTED_NO_GROSS`
- 30 ≤ gross < 3 × cost → `REJECTED_COST_WALL`
- passes gross and cost but fails t, concentration, tradability, leak or placebo → `INDECIDABLE`
- effect that depends on events without publication timestamp: impossible by selection
- ambiguous mapping: excluded before measurement, counted in the result

## Verdicts

Exactly one per hypothesis, written once by the harness into
`mechanisms/<mechanism_id>/results/verdict.json` and `mechanisms/event_reaction_v1/results/verdict.md`:
`REJECTED_NO_GROSS`, `REJECTED_COST_WALL`, `INDECIDABLE`, `FORWARD_SEAL_REQUIRED`.

## Execution discipline

1. This file and the FREEZE are committed and pushed (branch `p3-event-first-look-prereg`).
2. The look runs on branch `p3-event-first-look` (pushed, so the prereg has an upstream witness):
   `python mechanisms/event_reaction_v1/first_look.py --run`
3. The harness refuses to run if: the FREEZE is missing, the frozen snapshot's sha256 differs,
   any pinned file changed, the prereg is not pushed, or a result already exists.
4. Before the first price is fetched it appends the LOOK_LEDGER entry (hash-chained, with the
   witness) and debits 4 tests from the loop budget (6 → 2).
5. Positive control (synthetic prices, 80 bps injected): recovered 77.5–79.9 bps on H1–H4,
   nulls all `REJECTED_NO_GROSS` — `mechanisms/event_reaction_v1/results/positive_control.json`.
6. No optimization. No second look. A bug found during the look is fixed, recorded, and the
   look is re-sealed with a new FREEZE and a new ledger entry — never silently re-run.
