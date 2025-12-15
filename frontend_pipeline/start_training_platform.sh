#!/bin/bash

# ============================================================================
# Script de Démarrage de la Plateforme de Training
# ============================================================================

echo "🎓 =========================================="
echo "🎓  ALPHA TRADING - TRAINING PLATFORM"
echo "🎓 =========================================="
echo ""

# Couleurs pour l'output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Vérifier si le script est exécuté depuis le bon dossier
if [ ! -f "api_server.py" ]; then
    echo -e "${RED}❌ Erreur: Ce script doit être exécuté depuis frontend_pipeline/${NC}"
    echo -e "${YELLOW}📁 cd /Users/christopher/Desktop/futur/frontend_pipeline${NC}"
    exit 1
fi

# Fonction pour arrêter proprement les processus
cleanup() {
    echo ""
    echo -e "${YELLOW}🛑 Arrêt de la plateforme...${NC}"

    if [ ! -z "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null
        echo -e "${GREEN}✅ Backend arrêté${NC}"
    fi

    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null
        echo -e "${GREEN}✅ Frontend arrêté${NC}"
    fi

    echo -e "${GREEN}👋 À bientôt !${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Vérifier Python
echo -e "${BLUE}🔍 Vérification de Python...${NC}"
if ! command -v python &> /dev/null && ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python n'est pas installé${NC}"
    exit 1
fi

PYTHON_CMD=$(command -v python3 || command -v python)
echo -e "${GREEN}✅ Python trouvé: $PYTHON_CMD${NC}"

# Vérifier Node/NPM
echo -e "${BLUE}🔍 Vérification de Node.js...${NC}"
if ! command -v npm &> /dev/null; then
    echo -e "${RED}❌ Node.js/NPM n'est pas installé${NC}"
    exit 1
fi
echo -e "${GREEN}✅ NPM trouvé: $(npm --version)${NC}"

# Créer les dossiers nécessaires
echo -e "${BLUE}📁 Création des dossiers nécessaires...${NC}"
mkdir -p ../ai/checkpoints_light
mkdir -p /tmp
echo -e "${GREEN}✅ Dossiers créés${NC}"

# Démarrer le backend
echo ""
echo -e "${BLUE}🚀 Démarrage du Backend API...${NC}"
$PYTHON_CMD api_server.py > /tmp/training_platform_backend.log 2>&1 &
BACKEND_PID=$!

# Attendre que le backend démarre
echo -e "${YELLOW}⏳ Attente du démarrage du backend...${NC}"
sleep 3

# Vérifier que le backend est bien démarré
if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Le backend met du temps à démarrer, vérification dans 5s...${NC}"
    sleep 5

    if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${RED}❌ Le backend n'a pas pu démarrer${NC}"
        echo -e "${YELLOW}📋 Logs du backend:${NC}"
        tail -20 /tmp/training_platform_backend.log
        kill $BACKEND_PID 2>/dev/null
        exit 1
    fi
fi

echo -e "${GREEN}✅ Backend démarré sur http://localhost:8000${NC}"

# Démarrer le frontend
echo ""
echo -e "${BLUE}🚀 Démarrage du Frontend React...${NC}"
cd frontend/alpha-dashboard

# Vérifier si node_modules existe
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}📦 Installation des dépendances NPM (première fois)...${NC}"
    npm install
fi

# Démarrer le frontend
BROWSER=none npm start > /tmp/training_platform_frontend.log 2>&1 &
FRONTEND_PID=$!

cd ../..

echo -e "${YELLOW}⏳ Attente du démarrage du frontend...${NC}"
sleep 8

# Vérifier que le frontend est bien démarré
if ! curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Le frontend met du temps à démarrer...${NC}"
fi

echo ""
echo -e "${GREEN}✅✅✅ PLATEFORME DÉMARRÉE AVEC SUCCÈS ! ✅✅✅${NC}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BLUE}📊 Backend API:${NC}      http://localhost:8000"
echo -e "${BLUE}📊 API Docs:${NC}         http://localhost:8000/docs"
echo -e "${BLUE}🎨 Frontend:${NC}         http://localhost:3000"
echo -e "${BLUE}🎓 Training Tab:${NC}     Cliquez sur l'onglet 'Training'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "${YELLOW}📋 Logs Backend:${NC}  tail -f /tmp/training_platform_backend.log"
echo -e "${YELLOW}📋 Logs Frontend:${NC} tail -f /tmp/training_platform_frontend.log"
echo ""
echo -e "${RED}🛑 Pour arrêter: Ctrl+C${NC}"
echo ""

# Ouvrir le navigateur automatiquement (macOS)
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo -e "${BLUE}🌐 Ouverture du navigateur...${NC}"
    sleep 2
    open http://localhost:3000
fi

# Garder le script en vie et afficher les logs
echo -e "${BLUE}📊 Plateforme en cours d'exécution...${NC}"
echo ""

# Suivre les logs en temps réel
tail -f /tmp/training_platform_backend.log &
TAIL_PID=$!

# Attendre indéfiniment
wait $BACKEND_PID $FRONTEND_PID
