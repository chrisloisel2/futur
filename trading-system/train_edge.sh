#!/bin/bash
# Train Edge Forecaster on 5 years of data (2019-2023)
# PRODUCTION VERSION with all fixes applied - FORCE GPU

set -e

echo "========================================================================"
echo "TRAINING EDGE FORECASTER - PRODUCTION (GPU FORCED)"
echo "========================================================================"
echo ""
echo "Dataset: BTCUSDT 2019-01-01 → 2023-12-31 (5 years)"
echo ""
echo "PRODUCTION FIXES APPLIED:"
echo "  ✅ Horizon: 240min → 60min (1h for faster trading)"
echo "  ✅ TP dynamic: max(0.0025, 1.0 * rv_60) instead of fixed 1%"
echo "  ✅ Weight decay: 1e-4 → 1e-2 (100x stronger)"
echo "  ✅ ReduceLROnPlateau (adaptive LR)"
echo "  ✅ Early stopping (patience=5)"
echo "  ✅ Best model checkpointing"
echo "  ✅ 20+ metrics logged (directional_accuracy, overfitting_ratio, etc.)"
echo "  ✅ GPU FORCED (cuda)"
echo ""
echo "Parameters:"
echo "  - Horizon:       60 minutes (was 240)"
echo "  - TP threshold:  Dynamic (rv_60 based)"
echo "  - Sequence:      32 timesteps"
echo "  - Epochs:        100 (with early stopping)"
echo "  - Batch size:    256"
echo "  - Device:        cuda (FORCED)"
echo ""
echo "Expected Results:"
echo "  - tp_hit_rate:         ~40-45% (was ~55%, more discriminant)"
echo "  - overfitting_ratio:   <1.15 (test_loss/train_loss)"
echo "  - directional_accuracy: >55%"
echo ""

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

# Check CUDA availability
if command -v nvidia-smi &> /dev/null; then
    if nvidia-smi &> /dev/null; then
        echo "✅ CUDA detected - RTX 3070 ready"
        nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
        echo ""
    else
        echo "⚠️  nvidia-smi failed - GPU might not be available"
        echo "   Training will attempt to use CUDA anyway"
        echo ""
    fi
else
    echo "⚠️  nvidia-smi not found - GPU might not be available"
    echo "   Training will attempt to use CUDA anyway"
    echo ""
fi

python3 scripts/train_edge_forecaster.py \
  --start-date 2019-01-01 \
  --end-date 2023-12-31 \
  --symbol BTCUSDT \
  --output artifacts/models/edge/production_v1.pt \
  --horizon 60 \
  --tp-threshold 0.01 \
  --seq-len 32 \
  --epochs 100 \
  --batch-size 256 \
  --lr 0.001 \
  --device cuda \
  --test-size 0.2

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "========================================================================"
    echo "✅ EDGE FORECASTER TRAINING COMPLETE"
    echo "========================================================================"
    echo ""
    echo "Model saved to: artifacts/models/edge/production_v1.pt"
    echo "Best checkpoint: artifacts/models/edge/production_v1_best_checkpoint.pt"
    echo "Metrics saved to: artifacts/models/edge/production_v1_metrics.json"
    echo ""
    echo "Validation:"
    echo "  - Check overfitting: cat artifacts/models/edge/production_v1_metrics.json | grep overfitting_ratio"
    echo "  - Check directional: cat artifacts/models/edge/production_v1_metrics.json | grep directional_accuracy"
    echo "  - Check tp_hit_rate: cat artifacts/models/edge/production_v1_metrics.json | grep tp_hit"
    echo ""
    echo "Next step: Test with trained models using ./test_pipeline_trained.sh"
else
    echo ""
    echo "========================================================================"
    echo "❌ TRAINING FAILED (exit code: $EXIT_CODE)"
    echo "========================================================================"
    exit $EXIT_CODE
fi
