# Alpha Foundry V4

This branch introduces the architecture and first integrated data plane for a multi-mechanism alpha research factory.

Quick checks:

```bash
python3 -m pytest \
  tests/unit/test_alpha_foundry_v4.py \
  tests/unit/test_alpha_foundry_trade_tape_v4.py \
  -v

python3 scripts/alpha_foundry_v4_manifest.py
```

Build the causal Trade Tape V4 from the latest simultaneous Market Physics health window:

```bash
python3 scripts/build_alpha_foundry_trade_tape_v4.py \
  --root data/market_physics_v3 \
  --health-dir reports/market_physics_v3/health \
  --venues binance,bybit,okx,hyperliquid \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --cadence-ms 100
```

The Trade Tape keeps venue granularity explicit and produces both clock-time windows (100ms, 500ms, 2s, 10s, 60s) and event-count windows (last 10/50/250 trades), with signed flow, CVD, acceleration, jerk, entropy, arrival statistics, impact and absorption.

The full research contract is documented in `reports/ALPHA_FOUNDRY_V4_PROTOCOL.md`.

The currently running Phase 5.2 confirmation remains isolated and must not be reused for V4 discovery before its locked verdict is sealed.
