#!/bin/bash

# Script de démarrage de la plateforme de Portfolio Trading
# Démarre l'API server et le frontend React

set -e

echo "======================================================================"
echo "🚀 DÉMARRAGE PLATEFORME PORTFOLIO TRADING"
echo "======================================================================"
echo ""

# Vérifier qu'on est dans le bon répertoire
if [ ! -f "api_server.py" ]; then
    echo "❌ Erreur: api_server.py non trouvé"
    echo "Veuillez exécuter ce script depuis frontend_pipeline/"
    exit 1
fi

# Fonction pour tuer les processus au Ctrl+C
cleanup() {
    echo ""
    echo "🛑 Arrêt de la plateforme..."
    kill $API_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    echo "✅ Plateforme arrêtée"
    exit 0
}

trap cleanup INT TERM

# 1. Démarrer l'API Server
echo "📡 Démarrage de l'API Server..."
python api_server.py > api_server.log 2>&1 &
API_PID=$!
echo "   → API Server PID: $API_PID"
sleep 3

# Vérifier que l'API est démarrée
if ! kill -0 $API_PID 2>/dev/null; then
    echo "❌ Erreur: L'API Server n'a pas démarré"
    echo "Voir api_server.log pour les détails"
    exit 1
fi

# 2. Démarrer le Frontend React
echo ""
echo "🎨 Démarrage du Frontend React..."
cd frontend/alpha-dashboard

# Vérifier que node_modules existe
if [ ! -d "node_modules" ]; then
    echo "📦 Installation des dépendances npm..."
    npm install
fi

npm start > ../../frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   → Frontend PID: $FRONTEND_PID"

# Retourner au répertoire racine
cd ../..

echo ""
echo "======================================================================"
echo "✅ PLATEFORME DÉMARRÉE AVEC SUCCÈS"
echo "======================================================================"
echo ""
echo "📊 Services disponibles:"
echo ""
echo "   🔌 API Server:        http://localhost:8000"
echo "   📖 API Docs:          http://localhost:8000/docs"
echo "   🌐 Frontend:          http://localhost:3000"
echo ""
echo "======================================================================"
echo "📂 Onglets disponibles dans le frontend:"
echo ""
echo "   📊 Dashboard          - Vue d'ensemble du marché"
echo "   💼 Portfolio          - Simulateur de trading avec capital fictif"
echo "   🧠 AI Metrics         - Métriques et explainability du modèle"
echo "   🤖 Predictions        - Prédictions temps réel de l'IA"
echo "   🗂️  Data Explorer     - Exploration des datasets S3"
echo ""
echo "======================================================================"
echo "💡 Fonctionnalités Portfolio:"
echo ""
echo "   • Capital initial: \$10,000 (fictif)"
echo "   • Auto-trading piloté par l'IA"
echo "   • Simulation temps réel des prix"
echo "   • Calcul P&L en direct"
echo "   • Win Rate, Sharpe Ratio, Max Drawdown"
echo ""
echo "======================================================================"
echo "🧠 AI Metrics Dashboard:"
echo ""
echo "   • Performance du modèle (Accuracy, Precision, Recall)"
echo "   • Feature Importance Analysis"
echo "   • Explications détaillées des décisions"
echo "   • Architecture du Transformer"
echo ""
echo "======================================================================"
echo "📋 Logs:"
echo ""
echo "   • API Server:  tail -f api_server.log"
echo "   • Frontend:    tail -f frontend.log"
echo ""
echo "======================================================================"
echo ""
echo "Appuyez sur Ctrl+C pour arrêter la plateforme"
echo ""

# Attendre indéfiniment
wait $API_PID $FRONTEND_PID
