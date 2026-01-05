#!/bin/bash
# Recalibration seule - si le modèle production_v3_best_trading.pt existe déjà
set -e

cd /home/qbee/Bureau/Bourse/futur/trading-system

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

echo "========================================================================"
echo "RECALIBRATION RAPIDE - PRODUCTION V3"
echo "========================================================================"
echo ""

# Check if model exists
if [ ! -f "artifacts/models/edge/production_v3_best_trading.pt" ]; then
    echo "❌ Model not found: artifacts/models/edge/production_v3_best_trading.pt"
    echo "   Run ./train_edge.sh first to train the model"
    exit 1
fi

echo "✅ Model found: production_v3_best_trading.pt"
echo ""

# ============================================================================
# STEP 1: Recalibration sur 2023 Q1-Q3 (9 mois)
# ============================================================================
echo "STEP 1/3: Recalibration sur 2023 Q1-Q3..."
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

# Copy best_trading model to production
cp artifacts/models/edge/production_v3_best_trading.pt \
   artifacts/models/edge/production_v3.pt

echo "✅ Recalibration complete (2023 Q1-Q3)"
echo ""

# ============================================================================
# STEP 2: Paper test OOS sur 2023 Q4
# ============================================================================
echo "STEP 2/3: Paper test OOS sur 2023 Q4..."
echo "--------------------------------------------------------------------"

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
    echo "⚠️ Paper test Q4 failed (non-blocking)"
fi

echo "✅ Paper test Q4 complete"
echo ""

# ============================================================================
# STEP 3: Full 2023 test
# ============================================================================
echo "STEP 3/3: Full 2023 test (with recalibrated model)..."
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
echo "✅ RECALIBRATION COMPLETE"
echo "========================================================================"
echo ""
echo "Models:"
echo "  - artifacts/models/edge/production_v3.pt (best_trading)"
echo "  - artifacts/models/edge/production_v3_calibrator.pkl (recalibrated 2023)"
echo ""
echo "Paper test results:"
echo "  - 2023 Q4 OOS: artifacts/paper/production_v3_2023q4_oos/"
echo "  - 2023 Full:   artifacts/paper/production_v3_2023_full/"
echo ""
echo "Validation checks:"
cat artifacts/paper/production_v3_2023_full/paper_metrics.json | jq '{
  roi: .roi,
  sharpe: .sharpe,
  fees: .total_fees_pct,
  win_rate: .win_rate,
  avg_gross: .avg_trade_gross_pct,
  avg_net: .avg_trade_pct
}'
echo ""
echo "Expected improvements vs V1:"
echo "  - p_hit_calibrated: 0.30 → 0.48 (check logs)"
echo "  - Fees: 103% → <30%"
echo "  - ROI: -68% → -10% to +5%"
echo "========================================================================"
