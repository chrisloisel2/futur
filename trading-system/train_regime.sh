#!/bin/bash
# Train BINARY Regime Classifier on 5 years of data (2019-2023)
# PRODUCTION VERSION - BINARY CLASSIFICATION (calm vs reversal)

set -e

echo "========================================================================"
echo "TRAINING BINARY REGIME CLASSIFIER - PRODUCTION"
echo "========================================================================"
echo ""
echo "Dataset: BTCUSDT 2019-01-01 → 2023-12-31 (5 years)"
echo ""
echo "⚠️  CRITICAL ARCHITECTURE CHANGE:"
echo "  - Regimes: BINARY (calm, reversal) - impulse removed"
echo "  - Impulse: Now an EVENT detector (see impulse_detector.py)"
echo ""
echo "PRODUCTION FIXES APPLIED:"
echo "  ✅ SGDClassifier + class_weight='balanced'"
echo "  ✅ CalibratedClassifierCV (isotonic)"
echo "  ✅ Hard gates: accuracy>=0.60, calm_recall>=0.50, reversal_recall>=0.50"
echo "  ✅ ECE < 0.10 (calibration)"
echo ""
echo "Target Metrics (BINARY):"
echo "  - Accuracy:        > 60% (vs 46% with 3-class)"
echo "  - Calm Recall:     > 50%"
echo "  - Reversal Recall: > 50%"
echo "  - Brier Score:     < 0.20"
echo "  - ECE:             < 0.10"
echo ""

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

python3 scripts/train_regime_classifier_binary.py \
  --start-date 2019-01-01 \
  --end-date 2023-12-31 \
  --symbol BTCUSDT \
  --output artifacts/models/regime/production_binary_v1.pkl \
  --test-size 0.2 \
  --random-state 42 \
  --binary

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "========================================================================"
    echo "✅ BINARY REGIME CLASSIFIER TRAINING COMPLETE"
    echo "========================================================================"
    echo ""

    # Check if model passed gate (saved to production path)
    if [ -f "artifacts/models/regime/production_binary_v1.pkl" ]; then
        echo "✅ PRODUCTION GATE PASSED"
        echo ""
        echo "Model saved to: artifacts/models/regime/production_binary_v1.pkl"
        echo "Metrics saved to: artifacts/models/regime/production_binary_v1_metrics.json"
        echo ""
        echo "Classes: calm, reversal (BINARY)"
        echo ""
        echo "Next step: Train Edge Forecaster with ./train_edge.sh"
    else
        echo "❌ PRODUCTION GATE FAILED"
        echo ""
        echo "Model did NOT meet production criteria."
        echo "Check artifacts/models/regime/failed/ for failed model."
        echo ""
        echo "Common reasons:"
        echo "  - Accuracy < 0.60 (binary threshold)"
        echo "  - Calm recall < 0.50 (class collapse)"
        echo "  - Reversal recall < 0.50 (class collapse)"
        echo "  - ECE > 0.10 (poor calibration)"
        echo ""
        exit 1
    fi
else
    echo ""
    echo "========================================================================"
    echo "❌ TRAINING FAILED (exit code: $EXIT_CODE)"
    echo "========================================================================"
    exit $EXIT_CODE
fi
