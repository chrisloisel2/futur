"""
src/institutional/engines/cross_sectional_momentum_live/
─────────────────────────────────────────────────────────────────────────────
CROSS_SECTIONAL_MOMENTUM_LIVE_V1 — Live Alpha Lab, cross-sectional family.

Lightweight LIVE reconstruction of reports/edge_discovery/alpha_hunt_2026-08-30/
w1_cross_sectional/REPORT.md's #1 finding (M1: cross-sectional 7d->7d raw
momentum, long-only, quintile). NOT the same alpha as
CROSS_SECTIONAL_MOMENTUM_PIT_V1 (configs/live_alpha_registry.yaml, still
DATA_BLOCKED, untouched) -- that entry is the true original spec (PIT
312-symbol universe, data_v2/normalized). This module deliberately uses a
much smaller, live-only data path: direct Binance USDM futures daily klines
(price + real quote-volume) polled on demand, no L2, no new collector daemon,
no data_v2 dependency. See reports/live_alpha_lab/CROSS_SECTIONAL_MOMENTUM_LIVE_V1/
freeze_spec.json for the full honesty accounting of every deviation from the
source report.

- `klines_source.py` — I/O: incremental REST top-up of a small local daily
  OHLCV+volume cache per symbol.
- `signal.py` — pure functions (no I/O): causal trailing return, causal
  trailing liquidity, cross-sectional top-bucket selection, weekly rebalance
  orchestration.
"""
