# Addendum — calendar basis, declustered to true independent episodes

Follow-up to the main W3 report's calendar-basis finding, requested specifically because the
original n=111-114 was raw daily prints, not independent experiments (a multi-week extreme-basis
regime generates one row per day it persists). This addendum reruns the same BTC/ETH,
2021-2026, train-fit-on-first-60%/test-on-rest design, but takes **one entry per
regime-contiguous episode** (a new episode starts only when the regime flips CHEAP↔RICH↔NEUTRAL
or a calendar-day gap appears), holding from the episode's first day for a fixed k-day window.
Full episode-level ledger: `a9_calendar_basis_episodes_declustered.csv`.

## Result: the original headline overstated both the edge and its reliability

| horizon | n episodes | gross mean | net mean (15bps cost) | t-stat | p | worst episode | best episode | win rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| k1d | 91 | +7.8bps | -7.2bps | 2.27 | 0.026 | -82.3bps | +133.1bps | 63% |
| k3d | 92 | +8.8bps | -6.2bps | 1.16 | 0.251 | -537.7bps | +167.2bps | 71% |
| k7d | 93 | +10.0bps | -5.0bps | 0.80 | 0.424 | **-572.0bps** | +293.7bps | 67% |
| k14d | 93 | +34.5bps | +19.5bps | 2.33 | 0.022 | **-703.6bps** | +328.5bps | 73% |

Per-symbol at k7d: **BTCUSDT** n=61, mean +15.5bps, t=1.50, p=0.14 (directionally right, not
significant). **ETHUSDT** n=32, mean **-0.4bps**, t=-0.01, p=0.99 (flat — driven to zero by two
catastrophic RICH episodes in 2021-03 and 2024-03 that just kept getting richer instead of
converging: -570.5bps and -572.0bps).

This is a materially different, and much weaker, picture than the original report's "+360 to
+680bps/episode, t=10.4-13.1, dominates costs by 20-50x." That number was real arithmetic on
real data, but it was measuring **within-episode autocorrelation**, not edge: a 28-day RICH
stretch (e.g. `BTCUSDT_446`, 2022-08-18, 24 days) contributed ~24 near-identical, highly
correlated "observations" to the original n=111, all pointing the same direction because they're
the same trade re-measured on consecutive days, not 24 independent confirmations. Declustered to
one observation per episode, n drops from 111-114 to 91-93, but more importantly the standard
deviation explodes (72-143bps) because the tail risk that was averaged away across correlated
sub-samples now shows up honestly: a RICH regime that doesn't converge and instead runs further
into the tail before the position is closed is a large loss, not a small one, and there are
several of these on record (2021-02, 2021-03, 2024-03 for both symbols — all clustered around
the 2021 bull blow-off and the 2024 pre-halving run, i.e. **exactly the environment where
"rich gets richer" the longest**, which is a real, identifiable failure mode, not noise).

## Revised verdict: WEAK / high-tail-risk, not PROMISING

- k7d and k3d are not statistically distinguishable from zero once properly declustered.
- k1d and k14d are nominally significant (p<0.05) but k1d flips net-negative after a realistic
  15bps round-trip cost, and k14d's net edge (+19.5bps) sits next to a -703.6bps worst episode —
  a risk profile that needs explicit tail-hedging or position-sizing discipline before it's
  investable, not a "beats costs by 20-50x" free lunch.
- BTC alone is directionally the more credible leg (positive mean, positive t, though not
  significant at n=61); ETH does not currently show a usable edge at all once properly counted.
- The mechanism (thin arb capital in dated futures allowing basis dislocation) is still
  plausible and not ruled out — what's ruled out is the specific claim that it's already a
  large, reliable, low-risk edge. It would need a larger episode count (more history, more
  symbols if Binance lists more USDT-M quarterlies) and/or a smarter exit (e.g. stop-out on
  continued richening rather than a fixed k-day timeout) before it's a real candidate.

## Execution-realism request: hard data wall, not answered

The request to reconstruct each episode with bid/ask (perp and quarterly), depth, realizable
position size, and slippage **cannot be done with any data that exists in this repo, and
this specific data does not exist anywhere retrievable for the historical window needed**:

- `data/derivatives_backfill/binance_vision_quarterly/*.parquet` contains exactly two
  columns of substance: `open_time` (ms) and `close`. `scripts/backfill_binance_quarterly_vision.py`
  line 63 explicitly discards open/high/low/volume/trade-count from Binance's own daily-kline
  zips before ever writing to disk — so even upgrading to full OHLCV is a re-fetch, not a local
  fix, and would still only give daily (or, if re-fetched at finer interval, hourly/minute)
  **klines** — never bid/ask or book depth.
- Binance's historical public data archive (Binance Vision) does not publish historical L2
  order-book snapshots for any product, at any granularity, ever — it publishes trades and
  klines only. Real bid/ask/depth history for dated quarterly futures does not exist as a
  downloadable archive from any source used in this repo.
- The only live L2 capture in this repo (`market_physics_v3/raw/book_events`) covers spot/perp
  BTC/ETH/SOL for a 2-week window in August 2026 — it does not cover quarterly futures at all,
  and even if it did, 2 weeks can't reconstruct fills for episodes dated back to 2021.

**What's actually achievable, not attempted here (flagging as a possible next step, not done
without checking first — it requires new network calls against Binance's public archive, which
is a different kind of action than the read-only analysis in this hunt so far):** re-backfilling
1h (or 1m) OHLCV klines for BTC/ETH quarterly contracts and the matching perp leg would let entry
precision move from daily-close to intraday-bar, and would give a real (if still spread-free)
volume series to sanity-check size. It would **not** produce real bid/ask or depth under any
circumstance — those don't exist historically. Given the revised, much less clearly-profitable
result above, it's worth deciding whether that additional precision is worth pursuing before
spending more effort here.
