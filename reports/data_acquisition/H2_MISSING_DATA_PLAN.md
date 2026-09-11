# H2 — missing data plan (P5, 2026-09-11)

Source: `H2_LAUNCH_COVERAGE_MATRIX.csv` / `.json` (174 perp-first Binance launches of `event_listing_perp_fade_v1`, frozen universe),
Vision existence probed by HEAD (1 218 keys, 0 network errors, cache `_vision_probe_cache.json`). No price read, no test.

## Where the 174 events stand

| state | score | events | meaning |
|---|---|---|---|
| now (in hand) | min 20, median 30, max 30 | **174 unusable** | we hold the announcement second, the launch minute, `onboardDate` and the spot/other-venue facts — nothing else |
| after the free backfill (Vision + announcement body) | min 66, median 87, max 92 | **85 clean, 84 near-usable, 5 partial** | 169 / 174 at 70 or more without paying anyone |
| after the free backfill + read-only account key | max 95 | **87 clean** | the actual fee is the last 3 points a key can give |
| what no backfill gives | 5 points | all | `PENDING_TRADING` timestamps exist only forward (P4 records them for future launches) |

Score distribution after the free backfill: {66: 5, 76: 3, 77: 2, 82: 77, 87: 2, 92: 85}.
The two plateaus are the cross-venue precedence: 90 events have an OKX / Bybit listing before the Binance launch in our tape
(+10), 84 have no trace (Gate / MEXC / KuCoin / Bitget / DEX not covered).

## Missing fields, by what fills them

| gap | events | fill | cost | points |
|---|---|---|---|---|
| first trade, trades t0 → t+6h (`aggTrades`) | 174 | Vision daily aggTrades (174 / 174 exist on the launch day) | free, ~1 day of code | 11 |
| first mark / first index (`markPriceKlines`, `indexPriceKlines` or `premiumIndexKlines`) | 174 | Vision (mark 174, index-or-premium 173 / 174) | free | 10 |
| first OI (`metrics` 5-min) | 174 | Vision (174 / 174) | free | 5 |
| funding at first settlement | 174 | Vision monthly `fundingRate` (171 / 174; the 3 missing are launches too recent for the monthly file) | free | 5 |
| first order book, L2 1-min t0 → t+6h, capacity | 174 | Vision `bookDepth` (166 / 174 on the launch day) | free | 16 |
| announcement body + announced trading time | 174 | scrape the CMS article body (the tape stores a hash only) | free, ~0.5 day | 15 |
| actual fee | 174 | read-only key: `fapi/v1/commissionRate` (script exists since P1.1) | free, needs a key | 3 |
| other-venue precedence | 84 | Gate / MEXC / KuCoin / Bitget official listing pages + `fapi/v1/constituents` | free, ~1 day | 10 |
| L2 for the 8 launches without Vision `bookDepth`, index for SPCX / SXT | 9 | paid provider, targeted windows (`H2_PROVIDER_REQUEST_WINDOWS.csv`, P0 rows: 9 × 6.5 h) | paid, small | up to 13 |
| tick-level L2 for the other 165 | optional | paid provider (P1 rows) | paid | 0 (no score change: 1-min depth already counts) |
| `PENDING_TRADING` timestamp | 174 | live only: P4 lifecycle events for future launches | — | 5 |

## The answer to "how many H2 events are cleanly testable?"

- **Today: 0.** Every event is judged against BTC with declared costs because the dataset holds no reference price, no book, no OI, no funding.
- **After the free work: 85 clean and 84 near-usable (169 / 174).** The paid provider changes at most 9 events. The gap is not historical L2 to buy; it is the free backfill nobody built.
- The remaining plateau at 82 is cross-venue precedence (84 events): a collector problem, not a purchase.

## Order of work (each step raises every event's score; none is a test)

1. `binance_public_backfill` — Vision aggTrades, markPrice / indexPrice / premiumIndex klines, metrics, fundingRate, bookDepth for the 174 windows (launch − 30 min → + 6 h), hashed per file: +47 points per event.
2. `announcement_body_archive` + parser — body text, announced trading time, delisting / suspension time: +15 points, and it closes I22 for H3 too.
3. read-only account key — actual commission, leverage brackets, margin rates: +3 points, and the VIP0 wall becomes measured.
4. cross-venue lifecycle collectors (Gate / MEXC / KuCoin / Bitget) + index constituents: +10 points on 84 events.
5. provider request for the 9 P0 windows only — after 1–4, if a preregistration needs them.
6. forward: P4 keeps recording `PENDING_TRADING → TRADING`, first book, first trade, first mark / index / OI for every future launch.

Nothing above is a price look. Reaching 90 on an event does not mean a test: budget is 0 and the next test needs its own preregistration.
