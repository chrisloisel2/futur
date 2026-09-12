# ANNOUNCEMENT BODY ARCHIVE — P7 (2026-09-11T22:59:43 UTC)

The event tape stores only a hash of an announcement. The timestamps that decide executability — when the contract actually opens, when a delisting takes effect, when borrowing is suspended — are in the text. This phase archives the bodies and parses those times. No price join, no signal, no verdict, no budget.

| scope | announcements | bodies archived | median body | trading start | delisting | suspension |
|---|---|---|---|---|---|---|
| H2 (perp launches) | 174 | 174 | 2913 chars | **174** (173 with an explicit clock time) | 0 | 0 |
| H3 (delistings) | 56 | 56 | 4226 chars | 44 | **56** | 23 |

## Source

Binance publishes article bodies through its CMS detail endpoint as a nested node tree; the parser flattens it to text. The endpoint rate-limits past roughly 200 close requests, so the collector paces at 0.8 s and backs off 20–240 s on HTTP 429. Every archive keeps the raw payload hash, the parser version, and is never silently overwritten: a body that changes is archived beside the current one and the rewrite is logged in the append-only index.

## Cross-check: announced time against the first traded minute

For **174 of the 174** H2 launches the body states an opening time. Comparing it to the first Vision 1-minute bar (an independent source): **162 agree within 5 minutes**. Two independent records of the same event converging is the strongest evidence available that both are right.

Largest disagreements, which need a human look before any test uses them:

| symbol | announced | first traded bar | gap (min) |
|---|---|---|---|
| DOLOUSDT | 2025-04-30T12:30 | 2025-05-01T03:00 | +870 |
| GPROUSDT | 2026-09-03T00:00 | 2026-09-03T13:45 | +825 |
| LUNA2USDT | 2022-09-10T00:00 | 2022-09-10T03:00 | +180 |
| ARKUSDT | 2023-09-19T14:30 | 2023-09-19T16:03 | +93 |
| SPXUSDT | 2024-12-10T12:00 | 2024-12-10T12:45 | +45 |
| SWARMSUSDT | 2025-01-07T11:30 | 2025-01-07T12:15 | +45 |
| SWELLUSDT | 2024-11-08T15:30 | 2024-11-08T16:00 | +30 |
| KMNOUSDT | 2024-12-20T18:30 | 2024-12-20T19:00 | +30 |

## What this closes

Instrument defect I22: the tape's `trading_start_ts` is the date in the title parsed at midnight, so it preceded the publication timestamp by 6–10 hours for most listings. The announced time from the body replaces it, and the Vision first bar corroborates it.

