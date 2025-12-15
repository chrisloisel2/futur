#!/bin/bash

# Alpha Trading Platform - Stop Script

# Couleurs
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}   Alpha Trading Platform - Arrêt${NC}"
echo -e "${BLUE}=========================================${NC}"
echo ""

# Arrêter les processus sauvegardés
if [ -f /tmp/alpha_backend.pid ]; then
    BACKEND_PID=$(cat /tmp/alpha_backend.pid)
    if ps -p $BACKEND_PID > /dev/null 2>&1; then
        echo -e "${YELLOW}Arrêt du backend (PID: $BACKEND_PID)...${NC}"
        kill $BACKEND_PID
        echo -e "${GREEN}✓ Backend arrêté${NC}"
    fi
    rm /tmp/alpha_backend.pid
fi

if [ -f /tmp/alpha_frontend.pid ]; then
    FRONTEND_PID=$(cat /tmp/alpha_frontend.pid)
    if ps -p $FRONTEND_PID > /dev/null 2>&1; then
        echo -e "${YELLOW}Arrêt du frontend (PID: $FRONTEND_PID)...${NC}"
        kill $FRONTEND_PID
        echo -e "${GREEN}✓ Frontend arrêté${NC}"
    fi
    rm /tmp/alpha_frontend.pid
fi

# Nettoyer les processus restants
echo -e "${YELLOW}Nettoyage des processus restants...${NC}"

# Tuer tous les processus Python api_server.py
pkill -f "python.*api_server.py" 2>/dev/null

# Tuer tous les processus npm start dans alpha-dashboard
pkill -f "node.*alpha-dashboard" 2>/dev/null

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   ✓ Plateforme arrêtée${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
