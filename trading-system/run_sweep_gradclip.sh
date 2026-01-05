#!/bin/bash
# =============================================================================
# RUN SWEEP GRAD_CLIP (RUN 1 — PLAN EXPÉRIMENTAL)
# =============================================================================
# Si baseline montre clip_ratio_epoch_pct > 80%, ce script teste 3 valeurs
# de grad_clip pour identifier la valeur optimale

set -e

echo "=========================================="
echo "RUN 1: SWEEP GRAD_CLIP"
echo "=========================================="
echo ""
echo "Hypothèse: grad_clip=1.0 est trop bas → 90% des steps clippés"
echo ""
echo "Tests:"
echo "  1. grad_clip = 1.0 (baseline, pour référence)"
echo "  2. grad_clip = 5.0 (5x plus large)"
echo "  3. grad_clip = 1000.0 (désactivé en pratique)"
echo ""
echo "Critère de succès:"
echo "  - val_loss améliore > 10% avec grad_clip=5.0 ou 1000.0"
echo "  - clip_ratio_epoch_pct baisse significativement"
echo ""

START_DATE="2024-01-01"
END_DATE="2024-12-31"

# Parallel runs si GPU multiple, sinon séquentiel
PARALLEL=${1:-false}

if [ "$PARALLEL" = "true" ]; then
    echo "Mode: PARALLEL (3 runs en même temps)"
    echo "CRITICAL: Nécessite 3 GPUs ou beaucoup de VRAM"
    echo ""
else
    echo "Mode: SEQUENTIAL (3 runs l'un après l'autre)"
    echo "Durée estimée: 45-60min"
    echo ""
fi

# =============================================================================
# RUN 1a: grad_clip = 1.0 (baseline, optionnel si déjà fait)
# =============================================================================
echo "=========================================="
echo "RUN 1a: grad_clip = 1.0 (baseline)"
echo "=========================================="

if [ -f "artifacts/models/edge/baseline_diagnostic_v0_metrics.json" ]; then
    echo "Baseline déjà existant, skip."
else
    echo "Baseline non trouvé, run..."
    python3 scripts/train_edge_forecaster.py \
      --start-date "$START_DATE" \
      --end-date "$END_DATE" \
      --output artifacts/models/edge/sweep_gc_1 \
      --epochs 5 --data-pct 0.10 --log-interval 50 \
      --max-grad-norm 1.0 --device cuda --amp 1 \
      2>&1 | tee sweep_gc_1.log &
    PID_1=$!
fi

# =============================================================================
# RUN 1b: grad_clip = 5.0
# =============================================================================
echo "=========================================="
echo "RUN 1b: grad_clip = 5.0"
echo "=========================================="

python3 scripts/train_edge_forecaster.py \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --output artifacts/models/edge/sweep_gc_5 \
  --epochs 5 --data-pct 0.10 --log-interval 50 \
  --max-grad-norm 5.0 --device cuda --amp 1 \
  2>&1 | tee sweep_gc_5.log &
PID_2=$!

# =============================================================================
# RUN 1c: grad_clip = 1000.0 (désactivé)
# =============================================================================
echo "=========================================="
echo "RUN 1c: grad_clip = 1000.0 (disabled)"
echo "=========================================="

python3 scripts/train_edge_forecaster.py \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --output artifacts/models/edge/sweep_gc_disabled \
  --epochs 5 --data-pct 0.10 --log-interval 50 \
  --max-grad-norm 1000.0 --device cuda --amp 1 \
  2>&1 | tee sweep_gc_1000.log &
PID_3=$!

# Wait for all runs
if [ "$PARALLEL" = "true" ]; then
    echo ""
    echo "Waiting for parallel runs to complete..."
    wait $PID_1 $PID_2 $PID_3
else
    echo ""
    echo "Sequential mode: waiting for each run..."
    wait $PID_2
    wait $PID_3
fi

echo ""
echo "=========================================="
echo "COMPARAISON AUTOMATIQUE DES RÉSULTATS"
echo "=========================================="

# Extract val_loss final pour chaque run
VAL_LOSS_1=$(grep "EPOCH_SUMMARY_PRODUCTION_GRADE" sweep_gc_1.log 2>/dev/null | tail -1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('val_loss', 999.0))
" 2>/dev/null || echo "999.0")

VAL_LOSS_5=$(grep "EPOCH_SUMMARY_PRODUCTION_GRADE" sweep_gc_5.log | tail -1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('val_loss', 999.0))
" 2>/dev/null || echo "999.0")

