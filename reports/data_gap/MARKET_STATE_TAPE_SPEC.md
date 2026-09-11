# MARKET_STATE_TAPE — specification (P4, 2026-09-11)

Purpose: capture the complete microstructural state of a market at the moments where an edge can be born —
birth, death, status change, appearance of shortability / liquidity, liquidation stress. One central collector,
two levels. No signal, no verdict, no order, no join to any past result, no budget consumed.

## Modules

| module | role |
|---|---|
| `data_lake/collectors/market_state_schema.py` | mandatory schemas, `validate`, `raw_hash`, append-only writer (jsonl / jsonl.gz, concatenated members readable by `tape_io`), completeness |
| `data_lake/collectors/exchange_info_diff.py` | photographs `fapi/v1/exchangeInfo` (USDS-M) and `api/v3/exchangeInfo` (spot), normalises what is watched, archives a snapshot only when the view's hash changes, logs atomic changes |
| `data_lake/collectors/symbol_lifecycle.py` | persistent per-symbol state; changes → `symbol_lifecycle_event` (born, status_change, onboard_date_set/change, filters_change, shortability_change, died) and capture triggers |
| `data_lake/collectors/microstructure_window.py` | triggered heavy capture of one market: two WebSocket connections + REST depth/OI, 1-second `market_state_snapshot`s, hashed `triggered_window_manifest` |
| `data_lake/collectors/market_state_tape.py` | CLI: `lightweight_watch`, `triggered_microstructure_capture`, `readiness`; trigger dispatcher (dedup, cooldown, bounded concurrency, journal) |

## Mode 1 — `lightweight_watch` (24/7)

```
python -m data_lake.collectors.market_state_tape --mode lightweight_watch
```

| loop | source | default cadence | REST weight | output |
|---|---|---|---|---|
| exchangeInfo USDS-M | `fapi/v1/exchangeInfo` | 5 s (`--exchange-info-interval`) | 1 | `exchange_info/venue=binance_um/{snapshot_*.json, changes.jsonl, last_view.json}` + lifecycle events |
| exchangeInfo spot | `api/v3/exchangeInfo` | 60 s | 20 | same for `venue=binance_spot` (includes `isMarginTradingAllowed`: shortability) |
| mark / index / funding | `fapi/v1/premiumIndex` (all symbols, one call) | 60 s (`--state-interval`) | 1 | `market_state_snapshot` rows, source `watch_premium_index` (gz) |
| open interest | `fapi/v1/openInterest` per TRADING perp | one pass per 300 s (`--oi-interval`) | 1 × ~900 | rows source `watch_open_interest`; OI spike detection between passes |
| 24 h ticker | `fapi/v1/ticker/24hr` | 300 s | 40 | `ticker_24h` rows (quote volume, count) |
| liquidation bursts | WS `!forceOrder@arr` | continuous | 0 | burst counter (10 s window) → trigger only; the message itself is never a signal |

Watched fields (USDS-M): status, onboardDate, deliveryDate, contractType, marginAsset, quoteAsset, baseAsset,
permissionSets, liquidationFee, maintMarginPercent, marketTakeBound, maxMoveOrderLimit, underlyingType, filters
(tickSize, minQty, stepSize, notional, maxQty). Spot: status, permissions, isMarginTradingAllowed, isSpotTradingAllowed, filters.
Leverage brackets require an API key: collected only when a read-only key is configured (not in this branch).

Every record carries `ts_local` (or `detected_at_local`), `ts_exchange` when the API provides it, `source`,
`raw_hash` of the raw payload. Note: `fapi` exchangeInfo `serverTime` is served stale (hours) by the CDN; it is
recorded as `exchange_ts` but the detection reference is the local receive time.

REST budget of the watch: ≈ 220 weight/min (limit 2 400), paced by a 60-s token bucket (`rest_budget_per_min` 600).

## Mode 2 — `triggered_microstructure_capture`

```
python -m data_lake.collectors.market_state_tape --mode triggered_microstructure_capture --symbol SOMEUSDT --trigger-type new_perp_listing [--t0 ISO] [--pre-window 1800] [--post-window 21600]
```

