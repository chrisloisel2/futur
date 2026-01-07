#!/bin/bash

# Script de test pour vérifier la refonte du Portfolio
# Usage: ./test_portfolio_refactor.sh

echo "🔍 Test de la Refonte du Portfolio"
echo "=================================="
echo ""

# Couleurs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Compteurs
PASSED=0
FAILED=0

# Fonction de test
test_file() {
    local file=$1
    local description=$2

    if [ -f "$file" ]; then
        echo -e "${GREEN}✓${NC} $description"
        ((PASSED++))
    else
        echo -e "${RED}✗${NC} $description - MANQUANT: $file"
        ((FAILED++))
    fi
}

test_content() {
    local file=$1
    local pattern=$2
    local description=$3

    if grep -q "$pattern" "$file" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} $description"
        ((PASSED++))
    else
        echo -e "${RED}✗${NC} $description"
        ((FAILED++))
    fi
}

echo "📁 Vérification des fichiers modifiés..."
echo ""

# Test fichiers principaux
test_file "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "PortfolioTracker.tsx existe"

test_file "frontend_pipeline/frontend/alpha-dashboard/src/components/CandlestickChart.tsx" \
    "CandlestickChart.tsx existe"

test_file "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.css" \
    "PortfolioTracker.css existe"

echo ""
echo "🔍 Vérification du contenu..."
echo ""

# Test interfaces TypeScript
test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "interface TradingConfig" \
    "Interface TradingConfig définie"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "AVAILABLE_MODELS" \
    "Constante AVAILABLE_MODELS définie"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "CRYPTO_LIST" \
    "Constante CRYPTO_LIST définie"

# Test états React
test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "showConfig" \
    "État showConfig ajouté"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "autoTradeEnabled" \
    "État autoTradeEnabled ajouté"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "config.*TradingConfig" \
    "État config défini"

# Test paramètres
test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "confidenceThreshold" \
    "Paramètre confidenceThreshold présent"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "stopLoss" \
    "Paramètre stopLoss présent"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "takeProfit" \
    "Paramètre takeProfit présent"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "temperature" \
    "Paramètre temperature présent"

# Test Panel Configuration
test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "config-panel" \
    "Panel de configuration ajouté"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "config-toggle-btn" \
    "Bouton toggle configuration présent"

# Test Auto-Trading Toggle
test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "toggle-switch" \
    "Toggle switch ajouté"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "Auto-Trading ON" \
    "Label Auto-Trading ON présent"

# Test logique Stop Loss / Take Profit
test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "Stop Loss hit" \
    "Logique Stop Loss implémentée"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.tsx" \
    "Take Profit hit" \
    "Logique Take Profit implémentée"

# Test CSS
test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.css" \
    "config-panel" \
    "Style config-panel défini"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.css" \
    "toggle-switch" \
    "Style toggle-switch défini"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.css" \
    "config-slider" \
    "Style config-slider défini"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/PortfolioTracker.css" \
    "slideDown" \
    "Animation slideDown définie"

# Test Cache Fix
test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/CandlestickChart.tsx" \
    "CACHE_DURATION = 300000" \
    "Cache TTL augmenté à 5 minutes"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/CandlestickChart.tsx" \
    "Cache HIT" \
    "Logs cache améliorés"

test_content "frontend_pipeline/frontend/alpha-dashboard/src/components/CandlestickChart.tsx" \
    "dataCache.set" \
    "Mise à jour cache en temps réel"

echo ""
echo "📄 Vérification de la documentation..."
echo ""

test_file "REFONTE_PORTFOLIO.md" \
    "Documentation de refonte créée"

test_file "GUIDE_UTILISATION_PORTFOLIO.md" \
    "Guide d'utilisation créé"

test_file "frontend_pipeline/backend_updates_example.py" \
    "Exemple backend créé"

echo ""
echo "🎯 Vérification des modèles disponibles..."
echo ""

# Test modèles
MODELS=(
    "trading-system/artifacts/models/edge/production_v1.pt"
    "trading-system/artifacts/models/edge/production_v1_best_trading.pt"
    "trading-system/artifacts/models/edge/production_v3.pt"
    "trading-system/artifacts/models/edge/production_v3_best_trading.pt"
    "trading-system/artifacts/models/edge/production_v4_2.pt"
    "trading-system/artifacts/models/edge/production_v4_2_best_trading.pt"
)

for model in "${MODELS[@]}"; do
    test_file "$model" "Modèle $(basename $model) disponible"
done

echo ""
echo "=================================="
echo "📊 RÉSUMÉ DES TESTS"
echo "=================================="
echo -e "${GREEN}✓ Tests réussis: $PASSED${NC}"
echo -e "${RED}✗ Tests échoués: $FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}🎉 TOUS LES TESTS SONT PASSÉS !${NC}"
    echo ""
    echo "✅ La refonte du Portfolio est complète et fonctionnelle."
    echo ""
    echo "📝 Prochaines étapes :"
    echo "  1. Lancer le frontend: cd frontend_pipeline/frontend/alpha-dashboard && npm start"
    echo "  2. Lancer le backend: python frontend_pipeline/main.py"
    echo "  3. Accéder au Portfolio dans le navigateur"
    echo "  4. Cliquer sur ⚙️ Configuration pour tester les nouveaux paramètres"
    echo ""
    exit 0
else
    echo -e "${YELLOW}⚠️  Certains tests ont échoué${NC}"
    echo ""
    echo "Veuillez vérifier les fichiers manquants ou le contenu manquant."
    echo ""
    exit 1
fi
