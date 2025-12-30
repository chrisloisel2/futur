#!/bin/bash
# Verification Script: Binary Regime Architecture
# Date: 2025-12-29

set -e

echo "════════════════════════════════════════════════════════════════════════════"
echo "VÉRIFICATION : Configuration Régimes Binaires"
echo "════════════════════════════════════════════════════════════════════════════"
echo ""

ERRORS=0

# 1. Check binary training script exists
echo "1️⃣  Vérification script training binaire..."
if [ -f "trading-system/scripts/train_regime_classifier_binary.py" ]; then
    echo "   ✅ train_regime_classifier_binary.py trouvé"
else
    echo "   ❌ train_regime_classifier_binary.py MANQUANT"
    ERRORS=$((ERRORS+1))
fi

# 2. Check shell script calls binary version
echo ""
echo "2️⃣  Vérification train_regime.sh..."
if grep -q "train_regime_classifier_binary.py" trading-system/train_regime.sh; then
    echo "   ✅ train_regime.sh appelle train_regime_classifier_binary.py"
else
    echo "   ❌ train_regime.sh n'appelle PAS le script binaire"
    ERRORS=$((ERRORS+1))
fi

# 3. Check regime_classifier_v2.py has binary classes
echo ""
echo "3️⃣  Vérification regime_classifier_v2.py..."
if grep -q 'DEFAULT_CLASSES = \["calm", "reversal"\]' ai/models/training/common/regime_classifier_v2.py; then
    echo "   ✅ regime_classifier_v2.py utilise classes BINAIRES"
else
    echo "   ❌ regime_classifier_v2.py N'utilise PAS classes binaires"
    ERRORS=$((ERRORS+1))
fi

# 4. Check production gates are binary
echo ""
echo "4️⃣  Vérification production_gates.py..."
if grep -q "min_accuracy: float = 0.60" ai/models/training/common/production_gates.py; then
    echo "   ✅ production_gates.py a min_accuracy binaire (0.60)"
else
    echo "   ❌ production_gates.py N'a PAS min_accuracy binaire"
    ERRORS=$((ERRORS+1))
fi

if grep -q "min_impulse_recall:" ai/models/training/common/production_gates.py; then
    echo "   ❌ production_gates.py contient ENCORE min_impulse_recall comme paramètre"
    ERRORS=$((ERRORS+1))
else
    echo "   ✅ production_gates.py N'a PLUS min_impulse_recall comme paramètre"
fi

# 5. Check impulse detector exists
echo ""
echo "5️⃣  Vérification impulse_detector.py..."
if [ -f "ai/models/training/common/impulse_detector.py" ]; then
    echo "   ✅ impulse_detector.py trouvé (event detector)"
else
    echo "   ❌ impulse_detector.py MANQUANT"
    ERRORS=$((ERRORS+1))
fi

# 6. Check impulse gates exist
echo ""
echo "6️⃣  Vérification impulse_gates.py..."
if [ -f "ai/models/training/common/impulse_gates.py" ]; then
    echo "   ✅ impulse_gates.py trouvé (event gates)"
else
    echo "   ❌ impulse_gates.py MANQUANT"
    ERRORS=$((ERRORS+1))
fi

# 7. Check meta_control exists
echo ""
echo "7️⃣  Vérification meta_control.py..."
if [ -f "ai/models/training/common/meta_control.py" ]; then
    echo "   ✅ meta_control.py trouvé (position sizing)"
else
    echo "   ❌ meta_control.py MANQUANT"
    ERRORS=$((ERRORS+1))
fi

# 8. Check execution_engine exists
echo ""
echo "8️⃣  Vérification execution_engine.py..."
if [ -f "ai/models/training/common/execution_engine.py" ]; then
    echo "   ✅ execution_engine.py trouvé (MAKER/TAKER routing)"
else
    echo "   ❌ execution_engine.py MANQUANT"
    ERRORS=$((ERRORS+1))
fi

# 9. Check test suite exists
echo ""
echo "9️⃣  Vérification test_integration.py..."
if [ -f "ai/models/training/common/test_integration.py" ]; then
    echo "   ✅ test_integration.py trouvé"
else
    echo "   ❌ test_integration.py MANQUANT"
    ERRORS=$((ERRORS+1))
fi

# 10. Check script imports corrected modules
echo ""
echo "🔟 Vérification imports dans train_regime_classifier_binary.py..."
if grep -q "from regime_classifier_v2 import" trading-system/scripts/train_regime_classifier_binary.py; then
    echo "   ✅ Script importe regime_classifier_v2"
else
    echo "   ❌ Script N'importe PAS regime_classifier_v2"
    ERRORS=$((ERRORS+1))
fi

if grep -q "from production_gates import RegimeClassifierGates" trading-system/scripts/train_regime_classifier_binary.py; then
    echo "   ✅ Script importe RegimeClassifierGates"
else
    echo "   ❌ Script N'importe PAS RegimeClassifierGates"
    ERRORS=$((ERRORS+1))
fi

# 11. Check documentation exists
echo ""
echo "1️⃣1️⃣ Vérification documentation..."
DOC_COUNT=0
for doc in MIGRATION_GUIDE.md IMPLEMENTATION_COMPLETE.md INDEX.md FINAL_SUMMARY.md FIX_COMPLETE.md; do
    if [ -f "ai/models/$doc" ] || [ -f "$doc" ]; then
        DOC_COUNT=$((DOC_COUNT+1))
    fi
done
if [ $DOC_COUNT -ge 3 ]; then
    echo "   ✅ Documentation trouvée ($DOC_COUNT fichiers)"
else
    echo "   ⚠️  Documentation limitée ($DOC_COUNT fichiers)"
fi

# Summary
echo ""
echo "════════════════════════════════════════════════════════════════════════════"
if [ $ERRORS -eq 0 ]; then
    echo "✅ VÉRIFICATION COMPLÈTE : Tous les composants sont prêts!"
    echo ""
    echo "Prochaine étape : Training binaire"
    echo "  cd trading-system"
    echo "  ./train_regime.sh"
    echo ""
    echo "Métriques attendues :"
    echo "  - Accuracy: >60% (vs 46% avant)"
    echo "  - Calm recall: >50%"
    echo "  - Reversal recall: >50%"
    echo "  - ECE: <0.10"
else
    echo "❌ VÉRIFICATION ÉCHOUÉE : $ERRORS erreur(s) trouvée(s)"
    echo ""
    echo "Veuillez corriger les erreurs ci-dessus avant de continuer."
    exit 1
fi
echo "════════════════════════════════════════════════════════════════════════════"