| stream | endpoint | cadence | content |
|---|---|---|---|
| `<sym>@bookTicker` | `wss://fstream.binance.com/public/ws` | tick | best bid / ask + sizes, event ts |
| `<sym>@depth20@100ms` | `wss://fstream.binance.com/public/ws` | 100 ms | 20-level L2 snapshots |
| `<sym>@aggTrade` | `wss://fstream.binance.com/market/ws` | tick | trades tick-by-tick |
| `<sym>@markPrice@1s` | `wss://fstream.binance.com/market/ws` | 1 s | mark, index, funding, next funding time |
| REST `depth?limit=1000` | `fapi/v1/depth` | 5 s (weight 20) | 1 000-level book for the 10 / 25 / 50 bps bands |
| REST `openInterest` | `fapi/v1/openInterest` | 5 s (weight 1) | OI |

(`/market/ws` does not serve per-symbol bookTicker / depth on this host; `/public/ws` does — measured 2026-09-11.)

Outputs, append-only, under `data_lake/market_state/windows/<trigger_id>/`: raw streams (`raw_<stream>/…jsonl.gz`),
`market_state_snapshot/…/snapshots.jsonl` (one row per second), `manifest.json` (rewritten every 60 s and at the end,
final version carries sha256 of every file), `capture.log`.

Snapshot row: venue, symbol, ts_exchange (latest event ts among streams), ts_local, source, bid, ask, mid, spread_bps,
depth_{10,25,50}bps_{bid,ask}_usd, imbalance_10bps, mark_price, index_price, funding_rate, open_interest, volume_1m (USD),
trade_count_1m, latency_ms (median receive − event over the last 200 messages), raw_hash; plus depth_source (rest /
ws_depth20), depth_covered_bps (the widest band the book actually covers — bands beyond it are null, never guessed),
book_levels, trigger_id.

First timestamps recorded in the manifest: first_orderbook_ts, first_bookticker_ts, first_trade_ts, first_mark_ts,
first_index_ts, first_oi_ts. Windows: `pre_window` 30 min when the trigger is anticipated (t0 known and in the future:
onboardDate, PENDING_TRADING), `post_window` 6 h; if the capture starts at or after t0, `pre_window_missing = true`.
A symbol that does not exist yet is re-subscribed every 30 s until its first message.

## Triggers

| trigger_type | source | anticipated | cooldown |
|---|---|---|---|
| new_perp_listing | symbol added (perp) / status → TRADING | yes if PENDING_TRADING or onboardDate in the future | 6 h |
| onboard_date_change | onboardDate set or changed | yes | 6 h |
| status_change | any other status transition (perp) | no | 6 h |
| delisting_detected | status → SETTLING / CLOSE / DELIVERING… or symbol removed | no | 6 h |
| oi_spike | OI +10 % between two passes (≈ 5 min) | no | 6 h |
| funding_extreme | \|lastFundingRate\| ≥ 0.3 % per 8 h | no | 8 h |
| liquidation_burst | ≥ 5 forceOrder messages for one symbol in 10 s, or ≥ 100 in total | no | 1 h |
| manual | CLI | as given | 0 |

Dispatcher: one active capture per symbol, at most 4 concurrent captures, cooldown per (venue, symbol, type), spot
symbols never captured (no per-symbol futures stream; their lifecycle and shortability are still recorded). Every
trigger is journaled in `triggers/triggers.jsonl` with the decision (fired or why not). Captures run as detached
subprocesses so a watch restart does not kill them.

## Schemas (mandatory fields, validated on write)

- `symbol_lifecycle_event`: venue, symbol, base_asset, quote_asset, market_type, old_status, new_status,
  detected_at_local, exchange_ts, onboard_date, first_seen_at, raw_hash, source (+ event_kind, detail).
- `market_state_snapshot`: venue, symbol, ts_exchange, ts_local, source, bid, ask, mid, spread_bps,
  depth_10bps_bid_usd, depth_10bps_ask_usd, depth_25bps_bid_usd, depth_25bps_ask_usd, depth_50bps_bid_usd,
  depth_50bps_ask_usd, imbalance_10bps, mark_price, index_price, funding_rate, open_interest, volume_1m,
  trade_count_1m, latency_ms, raw_hash.
- `triggered_window_manifest`: trigger_id, trigger_type, venue, symbol, start_ts, end_ts, reason, prereg_link,
  files_written, row_counts, sha256, completeness_score, missing_fields, no_alpha_test (= true, enforced).

