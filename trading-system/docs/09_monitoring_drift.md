# Monitoring & Drift Control (Live)

- Data drift: PSI/JS/KS/missingness over X_fast/X_mid/S_slow features with baselines from S3.
- Prediction drift: calibration decay, entropy, p_hit shifts.
- Performance drift: pnl/hit-rate/slippage/latency; only used for defensive actions.
- Regime drift: transition rate and regime distribution shifts.
- Actions: defensive only (freeze, tighten thresholds, cooldown, reduce caps), rate-limited with TTL.
- Alerts: INFO/WARN/CRIT stored in Mongo and emitted to dashboards; bundles exported to S3 for investigation.
