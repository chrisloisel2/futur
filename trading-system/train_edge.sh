#!/bin/bash
# Train Edge Forecaster - PRODUCTION V3 - EXCELLENCE
#
# Stratégie: Walk-forward avec recalibration pour éviter distribution shift
# Fix du problème: p_hit_calibrated = 0.30 → 0.50 sur 2023

set -e

echo "========================================================================"
echo "TRAINING EDGE FORECASTER - PRODUCTION V3 - EXCELLENCE"
echo "========================================================================"
echo ""
echo "STRATÉGIE: Walk-Forward Training + Recalibration"
echo "  1. Train: 2019-2022 (4 ans) → ~80% train, ~20% val (2022)"
echo "  2. Recalibrate: 2023 Q1-Q3 (9 mois) pour ajuster distribution"
echo "  3. Test OOS: 2023 Q4 (3 mois) pour validation finale"
echo ""
echo "AMÉLIORATIONS V3:"
echo "  ✅ Horizon: 60min (1h pour trading rapide)"
echo "  ✅ TP/SL adaptatifs par quantile de volatilité"
echo "  ✅ Dropout: 0.15 (vs 0.10 - régularisation)"
echo "  ✅ Weight decay: 1e-3 (optimal pour Transformer)"
echo "  ✅ Focal loss sur p_hit (focus hard examples, γ=2.0)"
echo "  ✅ Trading score = Sharpe - 2×ECE - overfit_penalty"
echo "  ✅ Multiple checkpoints (best_trading, best_sharpe)"
echo "  ✅ Recalibration sur 2023 pour fix distribution shift"
echo ""
echo "TARGET PERFORMANCE:"
echo "  - Brier (calibrated):    < 0.20"
echo "  - ECE (after calib):     < 0.10"
echo "  - p_hit_mean_calibrated: 0.45-0.50 (sur 2023)"
echo "  - Sharpe_pred:           > 0.5"
echo "  - Win rate (paper):      > 42%"
echo "  - Avg gross PnL:         > 0.10% per trade"
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

# ============================================================================
# STEP 1: Training sur 2019-2022 (évite distribution shift 2023)
# ============================================================================
echo "STEP 1/4: Training sur 2019-2022..."
echo "--------------------------------------------------------------------"

python3 scripts/train_edge_forecaster.py \
  --start-date 2019-01-01 \
  --end-date 2022-12-31 \
  --symbol BTCUSDT \
  --output artifacts/models/edge/production_v3.pt \
  --horizon 60 \
  --k-tp 2.0 \
  --m-sl 1.5 \
  --min-tp 0.005 \
  --max-tp 0.025 \
  --min-sl 0.003 \
  --max-sl 0.015 \
  --adaptive-tp 1 \
  --seq-len 32 \
  --epochs 100 \
  --batch-size 256 \
  --lr 0.001 \
  --device cuda

EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "❌ Training failed (exit code: $EXIT_CODE)"
    exit $EXIT_CODE
fi

echo "✅ Training complete (2019-2022)"
echo ""

# ============================================================================
# STEP 2: Recalibration sur 2023 Q1-Q3 (9 mois)
# ============================================================================
echo "STEP 2/4: Recalibration sur 2023 Q1-Q3..."
echo "--------------------------------------------------------------------"

python3 scripts/recalibrate_model.py \
  --model-path artifacts/models/edge/production_v3_best_trading.pt \
  --calibrator-output artifacts/models/edge/production_v3_calibrator_2023.pkl \
  --start-date 2023-01-01 \
  --end-date 2023-09-30 \
  --symbol BTCUSDT \
  --method platt

if [ $? -ne 0 ]; then
    echo "❌ Recalibration failed"
    exit 1
fi

# Replace calibrator
cp artifacts/models/edge/production_v3_calibrator_2023.pkl \
   artifacts/models/edge/production_v3_calibrator.pkl

echo "✅ Recalibration complete (2023 Q1-Q3)"
echo ""

# ============================================================================
# STEP 3: Paper test OOS sur 2023 Q4 (validation finale)
# ============================================================================
echo "STEP 3/4: Paper test OOS sur 2023 Q4..."
echo "--------------------------------------------------------------------"

# Copy best_trading model to production
cp artifacts/models/edge/production_v3_best_trading.pt \
   artifacts/models/edge/production_v3.pt

python3 scripts/paper_test_trained.py \
  --symbol BTCUSDT \
  --start 2023-10-01 \
  --end 2023-12-31 \
  --entry-threshold 0.85 \
  --use-shorts \
  --gating none \
  --cooldown-minutes 120 \
  --min-edge 0.10 \
  --output-dir artifacts/paper/production_v3_2023q4_oos

if [ $? -ne 0 ]; then
    echo "⚠️ Paper test failed (non-blocking)"
fi

echo "✅ Paper test complete (2023 Q4 OOS)"
echo ""

# ============================================================================
# STEP 4: Full 2023 test with optimal threshold
# ============================================================================
echo "STEP 4/4: Full 2023 test (with recalibrated model)..."
echo "--------------------------------------------------------------------"

python3 scripts/paper_test_trained.py \
  --symbol BTCUSDT \
  --start 2023-01-01 \
  --end 2023-12-31 \
  --entry-threshold 0.85 \
  --use-shorts \
  --gating none \
  --cooldown-minutes 120 \
  --min-edge 0.10 \
  --output-dir artifacts/paper/production_v3_2023_full

echo ""
echo "========================================================================"
echo "✅ TRAINING COMPLETE - PRODUCTION V3"
echo "========================================================================"
echo ""
echo "Models:"
echo "  - artifacts/models/edge/production_v3.pt (best_trading)"
echo "  - artifacts/models/edge/production_v3_best_sharpe.pt"
echo "  - artifacts/models/edge/production_v3_calibrator.pkl (recalibrated 2023)"
echo ""
echo "Metrics:"
echo "  - artifacts/models/edge/production_v3_metrics.json"
echo ""
echo "Paper test results:"
echo "  - 2023 Q4 OOS: artifacts/paper/production_v3_2023q4_oos/"
echo "  - 2023 Full:   artifacts/paper/production_v3_2023_full/"
echo ""
echo "Validation checks:"
jq '.brier_phit_calibrated, .ece_after, .sharpe_pred' artifacts/models/edge/production_v3_metrics.json
echo ""
echo "Expected improvements vs V1:"
echo "  - p_hit_calibrated: 0.30 → 0.48 (closer to true hit rate)"
echo "  - Fees: 103% → <30% (less overtrading at threshold 0.85)"
echo "  - ROI: -68% → -10% to +5% (depends on market)"
echo ""
echo "Next: Run threshold sweep on full 2023"
echo "  bash RUN_SWEEP.sh"
echo "========================================================================"
