#!/bin/bash
# =============================================================================
# RUN BASELINE DIAGNOSTIC (RUN 0 — PLAN EXPÉRIMENTAL)
# =============================================================================
# Premier run avec logging complet pour établir la baseline et identifier
# la cause du plateau (grad_clip / saturation / AMP)

set -e

echo "=========================================="
echo "RUN 0: BASELINE DIAGNOSTIC"
echo "=========================================="
echo ""
echo "Objectif: Établir baseline avec logging complet"
echo ""
echo "Configuration:"
echo "  - Data: 10% (runs rapides ~10min)"
echo "  - Epochs: 3"
echo "  - Log interval: 50 batches"
echo "  - grad_clip: 1.0 (valeur actuelle, probablement trop bas)"
echo "  - AMP: enabled"
echo ""
echo "Métriques critiques à observer:"
echo "  1. clip_ratio_epoch_pct > 80% → grad_clip trop bas (CAUSE #1)"
echo "  2. pct_saturated > 10% → target saturation (CAUSE #2)"
echo "  3. amp_scale < 100 après epoch 3 → AMP instable (CAUSE #3)"
echo ""

# CRITICAL: Use large date range for realistic patterns
START_DATE="2024-01-01"
END_DATE="2024-12-31"

OUTPUT_DIR="artifacts/models/edge/baseline_diagnostic_v0"

echo "Launching training..."
echo ""

python3 scripts/train_edge_forecaster.py \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --symbol BTCUSDT \
  --output "$OUTPUT_DIR" \
  --epochs 3 \
  --data-pct 0.10 \
  --log-interval 50 \
  --max-grad-norm 1.0 \
  --lr 2e-4 \
  --batch-size 256 \
  --device cuda \
  --amp 1 \
  2>&1 | tee baseline_diagnostic.log

echo ""
echo "=========================================="
echo "ANALYSE AUTOMATIQUE DES RÉSULTATS"
echo "=========================================="

# Extraire les métriques clés du dernier epoch
LAST_EPOCH_SUMMARY=$(grep "EPOCH_SUMMARY_PRODUCTION_GRADE" baseline_diagnostic.log | tail -1)

# Extraire clip_ratio_epoch_pct
CLIP_RATIO=$(echo "$LAST_EPOCH_SUMMARY" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('gradient_summary', {}).get('clip_ratio_epoch_pct', 0.0))
" 2>/dev/null || echo "N/A")

