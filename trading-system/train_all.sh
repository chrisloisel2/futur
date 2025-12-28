#!/bin/bash
# Train ALL models in sequence

set -e

echo "="
echo "TRAINING ALL MODELS - PRODUCTION PIPELINE"
echo "="
echo ""
echo "This will train:"
echo "  1. Regime Classifier (sklearn LogisticRegression)"
echo "  2. Edge Forecaster (Transformer with causal attention)"
echo ""
echo "Dataset: BTCUSDT 2019-01-01 → 2023-12-31 (5 years)"
echo ""
echo "Estimated time: 30-60 minutes (depending on CPU)"
echo ""

read -p "Continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# Step 1: Train Regime Classifier
echo ""
echo "STEP 1/2: Training Regime Classifier..."
echo ""

./train_regime.sh

if [ $? -ne 0 ]; then
    echo "❌ Regime classifier training failed. Aborting."
    exit 1
fi

# Step 2: Train Edge Forecaster
echo ""
echo "STEP 2/2: Training Edge Forecaster..."
echo ""

./train_edge.sh

if [ $? -ne 0 ]; then
    echo "❌ Edge forecaster training failed. Aborting."
    exit 1
fi

# Summary
echo ""
echo "="
echo "🎉 ALL MODELS TRAINED SUCCESSFULLY!"
echo "="
echo ""
echo "Models saved:"
echo "  - Regime: artifacts/models/regime/production_v1.pkl"
echo "  - Edge:   artifacts/models/edge/production_v1.pt"
echo ""
echo "Next steps:"
echo "  1. Test with trained models: ./test_pipeline_trained.sh"
echo "  2. Optimize thresholds: python scripts/optimize_thresholds.py"
echo "  3. Run full backtest: ./backtest_real_data.sh"
echo ""
