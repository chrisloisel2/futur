# Execution Engine (Mode Switching)

- Reads OrdersPlan, routes to maker/taker executors.
- Maker: quotes distance based on spread/depth, cancel/repost cadence, post-only.
- Taker: split orders, limit-to-market with slippage guard.
- Fill model + slippage estimator provide expected costs; adverse selection can widen/disable maker.
- Tracks ExecutionState (symbol modes, open orders, health) and emits OrderEvents, ExecutedFills, ExecutionCosts. Stores to Mongo caches and S3 parquet under artifacts/execution/.
- Resilience: rate-limit tokens, retries via execution client, reconcile open orders, respect risk tags.
