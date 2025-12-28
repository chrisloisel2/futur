#!/bin/bash
# Train Edge Forecaster on 5 years of data (2019-2023)

set -e

echo "="
echo "TRAINING EDGE FORECASTER"
echo "="
echo ""
echo "Dataset: BTCUSDT 2019-01-01 → 2023-12-31 (5 years)"
echo "Target: Brier < 0.20, MAE < 0.5%, Sharpe > 0.5"
echo ""
echo "Forward horizon: 4 hours (240 minutes)"
echo "TP threshold: 1% (0.01)"
echo "Sequence length: 32 timesteps"
echo "Epochs: 50"
echo ""

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

python scripts/train_edge_forecaster.py \
  --start-date 2019-01-01 \
  --end-date 2023-12-31 \
  --symbol BTCUSDT \
  --output artifacts/models/edge/production_v1.pt \
  --horizon 240 \
  --tp-threshold 0.01 \
  --seq-len 32 \
  --epochs 50 \
  --batch-size 256 \
  --lr 0.001 \
  --device cpu \
  --test-size 0.2

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ EDGE FORECASTER TRAINING COMPLETE"
    echo ""
    echo "Model saved to: artifacts/models/edge/production_v1.pt"
    echo "Metrics saved to: artifacts/models/edge/production_v1_metrics.json"
    echo ""
    echo "Next step: Test with trained models using ./test_pipeline_trained.sh"
else
    echo ""
    echo "❌ TRAINING FAILED (exit code: $EXIT_CODE)"
    exit $EXIT_CODE
fi
