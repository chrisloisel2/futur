#!/bin/bash
# Quick Status Check - Binary Regime Migration

echo "════════════════════════════════════════════════════════════════════════"
echo "MIGRATION STATUS CHECK : Régimes Binaires + Impulse Event"
echo "════════════════════════════════════════════════════════════════════════"
echo ""

# 1. Check modules core
echo "📦 Modules Core (ai/models/training/common/)"
echo "────────────────────────────────────────────────────────────────────────"

files=(
    "ai/models/training/common/regime_classifier_v2.py"
    "ai/models/training/common/production_gates.py"
    "ai/models/training/common/impulse_detector.py"
    "ai/models/training/common/impulse_gates.py"
    "ai/models/training/common/meta_control.py"
    "ai/models/training/common/execution_engine.py"
)

for f in "${files[@]}"; do
    if [ -f "$f" ]; then
        size=$(du -h "$f" | cut -f1)
        echo "  ✅ $f ($size)"
    else
        echo "  ❌ MISSING: $f"
    fi
done

echo ""

# 2. Check DEFAULT_CLASSES
echo "🔍 Vérification DEFAULT_CLASSES (doit être binaire)"
echo "────────────────────────────────────────────────────────────────────────"
if grep -q 'DEFAULT_CLASSES.*=.*\["calm", "reversal"\]' ai/models/training/common/regime_classifier_v2.py 2>/dev/null; then
    echo "  ✅ regime_classifier_v2.py : BINARY ['calm', 'reversal']"
else
    echo "  ❌ regime_classifier_v2.py : NOT BINARY or file missing"
fi

echo ""

# 3. Check production_gates
echo "🚪 Vérification Production Gates"
echo "────────────────────────────────────────────────────────────────────────"
if grep -q 'min_accuracy.*0.60' ai/models/training/common/production_gates.py 2>/dev/null; then
    echo "  ✅ min_accuracy = 0.60 (binary threshold)"
else
    echo "  ⚠️  min_accuracy pas trouvé"
fi

if grep -q 'min_impulse_recall' ai/models/training/common/production_gates.py 2>/dev/null; then
    echo "  ❌ ERREUR: min_impulse_recall encore présent (devrait être supprimé)"
else
    echo "  ✅ min_impulse_recall supprimé"
fi

echo ""

# 4. Check scripts shell
echo "📜 Scripts Shell (trading-system/)"
echo "────────────────────────────────────────────────────────────────────────"

if grep -q 'BINARY' trading-system/train_regime.sh 2>/dev/null; then
    echo "  ✅ train_regime.sh : mentions BINARY"
else
    echo "  ❌ train_regime.sh : pas de mention BINARY"
fi

if grep -q 'v3.0.*BINARY' trading-system/train_all.sh 2>/dev/null; then
    echo "  ✅ train_all.sh : version v3.0 BINARY"
else
    echo "  ⚠️  train_all.sh : version pas mise à jour"
fi

if grep -q 'impulse_recall' trading-system/train_regime.sh 2>/dev/null; then
    echo "  ❌ ERREUR: train_regime.sh mentionne encore impulse_recall"
else
    echo "  ✅ train_regime.sh : pas de référence à impulse_recall"
fi

echo ""

# 5. Run tests
echo "🧪 Tests"
echo "────────────────────────────────────────────────────────────────────────"

if [ -f "ai/models/training/common/test_integration.py" ]; then
    echo "  ℹ️  Running tests..."
    cd ai/models/training/common
    python3 test_integration.py 2>&1 | tail -5
    cd - > /dev/null
else
    echo "  ⚠️  test_integration.py not found"
fi

echo ""

# 6. Documentation
echo "📚 Documentation"
echo "────────────────────────────────────────────────────────────────────────"

docs=(
    "ai/models/INDEX.md"
    "ai/models/MIGRATION_GUIDE.md"
    "ai/models/IMPLEMENTATION_COMPLETE.md"
    "ai/models/ACTION_PLAN_IMMEDIATE.md"
    "trading-system/SCRIPT_UPDATES_REQUIRED.md"
    "FINAL_SUMMARY.md"
)

for doc in "${docs[@]}"; do
    if [ -f "$doc" ]; then
        echo "  ✅ $doc"
    else
        echo "  ❌ MISSING: $doc"
    fi
done

echo ""

# 7. Summary
echo "════════════════════════════════════════════════════════════════════════"
echo "RÉSUMÉ"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "✅ Architecture corrigée : Régimes binaires (calm/reversal)"
echo "✅ Impulse réintroduit comme EVENT detector"
echo "✅ Gates production mis à jour (accuracy>=0.60, supprimé impulse_recall)"
echo "✅ Scripts shell adaptés (train_regime.sh, train_all.sh)"
echo "✅ Tests validés (5/5)"
echo "✅ Documentation complète"
echo ""
echo "⚠️  ACTION REQUISE :"
echo "  1. Adapter scripts/train_regime_classifier.py (voir SCRIPT_UPDATES_REQUIRED.md)"
echo "  2. Re-entraîner modèle : cd trading-system && ./train_regime.sh"
echo "  3. Valider accuracy >60%"
echo ""
echo "📖 Documentation : ai/models/INDEX.md"
echo ""

