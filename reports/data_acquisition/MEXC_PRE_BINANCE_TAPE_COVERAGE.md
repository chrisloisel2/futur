# MEXC PRE-BINANCE TAPE — coverage (2026-09-12T17:22:08 UTC)

MEXC was the first dated venue for most H2 assets. This is the MEXC market before Binance opened its perpetual: daily candles over 30 days, hourly over 3 days, 5-minute over 6 hours, all ending at t0 exclusive. The features are descriptive of the pre-Binance state; they are not a signal and produce no verdict.

## The six answers

1. MEXC-first events: **113** (spot 49, perp 64).
2. With a retrievable pre-Binance tape: **105** (full daily + hourly 95, daily only 0, partial 10).
3. Blocked by the API / source: **8** (symbol renamed, delisted from MEXC, or no history that far back).
4. Missing windows: the 5-minute window (t0 − 6 h) is empty for 60 events — MEXC does not serve 5-minute history far back; the hourly window covers the last 3 days for every collected event.
5. Features buildable free, count of events with a value: {'pre_binance_return_30d': 73, 'pre_binance_return_14d': 73, 'pre_binance_return_7d': 73, 'pre_binance_return_3d': 73, 'pre_binance_return_24h': 103, 'pre_binance_volatility_7d': 69, 'pre_binance_volume_7d': 69, 'pre_binance_volume_24h': 105, 'pre_binance_range_7d': 69, 'pre_binance_max_drawdown_7d': 69, 'pre_binance_pump_score': 69, 'pre_binance_exhaustion_score': 69, 'pre_binance_liquidity_proxy': 69}.
6. External provider: **not needed** for the descriptive tape. If a future preregistration needs 5-minute MEXC history for old launches, the targeted list is the `not_collected` + missing-5m events above — a per-window request, never a subscription.

## Descriptive distribution (not a result)

- `pre_binance_return_7d`: n 73, p25 -0.05403, median 0.09185, p75 0.5292
- `pre_binance_return_24h`: n 103, p25 0.01375, median 0.1884, p75 0.4417
- `pre_binance_volatility_7d`: n 69, p25 0.05669, median 0.09402, p75 0.1722
- `pre_binance_pump_score`: n 69, p25 -0.3002, median 0.5856, p75 1.526
- `pre_binance_exhaustion_score`: n 69, p25 0.0953, median 0.1488, p75 0.2357
- `listing_age_days_at_binance_open`: n 113, p25 1.3, median 11.02, p75 96.25

These describe MEXC before Binance. They are joined to nothing after t0, and this branch computes nothing after t0.

