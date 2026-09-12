# MEXC PRE-BINANCE TAPE — coverage (2026-09-12T14:57:00 UTC)

MEXC was the first dated venue for most H2 assets. This is the MEXC market before Binance opened its perpetual: daily candles over 30 days, hourly over 3 days, 5-minute over 6 hours, all ending at t0 exclusive. The features are descriptive of the pre-Binance state; they are not a signal and produce no verdict.

## The six answers

1. MEXC-first events: **113** (spot 49, perp 64).
2. With a retrievable pre-Binance tape: **112** (full daily + hourly 110, daily only 2, partial 0).
3. Blocked by the API / source: **1** (symbol renamed, delisted from MEXC, or no history that far back).
4. Missing windows: the 5-minute window (t0 − 6 h) is empty for 65 events — MEXC does not serve 5-minute history far back; the hourly window covers the last 3 days for every collected event.
5. Features buildable free, count of events with a value: {'pre_binance_return_30d': 95, 'pre_binance_return_14d': 95, 'pre_binance_return_7d': 95, 'pre_binance_return_3d': 95, 'pre_binance_return_24h': 105, 'pre_binance_volatility_7d': 73, 'pre_binance_volume_7d': 73, 'pre_binance_volume_24h': 110, 'pre_binance_range_7d': 73, 'pre_binance_max_drawdown_7d': 73, 'pre_binance_pump_score': 73, 'pre_binance_exhaustion_score': 73, 'pre_binance_liquidity_proxy': 73}.
6. External provider: **not needed** for the descriptive tape. If a future preregistration needs 5-minute MEXC history for old launches, the targeted list is the `not_collected` + missing-5m events above — a per-window request, never a subscription.

## Descriptive distribution (not a result)

- `pre_binance_return_7d`: n 95, p25 -0.008052, median 0.2522, p75 0.8224
- `pre_binance_return_24h`: n 105, p25 0.000613, median 0.1926, p75 0.5072
- `pre_binance_volatility_7d`: n 73, p25 0.08984, median 0.1491, p75 0.2419
- `pre_binance_pump_score`: n 73, p25 0.071, median 0.7845, p75 1.9
- `pre_binance_exhaustion_score`: n 73, p25 0.1379, median 0.1902, p75 0.3212
- `listing_age_days_at_binance_open`: n 113, p25 1.3, median 11.02, p75 96.25

These describe MEXC before Binance. They are joined to nothing after t0, and this branch computes nothing after t0.

