#!/bin/bash
# Test pipeline with TRAINED models on 1 week of data

set -e

echo "="
echo "TESTING PIPELINE WITH TRAINED MODELS"
echo "="
echo ""
echo "Period: 2024-01-01 → 2024-01-07 (1 week)"
echo "Models:"
echo "  - Regime: artifacts/models/regime/production_v1.pkl"
echo "  - Edge:   artifacts/models/edge/production_v1.pt"
echo ""

# Check if models exist
if [ ! -f "artifacts/models/regime/production_v1.pkl" ]; then
    echo "❌ Regime model not found. Train it first with: ./train_regime.sh"
    exit 1
fi

if [ ! -f "artifacts/models/edge/production_v1.pt" ]; then
    echo "❌ Edge model not found. Train it first with: ./train_edge.sh"
    exit 1
fi

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

python -m src.app.main backtest \
  --start-date 2024-01-01 \
  --end-date 2024-01-07 \
  --symbols BTCUSDT \
  --config configs/base.yaml \
  --regime-model artifacts/models/regime/production_v1.pkl \
  --edge-model artifacts/models/edge/production_v1.pt

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ PIPELINE TEST COMPLETE"
    echo ""
    echo "View results:"
    python view_results.py
    echo ""
    echo "Expected improvements vs untrained:"
    echo "  - Sharpe: -5.25 → >0.0 (should be positive)"
    echo "  - Win Rate: 33.7% → >48%"
    echo "  - Confirm Rate: 100% → 10-30% (more selective)"
    echo ""
else
    echo ""
    echo "❌ TEST FAILED (exit code: $EXIT_CODE)"
    exit $EXIT_CODE
fi