## Storage layout

```
data_lake/market_state/
  exchange_info/venue=<v>/snapshot_<ts>_<hash>.json, changes.jsonl, last_view.json
  lifecycle/state.json
  symbol_lifecycle_event/venue=<v>/date=<d>/symbol_lifecycle_event.jsonl
  market_state_snapshot/venue=binance/date=<d>/watch_premium_index.jsonl.gz, watch_open_interest.jsonl.gz
  ticker_24h/venue=binance/date=<d>/watch_ticker_24h.jsonl.gz
  triggers/triggers.jsonl
  windows/<trigger_id>/{raw_*/…, market_state_snapshot/…, manifest.json, capture.log}
  watch_state.json, watch.log
```
`data_lake/market_state/` is gitignored (data), the code and this spec are versioned. Sizes: watch ≈ 25 MB/day
(gz); a 6-h capture on a liquid symbol ≈ 100-300 MB raw; on a new listing ≈ 20-60 MB.

## Deployment

`deploy/systemd/futur-market-state-tape.service` (the watch, Restart=always) and
`futur-market-state-tape.timer` → `futur-market-state-readiness.service` (readiness report every 30 min:
`reports/data_gap/MARKET_STATE_TAPE_READINESS.{md,json}`).

## What it is not

No signal, no verdict, no order, no join to `mechanisms/*/results`, no budget credit (no new independent episodes),
no live_lab file touched. A future preregistration may consume this tape only with budget > 0.

## Validation on this host (2026-09-10 22:44-22:53 UTC, BTCUSDT, manual triggers)

| run | what it showed | fix |
|---|---|---|
| v1 (90 s, single WS on `/market/ws`, REST depth 100) | aggTrade / markPrice / OI / REST depth flow; bookTicker and depth20 silent (0 msg); all depth bands null (100 levels ≈ 1 bp on BTC) | per-symbol bookTicker / depth20 moved to `/public/ws`; REST depth 1 000 levels / 5 s |
| v2 (60 s) | bookTicker 9 694 msgs, depth20 564, aggTrade 300, markPrice 60; bands still null in 33/60 snapshots (freshness rule fell back to the 20-level WS book) | REST book preferred whenever < 10 s old |
| v3 (40 s) | depth_source rest 40/40, 10-bps band covered 40/40 (bid 23.3 M$, ask 17.8 M$, imbalance +0.13), spread 0.013 bps, median latency 110 ms, completeness 0.79; 25 / 50 bps bands null on BTC because 1 000 levels stop inside 25 bps (`depth_covered_bps` = 10) | none: the band is reported as not covered, never guessed |
| watch (45 s, `--no-capture`) | exchangeInfo UM 8 polls / spot 1, premiumIndex 900 symbols → 900 light snapshots, 175 OI polls, 1 ticker pass, 14 forceOrder messages, 4 funding_extreme triggers journaled (not fired: no-capture mode) | none |

Three manual dry-run windows remain on disk under `data_lake/market_state/windows/`; they are not analysis, they are the proof the pipeline writes what the schema says.

## First six hours of the live watch (2026-09-10 22:54 → 2026-09-11 05:05 UTC)

- The trigger → capture loop ran unattended: four `liquidation_burst` captures completed their 6-hour window
  (PUMP 90 MB, ZEC 162 MB, ETH 176 MB, BTC 162 MB; 21 5xx one-second snapshots each; completeness 0.79–1.00;
  median latency 111 ms; 3–4 WebSocket reconnects each, all logged in the manifest), a fifth (BEAT) started at 05:04.
- Corrections applied after this run: `MARKET_LOT_SIZE.maxQty` removed from the watched spot filters (Binance
  recomputes it continuously: 9 148 `filters_change` events in six hours were noise, kept on disk as append-only
  history but no longer produced); `--no-capture` dry runs no longer set cooldowns; burst cooldown 6 h; daily caps
  per trigger type (6 bursts / 6 OI spikes / 6 funding extremes / 12 status changes; births and delistings
  unlimited); BTC / ETH / SOL excluded from stress captures (already recorded 24/7 by `microstructure_reduced`);
  heartbeat line with RSS every 5 minutes in `watch.log` (RSS had grown from 201 MB to 569 MB without any log line).