VAL_LOSS_1000=$(grep "EPOCH_SUMMARY_PRODUCTION_GRADE" sweep_gc_1000.log | tail -1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('val_loss', 999.0))
" 2>/dev/null || echo "999.0")

# Extract clip_ratio
CLIP_RATIO_1=$(grep "EPOCH_SUMMARY_PRODUCTION_GRADE" sweep_gc_1.log 2>/dev/null | tail -1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('gradient_summary', {}).get('clip_ratio_epoch_pct', 0.0))
" 2>/dev/null || echo "0.0")

CLIP_RATIO_5=$(grep "EPOCH_SUMMARY_PRODUCTION_GRADE" sweep_gc_5.log | tail -1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('gradient_summary', {}).get('clip_ratio_epoch_pct', 0.0))
" 2>/dev/null || echo "0.0")

CLIP_RATIO_1000=$(grep "EPOCH_SUMMARY_PRODUCTION_GRADE" sweep_gc_1000.log | tail -1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('gradient_summary', {}).get('clip_ratio_epoch_pct', 0.0))
" 2>/dev/null || echo "0.0")

echo ""
printf "%-20s | %-15s | %-20s\n" "grad_clip" "val_loss" "clip_ratio_epoch_pct"
printf "%-20s-+-%-15s-+-%-20s\n" "--------------------" "---------------" "--------------------"
printf "%-20s | %-15s | %-20s\n" "1.0 (baseline)" "$VAL_LOSS_1" "$CLIP_RATIO_1%"
printf "%-20s | %-15s | %-20s\n" "5.0" "$VAL_LOSS_5" "$CLIP_RATIO_5%"
printf "%-20s | %-15s | %-20s\n" "1000.0 (disabled)" "$VAL_LOSS_1000" "$CLIP_RATIO_1000%"
echo ""

# Compute improvement
IMPROVEMENT_5=$(python3 -c "print(round((1 - $VAL_LOSS_5 / $VAL_LOSS_1) * 100, 1))" 2>/dev/null || echo "N/A")
IMPROVEMENT_1000=$(python3 -c "print(round((1 - $VAL_LOSS_1000 / $VAL_LOSS_1) * 100, 1))" 2>/dev/null || echo "N/A")

echo "Amélioration vs baseline:"
echo "  - grad_clip=5.0: $IMPROVEMENT_5%"
echo "  - grad_clip=1000.0: $IMPROVEMENT_1000%"
echo ""

# Determine best config
BEST_VAL_LOSS=$(python3 -c "print(min($VAL_LOSS_1, $VAL_LOSS_5, $VAL_LOSS_1000))")
BEST_CONFIG=""

if (( $(echo "$VAL_LOSS_5 == $BEST_VAL_LOSS" | bc -l) )); then
    BEST_CONFIG="5.0"
elif (( $(echo "$VAL_LOSS_1000 == $BEST_VAL_LOSS" | bc -l) )); then
    BEST_CONFIG="1000.0"
else
    BEST_CONFIG="1.0"
fi

echo "=========================================="
echo "RÉSULTAT DU SWEEP"
echo "=========================================="
echo ""

if [ "$BEST_CONFIG" != "1.0" ]; then
    echo "✅ HYPOTHÈSE VALIDÉE: grad_clip était trop bas"
    echo ""
    echo "  Meilleure config: grad_clip = $BEST_CONFIG"
    echo "  val_loss: $BEST_VAL_LOSS"
    echo ""
    echo "  Prochaine étape:"
    echo "    Retrain sur 100% data avec grad_clip=$BEST_CONFIG"
    echo ""
    echo "  Commande:"
    echo "    python scripts/train_edge_forecaster.py \\"
    echo "      --start-date 2024-01-01 --end-date 2024-12-31 \\"
    echo "      --output artifacts/models/edge/production_v4_optimal \\"
    echo "      --epochs 40 \\"
    echo "      --max-grad-norm $BEST_CONFIG \\"
    echo "      --device cuda"
else
    echo "⚠️  HYPOTHÈSE RÉFUTÉE: grad_clip n'était PAS la cause"
    echo ""
    echo "  grad_clip=1.0 reste meilleur ou équivalent"
    echo ""
    echo "  Prochaine étape:"
    echo "    Tester CAUSE #2 (saturation) ou CAUSE #3 (AMP)"
    echo ""
    echo "  Vérifier dans baseline_diagnostic.log:"
    echo "    - pct_saturated > 10% → tester clamps plus larges"
    echo "    - amp_scale < 100 → tester AMP=off"
fi

echo ""
echo "FIN DU SWEEP GRAD_CLIP"
