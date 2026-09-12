# H2 DEPTH CAPACITY FEATURES — P11 (2026-09-12T14:45:38 UTC)

Executable capacity around each of the 174 H2 launches, derived from the Vision archives already on disk (`bookDepth` for the book, `aggTrades` for a mid and an effective-spread proxy). No return computed, no verdict, no budget. "Measured" means the book was read and described; it says nothing about an edge.

## What the free archive can and cannot say

- `bookDepth` gives **cumulative notional at ±1 %, ±2 % … ±5 %** of the mid, one snapshot every ~30 s. Archives from 2026 add a ±0.2 % level. There is **no best bid, no best ask, no mid** in the file.
- Resolution across the 174 events: {20: 59, 100: 115} (bps; `None` = no book at all).
- Depth within 10 bps is below the resolution of every archive → always `None`. Depth within 25 / 50 bps is reported as the 20-bps band **as a lower bound**, and only where that band exists.
- Slippage for a 100 / 500 / 1 000 USDT market order: an **upper bound** (the band that contains the order) and a linear estimate assuming uniform fills inside the band — both reported, the method is named.
- The mid is the median trade price in the minute; the effective spread is (mean aggressive-buy price − mean aggressive-sell price) / mid from `is_buyer_maker`. Both are proxies and labelled as such.

## The seven answers

1. Events with a measured capacity (a readable book at t0): **160 / 174**.
2. Depth too thin at t0 (thinner side < 1000 USDT within the finest band): **47**.
3. Spread too wide at t0 (proxy > 50 bps): **21**.
4. No depth at all: **11** (no `bookDepth` archive for the launch day); bad book: 3.
5. Of the 159 events P10 called potentially clean, **11** are `CAPACITY_OK` at t0 and **45** at some window within the first hour. `UNKNOWN` at t0: 81 — the book fills a 1 000 USDT order within 1 % and the spread is fine, but the archive generation has no 20-bps level, so sub-1 % capacity is not observable.
6. Orders that fill inside the observed book at t0 (both sides): 100 USDT 157, 500 USDT 156, 1 000 USDT 155 events. Effective spread proxy at t0: median 28.368 bps (p25 14.332, p75 45.419).
7. Paper-only events (book measured, but too thin or too wide at t0 and never `CAPACITY_OK` in the first hour): **35** — BLURUSDT, KASUSDT, ETHWUSDT, WIFUSDT, TONUSDT, MYROUSDT, 1000000MOGUSDT, SWELLUSDT, 1000WHYUSDT, KMNOUSDT, SONICUSDT, TRUMPUSDT, ATHUSDT, INITUSDT, MERLUSDT ….

## Status by window (minutes after the first traded bar)

| window | CAPACITY_OK | SPREAD_TOO_WIDE | DEPTH_TOO_THIN | NO_DEPTH | BAD_BOOK | UNKNOWN |
|---|---|---|---|---|---|---|
| +0 min | 11 | 21 | 47 | 11 | 3 | 81 |
| +1 min | 23 | 4 | 34 | 10 | 0 | 103 |
| +5 min | 30 | 0 | 17 | 9 | 0 | 118 |
| +15 min | 32 | 0 | 15 | 10 | 0 | 117 |
| +30 min | 33 | 0 | 14 | 10 | 0 | 117 |
| +60 min | 43 | 1 | 6 | 9 | 0 | 115 |

`CAPACITY_OK` at the 1-hour mark: 43 events; at any window: 47.

## What this changes in the coverage matrix

`capacity_present` is now credited for the 160 measured events. Matrix after capacity: {'near_usable': 45, 'clean': 128, 'partial': 1}, scores min 66 / median 92 / max 92.

