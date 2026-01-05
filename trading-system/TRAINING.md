# UNIFIED PRODUCTION TRAINING

## Quick Start

```bash
# Train with defaults (BTCUSDT, 2023-2025, CPU)
./train.sh

# Train with custom parameters
SYMBOL=ETHUSDT START_DATE=2024-01-01 END_DATE=2025-12-31 DEVICE=cpu ./train.sh

# Or use Python directly
python3 train.py --symbol BTCUSDT --start-date 2023-01-01 --end-date 2025-12-31 --device cpu
```

## Configuration

Edit `training_config.py` for all hyperparameters:

### Market Environment
- `fee_bps`: Trading fees (default: 4.0 = 0.04%)
- `slippage_bps`: Execution slippage (default: 2.0)
- `leverage`: Position leverage (default: 1.0 = no leverage)
- `max_drawdown_stop`: Stop threshold (default: 15%)
- `max_daily_trades`: Trade frequency cap (default: 20)

### Data Splits
- `train_pct`: Training set (default: 70%)
- `val_pct`: Validation for early stopping (default: 15%)
- `test_pct`: Held-out test set (default: 15%)

### Edge Forecaster (Transformer)
- `seq_len`: Sequence length (default: 32 bars)
- `d_model`: Model dimension (default: 192)
- `n_heads`: Attention heads (default: 6)
- `n_layers`: Transformer layers (default: 5)
- `epochs`: Training epochs (default: 40)
- `batch_size`: Batch size (default: 256)
- `lr`: Learning rate (default: 2e-4)
- `tp_k`: Take-profit multiplier (default: 2.0 × ATR)
- `sl_k`: Stop-loss multiplier (default: 1.2 × ATR)

### Validation
- `lookahead_tests`: No-lookahead shuffle tests (default: 50)
- `bootstrap_samples`: Sharpe CI samples (default: 100)
- `ece_bins`: Calibration bins (default: 10)
- `min_val_sharpe`: Deployment threshold (default: 0.5)

## Components Trained

1. **Regime Classifier** (if enabled)
   - Multinomial logistic regression
   - 6 regimes: impulse, reversal, breakout, squeeze, calm, chop
   - Output: `artifacts/models/regime/{run_id}/`

2. **Edge Forecaster** (always)
   - Transformer architecture
   - Multi-task: quantiles (5%, 25%, 50%, 75%, 95%), direction probability, realized vol
   - Output: `artifacts/models/edge/{run_id}.pt`
   - Checkpoints: `{run_id}_best_trading.pt`, `{run_id}_best_val_loss.pt`

3. **Specialists** (future)
   - Regime-specific models (not implemented)

4. **Gating Network** (future)
   - Meta-learner for model ensemble (not implemented)

## Validation Features

### No-Lookahead Proof
- 50 shuffle tests of future raw OHLCV
- Recomputes features after shuffle
- Fails if any feature depends on future

### Bootstrap Confidence Intervals
- 100 bootstrap samples for Sharpe ratio
- Rejects models with CI width > 2.0 (unstable)

### Nested Validation
- Train/Val/Test split (70/15/15)
- Test set NEVER seen during training
- Prevents optimization leak

### Temperature Scaling
- Platt calibration for probability outputs
- Improves ECE (Expected Calibration Error)

### Quality Gates
- Minimum Sharpe: 0.5
- Maximum Drawdown: 20%
- Minimum Win Rate: 45%
- Minimum Trades: 50

## Outputs

```
artifacts/models/
├── edge/
│   ├── {run_id}.pt                    # Final model
│   ├── {run_id}_best_trading.pt       # Best proxy score
│   ├── {run_id}_best_val_loss.pt      # Best validation loss
│   └── {run_id}_metrics.json          # Training history
└── regime/
    └── {run_id}/
        └── model.pkl                   # Regime classifier
```

## Production Checklist

- [x] No temporal leakage (no-lookahead proof)
- [x] Causal features (all shifted)
- [x] Bootstrap validation (Sharpe CI)
- [x] Nested validation (held-out test set)
- [x] Temperature calibration (ECE)
- [x] Real market costs (fees + slippage)
- [x] Quality gates (Sharpe, DD, WR)
- [x] Reproducible (seeded)

## Example

```bash
# Production run
SYMBOL=BTCUSDT \
START_DATE=2023-01-01 \
END_DATE=2025-12-31 \
DEVICE=cpu \
RUN_ID=prod_$(date +%Y%m%d) \
./train.sh
```

Expected output:
```
Training: 70% → Val: 15% → Test: 15% (held-out)
No-lookahead: 50/50 passed
Epoch 40/40: Val Loss=0.234, Sharpe=1.45, Trades=156, CI=[0.89, 2.01]
Test Sharpe: 1.23 (TRUE OOS)
Models saved to: artifacts/models/prod_20260104/
```
