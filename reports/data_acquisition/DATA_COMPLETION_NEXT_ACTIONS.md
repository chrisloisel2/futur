# DATA COMPLETION — next actions (P5, 2026-09-11)

State: 0 validated sleeves, capital deployable false, budget 0. H2 INDECIDABLE, H3 forward-sealed, forced-flow rejected.
P4 tape on disk: 897 USDS-M + 3698 spot symbols watched,
16 triggered windows (11 final, mean completeness 0.8635), 13 captures fired, heartbeat RSS 564 MB,
1741.4 MB on disk, 43.0 GB free. Health sheet: `CURRENT_COLLECTION_HEALTH.md`.

## 1. Complete for free (in this order)

| action | what it fills | events lifted | effort |
|---|---|---|---|
| **Binance Vision backfill of the 174 H2 windows** (`data_lake/collectors/binance_public_backfill.py`, to build): aggTrades, markPriceKlines, indexPriceKlines / premiumIndexKlines, metrics (OI), fundingRate, bookDepth; launch − 30 min → + 6 h; one manifest per window with sha256 | first trade, trades, first mark / index / OI, funding, first book, L2 1-min, capacity | 174 (+47 pts each; 166 get depth) | 1 day |
| **Announcement body archive + parser** (`announcement_body_archive.py`, `announcement_body_parser.py`): title, body_html, body_text, publication_ts, update_ts, symbols, announced trading_start_ts, delisting_ts, suspension_ts, raw_hash | body, announced trading time (H2); delisting minute, borrow suspension (H3) | 174 H2 + 56 H3 (+15 pts) | 0.5 day |
| **Cross-venue lifecycle** (Gate / MEXC / KuCoin / Bitget official listing pages; `fapi/v1/constituents` for the index composition) | other-venue precedence for the 84 events with no trace | 84 (+10 pts) | 1 day |
| **P4 fixes shipped in this branch**: `KillMode=process` (a watch restart no longer kills running captures — BEAT was lost that way), URL-encoded symbols (牛来USDT lost OI and depth), trigger journal de-duplicated (12 368 identical refusals in 11 h), readiness written outside the repo | tape hygiene | — | done |

## 2. Obtain through a read-only account key

`scripts/fetch_account_fees.py` and `scripts/fetch_account_execution.py` exist since P1.1 / P2 and are inert without a key.
With a key that has **no trading permission**: actual commission (`fapi/v1/commissionRate`), leverage brackets, margin borrow
rates, historical fills (there are none) — the actual-fee column for all 174 events and the exact VIP0 wall. Nothing is sent to the exchange.

## 3. Ask a paid provider — only after 1 and 2

`H2_PROVIDER_REQUEST_WINDOWS.csv`: 174 windows of 6.5 h (launch − 30 min → + 6 h). **P0 (needs paid data): 9 windows, 58.5 hours** —
LUNA2USDT, BANKUSDT, DEEPUSDT, MEMEFIUSDT, DOLOUSDT, SXTUSDT, B2USDT, ZKJUSDT, SPCXUSDT — where Vision has no `bookDepth` on the launch day (8) or no index / premium klines (SPCX, SXT).
P1 (optional tick-level upgrade of the 1-min Vision depth): the other 165. Send the P0 rows only; a 30 €/month budget buys 58 hours of
targeted history, not a subscription.

## 4. Wait for live forward (P4 keeps running)

`PENDING_TRADING → TRADING` timestamps, first order book, first trade, first mark / index / OI of every future launch and delisting.
No capture of a real `new_perp_listing` has happened yet (0 fired); the 13 captures so far are
liquidation bursts and funding extremes. Watch: `tail -f data_lake/market_state/watch.log`, `tail -f data_lake/market_state/triggers/triggers.jsonl`,
`find data_lake/market_state/windows -name manifest.json`. A capture without the five first_* timestamps and sha256 is debug material.

## 5. Ignore

More candles, technical features, ML, funding-only, long/short-only, sentiment, generic news, the public liquidation message as a signal,
liquidation heatmaps, "complete" liquidation feeds, sub-second private feeds, DEX pre-listing prices, and an H2 bot now.

## Decision table

| category | events | decision |
|---|---|---|
| H2 events ≥ 90 after free backfill | 85 (87 with the key) | usable for a future preregistration, once the backfill exists and budget > 0 |
| 70–89 after free backfill | 84 | light backfill: cross-venue precedence is the missing piece |
| 40–69 after free backfill | 5 | need the provider (no Vision depth) |
| < 40 after free backfill | 0 | — |
| dominant missing fields now | trades, mark / index, OI, funding, depth, body (174 each) | next module = `binance_public_backfill` |
| paid windows required | 9 P0 | CSV ready for a quote |

The principal hole is **the free backfill that was never built**, then the announcement body, then execution reality. Not L2 to buy, not cross-venue, not live-only.
