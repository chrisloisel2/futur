#!/bin/bash

# Script utilitaire pour lancer les scrapers facilement

# Couleurs pour output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}  Crypto Scrapers Engine${NC}"
echo -e "${BLUE}================================${NC}"
echo ""

# Check if Python is available
if ! command -v python &> /dev/null && ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python not found${NC}"
    exit 1
fi

PYTHON_CMD="python"
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
fi

# Function to show menu
show_menu() {
    echo -e "${GREEN}Que voulez-vous faire ?${NC}"
    echo ""
    echo "1) Lister les scrapers disponibles"
    echo "2) Lancer un scraper spécifique"
    echo "3) Lancer tous les scrapers (parallèle)"
    echo "4) Lancer le scheduler (mode automatique)"
    echo "5) Tester les scrapers"
    echo "6) Voir les dernières données collectées"
    echo "7) Stats des données"
    echo "8) Nettoyer les vieux fichiers (>7 jours)"
    echo "9) Quitter"
    echo ""
    echo -n "Choix [1-9]: "
}

# Function to list spiders
list_spiders() {
    echo -e "${YELLOW}Scrapers disponibles:${NC}"
    $PYTHON_CMD runner.py list
}

# Function to run specific spider
run_spider() {
    echo -e "${YELLOW}Scrapers disponibles:${NC}"
    echo "- whale_alert"
    echo "- arkham"
    echo "- bitcointalk"
    echo "- crypto_news"
    echo "- asian_crypto"
    echo "- specialized_forums"
    echo "- social_sentiment"
    echo ""
    echo -n "Nom du scraper: "
    read spider_name

    echo -e "${GREEN}Lancement de $spider_name...${NC}"
    $PYTHON_CMD runner.py run --spider $spider_name
}

# Function to run all
run_all() {
    echo -e "${GREEN}Lancement de tous les scrapers en parallèle...${NC}"
    $PYTHON_CMD runner.py run-all --parallel
}

# Function to run scheduler
run_scheduler() {
    echo -e "${GREEN}Lancement du scheduler (Ctrl+C pour arrêter)...${NC}"
    $PYTHON_CMD scheduler.py
}

# Function to test
run_tests() {
    echo -e "${GREEN}Lancement des tests...${NC}"
    $PYTHON_CMD test_scrapers.py
}

# Function to view data
view_data() {
    echo -e "${YELLOW}Derniers fichiers de données:${NC}"
    ls -lth data/raw_articles/ | head -10

    echo ""
    latest=$(ls -t data/raw_articles/*.jsonl 2>/dev/null | head -1)
    if [ -n "$latest" ]; then
        echo -e "${YELLOW}Dernier fichier: $(basename $latest)${NC}"
        echo ""
        echo "Voulez-vous voir un échantillon ? (y/n)"
        read -r response
        if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
            head -3 "$latest" | $PYTHON_CMD -m json.tool
        fi
    else
        echo -e "${RED}Aucun fichier de données trouvé${NC}"
    fi
}

# Function to show stats
show_stats() {
    echo -e "${YELLOW}Statistiques des données collectées:${NC}"
    echo ""

    total=0
    if [ -d "data/raw_articles" ]; then
        for file in data/raw_articles/*.jsonl; do
            if [ -f "$file" ]; then
                count=$(wc -l < "$file")
                total=$((total + count))
                echo "$(basename $file): $count articles"
            fi
        done
        echo ""
        echo -e "${GREEN}Total: $total articles${NC}"

        # Disk usage
        du -sh data/raw_articles/ 2>/dev/null
    else
        echo -e "${RED}Aucune donnée trouvée${NC}"
    fi
}

# Function to clean old files
clean_old() {
    echo -e "${YELLOW}Nettoyage des fichiers de plus de 7 jours...${NC}"
    find data/raw_articles/ -name "*.jsonl" -mtime +7 -type f 2>/dev/null | while read file; do
        echo "Suppression: $(basename $file)"
        rm "$file"
    done
    echo -e "${GREEN}Nettoyage terminé${NC}"
}

# Main loop
while true; do
    show_menu
    read choice
    echo ""

    case $choice in
        1)
            list_spiders
            ;;
        2)
            run_spider
            ;;
        3)
            run_all
            ;;
        4)
            run_scheduler
            ;;
        5)
            run_tests
            ;;
        6)
            view_data
            ;;
        7)
            show_stats
            ;;
        8)
            clean_old
            ;;
        9)
            echo -e "${GREEN}Au revoir!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}Choix invalide${NC}"
            ;;
    esac

    echo ""
    echo -e "${BLUE}Appuyez sur Entrée pour continuer...${NC}"
    read
    clear
done
