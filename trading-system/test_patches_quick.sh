#!/bin/bash
# =============================================================================
# TEST RAPIDE DES PATCHES (QUALITÉ PROFESSIONNELLE)
# =============================================================================
# Ce script valide que tous les patches sont appliqués correctement
# en faisant un run ultra-court (1% data, 1 epoch, log toutes les 10 batches)

set -e  # Exit on error

echo "=========================================="
echo "TEST RAPIDE DES PATCHES PROFESSIONNELS"
echo "=========================================="

# CRITICAL: Use recent data pour avoir des patterns réalistes
START_DATE="2024-12-01"
END_DATE="2024-12-31"

echo ""
echo "Configuration:"
echo "  - Data: 1% (ultra-rapide)"
echo "  - Epochs: 1"
echo "  - Log interval: 10 batches"
echo "  - grad_clip: 1.0 (baseline pour diagnostic)"
echo ""
echo "Objectif: Vérifier que les logs contiennent:"
echo "  ✓ grad_pre_clip_norm"
echo "  ✓ grad_was_clipped"
echo "  ✓ grad_clip_ratio"
echo "  ✓ amp_scale"
echo "  ✓ lr_before_step / lr_after_step"
echo "  ✓ gradient_summary (epoch end)"
echo "  ✓ SATURATION_CHECK_EPOCH"
echo ""

# Run avec logging complet
python3 scripts/train_edge_forecaster.py \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --symbol BTCUSDT \
  --output artifacts/models/edge/test_patches_quick \
  --epochs 1 \
  --data-pct 0.01 \
  --log-interval 10 \
  --max-grad-norm 1.0 \
  --lr 2e-4 \
  --batch-size 128 \
  --device cuda \
  --amp 1 \
  2>&1 | tee test_patches_output.log

echo ""
echo "=========================================="
echo "VALIDATION DES LOGS"
echo "=========================================="

# Vérifier que les métriques critiques sont présentes
echo ""
echo "Checking gradient metrics..."
if grep -q "grad_pre_clip_norm" test_patches_output.log; then
    echo "✓ grad_pre_clip_norm logged"
else
    echo "✗ grad_pre_clip_norm MISSING"
    exit 1
fi

if grep -q "grad_was_clipped" test_patches_output.log; then
    echo "✓ grad_was_clipped logged"
else
    echo "✗ grad_was_clipped MISSING"
    exit 1
fi

if grep -q "grad_clip_ratio" test_patches_output.log; then
    echo "✓ grad_clip_ratio logged"
else
    echo "✗ grad_clip_ratio MISSING"
    exit 1
fi

echo ""
echo "Checking AMP scale..."
if grep -q "amp_scale" test_patches_output.log; then
    echo "✓ amp_scale logged"
else
    echo "✗ amp_scale MISSING"
    exit 1
fi

echo ""
echo "Checking LR metrics..."
if grep -q "lr_before_step" test_patches_output.log; then
    echo "✓ lr_before_step logged"
else
    echo "✗ lr_before_step MISSING"
    exit 1
fi

if grep -q "lr_after_step" test_patches_output.log; then
    echo "✓ lr_after_step logged"
else
    echo "✗ lr_after_step MISSING"
    exit 1
fi

echo ""
echo "Checking epoch gradient summary..."
if grep -q "gradient_summary" test_patches_output.log; then
    echo "✓ gradient_summary logged"
else
    echo "✗ gradient_summary MISSING"
    exit 1
fi

if grep -q "clip_ratio_epoch_pct" test_patches_output.log; then
    echo "✓ clip_ratio_epoch_pct logged"
else
    echo "✗ clip_ratio_epoch_pct MISSING"
    exit 1
fi

echo ""
echo "Checking saturation check..."
if grep -q "SATURATION_CHECK_EPOCH" test_patches_output.log; then
    echo "✓ SATURATION_CHECK_EPOCH logged"
else
    echo "✗ SATURATION_CHECK_EPOCH MISSING"
    exit 1
fi

echo ""
echo "=========================================="
echo "✅ TOUS LES PATCHES VALIDÉS"
echo "=========================================="
echo ""
echo "Le trainer est maintenant PRODUCTION-GRADE avec:"
echo "  ✓ Gradient logging complet (pre-clip, was_clipped, clip_ratio)"
echo "  ✓ AMP scale monitoring"
echo "  ✓ LR tracking précis (before/after step)"
echo "  ✓ Saturation detection automatique"
echo "  ✓ Epoch gradient summary"
echo ""
echo "Prochaine étape: Analyser les logs pour identifier la cause du plateau"
echo ""
echo "Commandes suggérées:"
echo "  1. BASELINE (3 epochs, 10% data):"
echo "     python scripts/train_edge_forecaster.py \\"
echo "       --start-date 2024-01-01 --end-date 2024-12-31 \\"
echo "       --output artifacts/models/edge/baseline_diagnostic \\"
echo "       --epochs 3 --data-pct 0.10 --log-interval 50 \\"
echo "       --max-grad-norm 1.0 --device cuda"
echo ""
echo "  2. Si clip_ratio_epoch_pct > 80% → TEST grad_clip=5.0:"
echo "     python scripts/train_edge_forecaster.py \\"
echo "       --start-date 2024-01-01 --end-date 2024-12-31 \\"
echo "       --output artifacts/models/edge/sweep_gc_5 \\"
echo "       --epochs 5 --data-pct 0.10 --log-interval 50 \\"
echo "       --max-grad-norm 5.0 --device cuda"
echo ""
echo "  3. Si pct_saturated > 10% → Modifier net.py (clamps plus larges)"
echo ""

# Afficher un extrait des logs pour vérification manuelle
echo "=========================================="
echo "EXTRAIT DES LOGS (BATCH DIAGNOSTIC)"
echo "=========================================="
grep "BATCH_DIAGNOSTIC_PRODUCTION_GRADE" test_patches_output.log | head -2 | python3 -m json.tool

echo ""
echo "=========================================="
echo "EXTRAIT DES LOGS (EPOCH SUMMARY)"
echo "=========================================="
grep "EPOCH_SUMMARY_PRODUCTION_GRADE" test_patches_output.log | head -1 | python3 -m json.tool

echo ""
echo "=========================================="
echo "EXTRAIT DES LOGS (SATURATION CHECK)"
echo "=========================================="
grep "SATURATION_CHECK_EPOCH" test_patches_output.log | head -1 | python3 -m json.tool

echo ""
echo "FIN DU TEST"
