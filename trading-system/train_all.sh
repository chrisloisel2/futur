#!/bin/bash
# Train ALL models in sequence - PRODUCTION VERSION (BINARY REGIMES)
# Architecture corrected: impulse moved to event detection

set -e

echo "========================================================================"
echo "TRAINING ALL MODELS - PRODUCTION PIPELINE v3.0 (BINARY)"
echo "========================================================================"
echo ""
echo "⚠️  ARCHITECTURE CHANGE:"
echo "  - Regime Classifier: BINARY (calm vs reversal)"
echo "  - Impulse: EVENT detector (not a regime)"
echo ""
echo "This will train:"
echo "  1. Binary Regime Classifier (SGDClassifier + CalibratedClassifierCV)"
echo "  2. Edge Forecaster (Transformer + overfitting fixes)"
echo ""
echo "Dataset: BTCUSDT 2019-01-01 → 2023-12-31 (5 years)"
echo ""
echo "PRODUCTION FIXES APPLIED:"
echo "  ✅ Regime: BINARY classification, class_weight='balanced'"
echo "  ✅ Regime gates: accuracy>=0.60, calm/reversal_recall>=0.50, ECE<0.10"
echo "  ✅ Edge: horizon 60min, TP dynamic, early stopping, checkpointing"
echo ""
echo "Estimated time: 30-90 minutes (depending on hardware)"
echo ""

read -p "Continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# Step 1: Train Binary Regime Classifier
echo ""
echo "========================================================================"
echo "STEP 1/2: Training BINARY Regime Classifier..."
echo "========================================================================"
echo ""

./train_regime.sh

if [ $? -ne 0 ]; then
    echo ""
    echo "========================================================================"
    echo "❌ Binary regime classifier training failed. Aborting pipeline."
    echo "========================================================================"
    exit 1
fi

# Step 2: Train Edge Forecaster
echo ""
echo "========================================================================"
echo "STEP 2/2: Training Edge Forecaster..."
echo "========================================================================"
echo ""

./train_edge.sh

if [ $? -ne 0 ]; then
    echo ""
    echo "========================================================================"
    echo "❌ Edge forecaster training failed. Aborting pipeline."
    echo "========================================================================"
    exit 1
fi

# Summary
echo ""
echo "========================================================================"
echo "🎉 ALL MODELS TRAINED SUCCESSFULLY!"
echo "========================================================================"
echo ""
echo "Models saved:"
echo "  - Regime (BINARY): artifacts/models/regime/production_binary_v1/model.pkl"
echo "  - Edge:            artifacts/models/edge/production_v1.pt"
echo "  - Edge checkpoint: artifacts/models/edge/production_v1_best_checkpoint.pt"
echo ""
echo "Metrics:"
echo "  - Regime: artifacts/models/regime/production_binary_v1/metrics.json"
echo "  - Edge:   artifacts/models/edge/production_v1_metrics.json"
echo ""
echo "Quick validation:"
echo "  - Regime accuracy:  cat artifacts/models/regime/production_binary_v1/metrics.json | grep accuracy"
echo "  - Calm recall:      cat artifacts/models/regime/production_binary_v1/metrics.json | grep -A 1 'calm'"
echo "  - Reversal recall:  cat artifacts/models/regime/production_binary_v1/metrics.json | grep -A 1 'reversal'"
echo "  - Edge overfitting: cat artifacts/models/edge/production_v1_metrics.json | grep overfitting_ratio"
echo ""
echo "⚠️  IMPORTANT: Impulse is now handled separately as an EVENT"
echo "  - See: ai/models/training/common/impulse_detector.py"
echo "  - Integration: ai/models/training/common/meta_control.py"
echo ""
echo "Next steps:"
echo "  1. Test with trained models: ./test_pipeline_trained.sh"
echo "  2. Optimize thresholds: python scripts/optimize_thresholds.py"
echo "  3. Run full backtest: ./backtest_real_data.sh"
echo ""
