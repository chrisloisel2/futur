#!/bin/bash
# Walk-Forward Training - Solution au distribution shift
# Entraîne sur fenêtres roulantes : 2019-2022 → test 2023
# Résout le problème p_hit_calibrated = 0.30 (au lieu de 0.50)

set -e

echo "========================================================================"
echo "WALK-FORWARD TRAINING - PRODUCTION V2"
echo "========================================================================"
echo ""
echo "Stratégie:"
echo "  1. Entraînement sur 2019-2022 (4 ans)"
echo "  2. Validation sur 2023 (1 an)"
echo "  3. Recalibration sur 2023 Q1-Q2"
echo "  4. Test final sur 2023 Q3-Q4"
echo ""
echo "Fixes appliqués:"
echo "  ✅ Horizon: 60min (1h)"
echo "  ✅ TP/SL adaptatifs par quantile de vol"
echo "  ✅ Dropout augmenté (0.15)"
echo "  ✅ Weight decay optimal (1e-3)"
echo "  ✅ Focal loss sur p_hit (focus hard examples)"
echo "  ✅ Trading score metric (Sharpe - ECE - overfit)"
echo "  ✅ Multiple checkpoints (best_trading, best_sharpe, best_loss)"
echo ""

cd /home/qbee/Bureau/Bourse/futur/trading-system

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

# ============================================================================
# STEP 1: Training sur 2019-2022
# ============================================================================
echo ""
echo "STEP 1/4: Training sur 2019-2022..."
echo "--------------------------------------------------------------------"

python3 scripts/train_edge_forecaster.py \
  --start-date 2019-01-01 \
  --end-date 2022-12-31 \
  --symbol BTCUSDT \
  --output artifacts/models/edge/production_v2_walkforward.pt \
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
  --device cuda \
  --test-size 0.2

if [ $? -ne 0 ]; then
    echo "❌ Training failed"
    exit 1
fi

echo ""
echo "✅ Training complete"
echo ""

# ============================================================================
# STEP 2: Recalibration sur 2023 Q1-Q2 (6 mois)
# ============================================================================
echo ""
echo "STEP 2/4: Recalibration sur 2023 Q1-Q2..."
echo "--------------------------------------------------------------------"

python3 scripts/recalibrate_model.py \
  --model-path artifacts/models/edge/production_v2_walkforward_best_trading.pt \
  --calibrator-output artifacts/models/edge/production_v2_calibrator_2023h1.pkl \
  --start-date 2023-01-01 \
  --end-date 2023-06-30 \
  --symbol BTCUSDT \
  --method platt

if [ $? -ne 0 ]; then
    echo "❌ Recalibration failed"
    exit 1
fi

echo ""
echo "✅ Recalibration complete"
echo ""

# ============================================================================
# STEP 3: Replace old calibrator
# ============================================================================
echo ""
echo "STEP 3/4: Replacing calibrator..."
echo "--------------------------------------------------------------------"

cp artifacts/models/edge/production_v2_calibrator_2023h1.pkl \
   artifacts/models/edge/production_v2_calibrator.pkl

echo "✅ Calibrator updated"
echo ""

# ============================================================================
# STEP 4: Paper test sur 2023 Q3-Q4 (out-of-sample)
# ============================================================================
echo ""
echo "STEP 4/4: Paper test sur 2023 Q3-Q4 (out-of-sample)..."
echo "--------------------------------------------------------------------"

# Copier le modèle pour paper test
cp artifacts/models/edge/production_v2_walkforward_best_trading.pt \
   artifacts/models/edge/production_v2.pt

python3 scripts/paper_test_trained.py \
  --symbol BTCUSDT \
  --start 2023-07-01 \
  --end 2023-12-31 \
  --entry-threshold 0.85 \
  --use-shorts \
  --gating none \
  --cooldown-minutes 120 \
  --min-edge 0.10 \
  --output-dir artifacts/paper/walkforward_2023q3q4

if [ $? -ne 0 ]; then
    echo "❌ Paper test failed"
    exit 1
fi

echo ""
echo "========================================================================"
echo "✅ WALK-FORWARD TRAINING COMPLETE"
echo "========================================================================"
echo ""
echo "Models:"
echo "  - artifacts/models/edge/production_v2.pt (best_trading)"
echo "  - artifacts/models/edge/production_v2_calibrator.pkl (recalibrated on 2023 Q1-Q2)"
echo ""
echo "Paper test results (2023 Q3-Q4 OOS):"
echo "  - artifacts/paper/walkforward_2023q3q4/trades.csv"
echo "  - artifacts/paper/walkforward_2023q3q4/paper_metrics.json"
echo ""
echo "Expected improvements:"
echo "  - p_hit_calibrated ≈ 0.45-0.50 (was 0.30)"
echo "  - Better Sharpe (less overtrading)"
echo "  - Lower fees (<20% vs 103%)"
echo ""
echo "Next: Test on full 2023 with threshold sweep"
echo "========================================================================"
