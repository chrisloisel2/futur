#!/bin/bash
# Train Regime Classifier on 5 years of data (2019-2023)

set -e

echo "="
echo "TRAINING REGIME CLASSIFIER"
echo "="
echo ""
echo "Dataset: BTCUSDT 2019-01-01 → 2023-12-31 (5 years)"
echo "Target: Accuracy > 60%, Entropy < 1.5, Brier < 0.20"
echo ""

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

python scripts/train_regime_classifier.py \
  --start-date 2019-01-01 \
  --end-date 2023-12-31 \
  --symbol BTCUSDT \
  --output artifacts/models/regime/production_v1.pkl \
  --test-size 0.2 \
  --random-state 42

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ REGIME CLASSIFIER TRAINING COMPLETE"
    echo ""
    echo "Model saved to: artifacts/models/regime/production_v1.pkl"
    echo "Metrics saved to: artifacts/models/regime/production_v1_metrics.json"
    echo ""
    echo "Next step: Train Edge Forecaster with ./train_edge.sh"
else
    echo ""
    echo "❌ TRAINING FAILED (exit code: $EXIT_CODE)"
    exit $EXIT_CODE
fi
