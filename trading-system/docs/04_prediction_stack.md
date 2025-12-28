# Prediction Stack (Simplified V1)

- Gating (hard thresholds) determines tradeable/mode/coarse_direction using quality flags, spread, staleness.
- Regime classifier outputs regime probabilities and entropy (stub baseline).
- Edge forecaster outputs quantile returns, p_hit, expected shortfall, rv_fwd.
- Comparator computes novelty/disagreement as stability indicators.
- Decision logic: INVALIDATE (not tradeable/flags), DELAY (low confidence/entropy/novelty/disagreement), else CONFIRM.
- Risk-aware filters clamp direction/mode under spread or extreme funding.
- Signals persisted to S3 parquet (`data/signals/`) and cached in Mongo (`signal_cache`).

Schema: see `data/schemas/signal.avsc` for flatten columns including regime_prob_* and quantiles.