# Extraire pct_saturated
PCT_SATURATED=$(grep "SATURATION_CHECK_EPOCH" baseline_diagnostic.log | tail -1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
sat = data.get('val_return_saturation', {})
above = sat.get('pct_above_clamp_max', 0.0)
below = sat.get('pct_below_clamp_min', 0.0)
print(above + below)
" 2>/dev/null || echo "N/A")

# Extraire amp_scale du dernier batch
AMP_SCALE=$(grep "BATCH_DIAGNOSTIC_PRODUCTION_GRADE" baseline_diagnostic.log | tail -1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('amp_scale', 0.0))
" 2>/dev/null || echo "N/A")

# Extraire val_loss
VAL_LOSS=$(echo "$LAST_EPOCH_SUMMARY" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('val_loss', 0.0))
" 2>/dev/null || echo "N/A")

echo ""
echo "Résumé des métriques clés:"
echo "  - clip_ratio_epoch_pct: $CLIP_RATIO%"
echo "  - pct_saturated: $PCT_SATURATED%"
echo "  - amp_scale (final): $AMP_SCALE"
echo "  - val_loss (epoch 3): $VAL_LOSS"
echo ""

echo "=========================================="
echo "DIAGNOSTIC AUTOMATIQUE"
echo "=========================================="
echo ""

# Diagnostiquer la cause la plus probable
CAUSE_IDENTIFIED=false

# Check CAUSE #1: Grad clip trop bas
if [ "$CLIP_RATIO" != "N/A" ]; then
    if (( $(echo "$CLIP_RATIO > 80.0" | bc -l) )); then
        echo "🔴 CAUSE #1 IDENTIFIÉE: GRAD_CLIP TROP BAS"
        echo ""
        echo "  clip_ratio_epoch_pct = $CLIP_RATIO% (> 80% threshold)"
        echo ""
        echo "  Explication:"
        echo "    - Plus de 80% des steps ont leurs gradients clippés"
        echo "    - Les updates sont constamment limitées à norm=1.0"
        echo "    - Le modèle ne peut pas sortir du minimum local"
        echo ""
        echo "  Recommandation:"
        echo "    RUN SWEEP grad_clip: tester 5.0, 10.0, 1000.0 (disabled)"
        echo ""
        echo "  Commande:"
        echo "    ./run_sweep_gradclip.sh"
        echo ""
        CAUSE_IDENTIFIED=true
    fi
fi

# Check CAUSE #2: Saturation
if [ "$PCT_SATURATED" != "N/A" ]; then
    if (( $(echo "$PCT_SATURATED > 10.0" | bc -l) )); then
        echo "🟠 CAUSE #2 IDENTIFIÉE: TARGET SATURATION"
        echo ""
        echo "  pct_saturated = $PCT_SATURATED% (> 10% threshold)"
        echo ""
        echo "  Explication:"
        echo "    - Plus de 10% des samples ont return_fwd > ±1%"
        echo "    - Ces gros moves sont écrasés à ±1% (clamp)"
        echo "    - Le signal réel est dégradé → modèle apprend sur données tronquées"
        echo ""
        echo "  Recommandation:"
        echo "    Modifier net.py pour élargir clamps: [-2.0, 2.0] ou [-5.0, 5.0]"
        echo ""
        echo "  Fichier: src/pipeline/models/edge/net.py:381"
        echo "    return_fwd = targets[:, 0:1].clamp(-2.0, 2.0)  # Au lieu de -1.0, 1.0"
        echo ""
        CAUSE_IDENTIFIED=true
    fi
fi

# Check CAUSE #3: AMP instable
if [ "$AMP_SCALE" != "N/A" ]; then
    if (( $(echo "$AMP_SCALE < 100.0" | bc -l) )); then
        echo "🟡 CAUSE #3 IDENTIFIÉE: AMP SCALE COLLAPSE"
        echo ""
        echo "  amp_scale = $AMP_SCALE (< 100 threshold)"
        echo ""
        echo "  Explication:"
        echo "    - GradScaler a détecté overflow/underflow répétés"
        echo "    - Scale factor a été réduit pour stabilité"
        echo "    - Gradients effectifs post-unscale sont trop petits"
        echo ""
        echo "  Recommandation:"
        echo "    Tester avec AMP désactivé (FP32 pur)"
        echo ""
        echo "  Commande:"
        echo "    python scripts/train_edge_forecaster.py \\"
        echo "      --start-date $START_DATE --end-date $END_DATE \\"
        echo "      --output artifacts/models/edge/sweep_amp_off \\"
        echo "      --epochs 5 --data-pct 0.10 --log-interval 50 \\"
        echo "      --max-grad-norm 1.0 --device cuda --amp 0"
        echo ""
        CAUSE_IDENTIFIED=true
    fi
fi

if [ "$CAUSE_IDENTIFIED" = false ]; then
    echo "ℹ️  AUCUNE CAUSE ÉVIDENTE DÉTECTÉE"
    echo ""
    echo "  Toutes les métriques sont dans les limites normales:"
    echo "    - clip_ratio < 80%"
    echo "    - pct_saturated < 10%"
    echo "    - amp_scale > 100"
    echo ""
    echo "  Possibilités:"
    echo "    1. Plateau naturel (LR trop bas)"
    echo "    2. Labels bruités ou biaisés"
    echo "    3. Architecture sous-dimensionnée"
    echo ""
    echo "  Recommandation:"
    echo "    - Vérifier val_loss: doit descendre au fil des epochs"
    echo "    - Si val_loss stagne: tester LR plus élevé (4e-4 ou 8e-4)"
    echo "    - Si val_loss descend: continuer training (pas de plateau réel)"
fi

echo ""
echo "=========================================="
echo "LOGS DÉTAILLÉS SAUVEGARDÉS"
echo "=========================================="
echo ""
echo "  baseline_diagnostic.log"
echo ""
echo "Pour analyse manuelle:"
echo "  grep 'gradient_summary' baseline_diagnostic.log | python -m json.tool"
echo "  grep 'SATURATION_CHECK' baseline_diagnostic.log | python -m json.tool"
echo ""
echo "FIN DU DIAGNOSTIC"
