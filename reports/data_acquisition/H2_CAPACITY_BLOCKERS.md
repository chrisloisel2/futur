# H2 CAPACITY BLOCKERS — P11 (2026-09-12T14:45:38 UTC)

What stops a launch from being executable at a realistic size, event by event. Descriptive; no strategy claim.

| blocker | events | route |
|---|---|---|
| no `bookDepth` archive on the launch day | 11 | provider window (see `H2_PROVIDER_REQUEST_WINDOWS.csv`) or accept the event as capacity-unknown |
| archive generation without the 20-bps level | 115 | structural for pre-2026 launches: sub-1 % capacity cannot be observed free; the P4 live tape records 20 bps and tick L2 for every future launch |
| book too thin at t0 | 47 | not a data gap: the market really was empty at open; a test that enters at t0 + 15 min must use the +15 min book, which the features carry |
| spread proxy too wide at t0 | 21 | same: a fact about the first minute, re-evaluated at each window |
| no trades in the first minute (spread unknown) | 1 | the +1 / +5 min windows usually have trades |

## Paper-only launches

Measured, never `CAPACITY_OK` within the first hour at 1 000 USDT within the finest band:

| symbol | launch | resolution | t0 status | best window status | thinner side at t0 (USDT) |
|---|---|---|---|---|---|
| BLURUSDT | 2023-04-28T12:00 | 100 | DEPTH_TOO_THIN | UNKNOWN (+60 min) | 0 |
| KASUSDT | 2023-11-17T02:00 | 100 | DEPTH_TOO_THIN | UNKNOWN (+15 min) | 863 |
| ETHWUSDT | 2023-11-28T12:30 | 100 | DEPTH_TOO_THIN | UNKNOWN (+60 min) | 397 |
| WIFUSDT | 2024-01-18T14:15 | 100 | DEPTH_TOO_THIN | UNKNOWN (+30 min) | 112 |
| TONUSDT | 2024-03-01T12:31 | 100 | DEPTH_TOO_THIN | UNKNOWN (+5 min) | 0 |
| MYROUSDT | 2024-03-05T08:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+60 min) | 7369 |
| 1000000MOGUSDT | 2024-11-07T12:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+30 min) | 7058 |
| SWELLUSDT | 2024-11-08T16:00 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+5 min) | 16834 |
| 1000WHYUSDT | 2024-11-25T11:31 | 100 | DEPTH_TOO_THIN | DEPTH_TOO_THIN (+0 min) | 0 |
| KMNOUSDT | 2024-12-20T19:00 | 100 | DEPTH_TOO_THIN | UNKNOWN (+15 min) | 0 |
| SONICUSDT | 2025-01-08T07:01 | 100 | DEPTH_TOO_THIN | UNKNOWN (+60 min) | 9 |
| TRUMPUSDT | 2025-01-18T13:00 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+60 min) | 4566 |
| ATHUSDT | 2025-04-02T15:45 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+60 min) | 2842 |
| INITUSDT | 2025-04-16T06:30 | 100 | DEPTH_TOO_THIN | UNKNOWN (+5 min) | 768 |
| MERLUSDT | 2025-05-29T08:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+60 min) | 3314 |
| NEWTUSDT | 2025-06-19T14:30 | 100 | DEPTH_TOO_THIN | UNKNOWN (+30 min) | 467 |
| CROSSUSDT | 2025-07-10T10:30 | 100 | DEPTH_TOO_THIN | UNKNOWN (+60 min) | 581 |
| VELVETUSDT | 2025-07-15T09:15 | 100 | DEPTH_TOO_THIN | UNKNOWN (+15 min) | 392 |
| YALAUSDT | 2025-08-07T16:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+1 min) | 4877 |
| AIOUSDT | 2025-08-13T11:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+60 min) | 5937 |
| XPLUSDT | 2025-08-22T09:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+15 min) | 12611 |
| SOMIUSDT | 2025-08-25T09:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+60 min) | 2905 |
| LINEAUSDT | 2025-09-01T08:30 | 100 | DEPTH_TOO_THIN | UNKNOWN (+60 min) | 279 |
| 0GUSDT | 2025-09-17T15:45 | 100 | DEPTH_TOO_THIN | UNKNOWN (+15 min) | 737 |
| MONUSDT | 2025-10-10T07:17 | 100 | DEPTH_TOO_THIN | UNKNOWN (+60 min) | 674 |
| YBUSDT | 2025-10-10T14:45 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+60 min) | 1477 |
| KITEUSDT | 2025-10-29T10:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+15 min) | 5907 |
| CCUSDT | 2025-10-31T12:00 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+5 min) | 2539 |
| STABLEUSDT | 2025-11-06T12:00 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+30 min) | 2301 |
| SENTUSDT | 2025-11-14T12:45 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+30 min) | 1175 |
| LITUSDT | 2025-12-23T17:30 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+1 min) | 4339 |
| XAGUSDT | 2026-01-07T10:00 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+30 min) | 5015 |
| ZAMAUSDT | 2026-01-09T09:00 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+60 min) | 2138 |
| FOGOUSDT | 2026-01-10T14:00 | 100 | SPREAD_TOO_WIDE | UNKNOWN (+15 min) | 2551 |
| CHIPUSDT | 2026-04-16T07:15 | 20 | DEPTH_TOO_THIN | DEPTH_TOO_THIN (+30 min) | 0 |
