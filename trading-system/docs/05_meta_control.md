# Meta Control (No Performance Chasing)

Inputs: Signal(t) (confidence_calibrated, entropy, p_hit, quantiles, novelty, disagreement), State(t) (microstructure, liquidity, vol), RiskState(t), ExecutionTelemetry(t), perf/drift snapshots.

Pipeline:
- MetaScaler: scale in [0,1], monotone vs confidence, penalized by spread/depth/novelty/disagreement/entropy, rate-limited up, fast down.
- AdaptiveThresholds: updates min_confidence/max_entropy/novelty caps and cooldown based on rolling perf/drift snapshots; ROI only adjusts thresholds, never multiplies scale.
- Coherence: penalizes contradictory signals/stress; feeds scaler.
- LeverageEngine: computes leverage target from scale and risk state, caps by budgets/regime/scenario, rate-limited up, instant down.
- PortfolioRouter: ranks assets net of cost, enforces cluster and concentration caps using clusters.yaml and budgets.

Outputs:
- Alloc(t): scale, leverage_target, trade_mode, asset_weights, cooldowns, thresholds, coherence_score, reasons → stored in Mongo alloc_cache + S3 parquet.
- MetaControlState: scale_raw/scale_smooth/leverage/cooldowns/thresholds/router selections.

Persistence:
- Mongo caches: signal_cache, state_cache, perf_snapshot_cache, drift_snapshot_cache, meta_state_cache, alloc_cache (TTL).
- S3: artifacts/meta_control/allocations/ and monitoring snapshots.

No performance chasing: recent PnL only adjusts thresholds/cooldowns/caps, never direct scale-up.
