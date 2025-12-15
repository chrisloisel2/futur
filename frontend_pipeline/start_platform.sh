#!/bin/bash

# Alpha Trading Platform - Quick Start Script
# Démarre l'API backend et le frontend React

set -e

# Couleurs
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}   Alpha Trading Platform - Startup${NC}"
echo -e "${BLUE}=========================================${NC}"
echo ""

# Vérifier Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 non trouvé${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python3 trouvé${NC}"

# Vérifier Node.js
if ! command -v node &> /dev/null; then
    echo -e "${RED}❌ Node.js non trouvé${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Node.js trouvé${NC}"

# Vérifier npm
if ! command -v npm &> /dev/null; then
    echo -e "${RED}❌ npm non trouvé${NC}"
    exit 1
fi
echo -e "${GREEN}✓ npm trouvé${NC}"

echo ""
echo -e "${YELLOW}📦 Installation des dépendances frontend si nécessaire...${NC}"
cd frontend/alpha-dashboard

if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}Installation de node_modules...${NC}"
    npm install
    echo -e "${GREEN}✓ Dépendances installées${NC}"
else
    echo -e "${GREEN}✓ Dépendances déjà installées${NC}"
fi

cd ../..

echo ""
echo -e "${YELLOW}🚀 Démarrage des services...${NC}"
echo ""

# Créer des fichiers de log
BACKEND_LOG="/tmp/alpha_backend.log"
FRONTEND_LOG="/tmp/alpha_frontend.log"

# Démarrer le backend en arrière-plan
echo -e "${BLUE}[1/2]${NC} Démarrage du backend API (http://localhost:8000)..."
python3 api_server.py > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
echo -e "${GREEN}✓ Backend démarré (PID: $BACKEND_PID)${NC}"

# Attendre que le backend soit prêt
echo -e "${YELLOW}Attente du backend...${NC}"
sleep 3

# Vérifier que le backend répond
if ! curl -s http://localhost:8000/health > /dev/null; then
    echo -e "${RED}❌ Le backend ne répond pas${NC}"
    echo -e "${YELLOW}Logs backend :${NC}"
    tail -20 "$BACKEND_LOG"
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi
echo -e "${GREEN}✓ Backend opérationnel${NC}"

# Démarrer le frontend en arrière-plan
echo ""
echo -e "${BLUE}[2/2]${NC} Démarrage du frontend React (http://localhost:3000)..."
cd frontend/alpha-dashboard
BROWSER=none npm start > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
cd ../..
echo -e "${GREEN}✓ Frontend démarré (PID: $FRONTEND_PID)${NC}"

# Attendre que le frontend compile
echo -e "${YELLOW}Attente de la compilation...${NC}"
sleep 8

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   ✓ Plateforme démarrée avec succès !${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo -e "${BLUE}📊 Services :${NC}"
echo -e "  • Backend API : ${YELLOW}http://localhost:8000${NC}"
echo -e "  • API Docs    : ${YELLOW}http://localhost:8000/docs${NC}"
echo -e "  • Frontend    : ${YELLOW}http://localhost:3000${NC}"
echo ""
echo -e "${BLUE}📝 Logs :${NC}"
echo -e "  • Backend  : ${YELLOW}$BACKEND_LOG${NC}"
echo -e "  • Frontend : ${YELLOW}$FRONTEND_LOG${NC}"
echo ""
echo -e "${BLUE}🔧 Contrôles :${NC}"
echo -e "  • Arrêter : ${YELLOW}./stop_platform.sh${NC}"
echo -e "  • Logs backend  : ${YELLOW}tail -f $BACKEND_LOG${NC}"
echo -e "  • Logs frontend : ${YELLOW}tail -f $FRONTEND_LOG${NC}"
echo ""

# Sauvegarder les PIDs
echo "$BACKEND_PID" > /tmp/alpha_backend.pid
echo "$FRONTEND_PID" > /tmp/alpha_frontend.pid

# Ouvrir le navigateur
if command -v open &> /dev/null; then
# Ouvrir le navigateur
	sleep 2

	if command -v xdg-open >/dev/null 2>&1; then
		xdg-open http://localhost:3000 >/dev/null 2>&1 &
	elif command -v open >/dev/null 2>&1; then
		open http://localhost:3000 >/dev/null 2>&1 &
	elif command -v start >/dev/null 2>&1; then
		start http://localhost:3000 >/dev/null 2>&1 &
	fi

	echo -e "${GREEN}✓ Ouverture du navigateur déclenchée${NC}"

fi

echo -e "${GREEN}✓ Le navigateur va s'ouvrir automatiquement...${NC}"
echo ""
echo -e "${YELLOW}Appuyez sur Ctrl+C pour arrêter les services${NC}"
echo ""

# Attendre les processus
wait
