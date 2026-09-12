# ANNOUNCEMENT TIME EXTRACTION — coverage (2026-09-11T22:59:43 UTC)

How often each timestamp is actually stated in the text, and how it was attributed. A date is assigned to the clause that names it (nearest keyword, or a keyword on the same line); a date with no matching clause is left unused, and a field with nothing in the text stays null.

| field | H2 | H3 |
|---|---|---|
| body archived | 174 / 174 | 56 / 56 |
| trading_start_ts | 174 | 44 |
| delisting_ts | 0 | 56 |
| suspension_ts | 0 | 23 |
| explicit clock time on the start | 173 | — |

Fetch errors: H2 none, H3 none.

## Events that still need a human look

8 H2 launches where the announced time and the first traded bar differ by more than 15 minutes (the contract opened later than announced, or the body names another contract's time). They are usable only once someone decides which timestamp is the event.

| symbol | announced | first bar | gap (min) |
|---|---|---|---|
| DOLOUSDT | 2025-04-30T12:30 | 2025-05-01T03:00 | +870 |
| GPROUSDT | 2026-09-03T00:00 | 2026-09-03T13:45 | +825 |
| LUNA2USDT | 2022-09-10T00:00 | 2022-09-10T03:00 | +180 |
| ARKUSDT | 2023-09-19T14:30 | 2023-09-19T16:03 | +93 |
| SPXUSDT | 2024-12-10T12:00 | 2024-12-10T12:45 | +45 |
| SWARMSUSDT | 2025-01-07T11:30 | 2025-01-07T12:15 | +45 |
| SWELLUSDT | 2024-11-08T15:30 | 2024-11-08T16:00 | +30 |
| KMNOUSDT | 2024-12-20T18:30 | 2024-12-20T19:00 | +30 |
