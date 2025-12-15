#!/bin/bash
# Script pour télécharger les modèles entraînés depuis S3
# Usage: ./scripts/download_models_from_s3.sh [options]

set -e

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
S3_BUCKET=${S3_BUCKET:-"qbia"}
S3_MODELS_PREFIX="models/trading"
DOWNLOAD_DIR="./downloaded_models"

echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         S3 Model Downloader - Trading ML               ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

# Vérifier les prérequis
if ! command -v aws &> /dev/null; then
    echo -e "${RED}❌ AWS CLI non installé. Installez-le avec: brew install awscli${NC}"
    exit 1
fi

if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}❌ AWS CLI non configuré. Exécutez: aws configure${NC}"
    exit 1
fi

# Fonction pour lister tous les modèles disponibles
list_models() {
    echo -e "${YELLOW}📋 Modèles disponibles dans s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/${NC}"
    echo "─────────────────────────────────────────────────────"

    # Récupérer la liste des dossiers (un par training)
    MODELS=$(aws s3 ls s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/ | grep "PRE" | awk '{print $2}' | sed 's/\///g')

    if [ -z "$MODELS" ]; then
        echo -e "${YELLOW}⚠️  Aucun modèle trouvé${NC}"
        return 1
    fi

    # Afficher chaque modèle avec ses détails
    i=1
    declare -A MODEL_ARRAY

    for MODEL in $MODELS; do
        # Récupérer la taille et le nombre de fichiers
        SIZE=$(aws s3 ls s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/${MODEL}/ --recursive --summarize | grep "Total Size" | awk '{print $3}')
        SIZE_MB=$(echo "scale=2; $SIZE / 1024 / 1024" | bc 2>/dev/null || echo "N/A")

        FILE_COUNT=$(aws s3 ls s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/${MODEL}/ --recursive | wc -l)

        # Extraire le config et le timestamp du nom
        CONFIG_NAME=$(echo "$MODEL" | sed 's/_[0-9]*_[0-9]*$//')
        TIMESTAMP=$(echo "$MODEL" | grep -oE '[0-9]{8}_[0-9]{6}$' || echo "N/A")

        # Formater le timestamp
        if [ "$TIMESTAMP" != "N/A" ]; then
            DATE_PART=$(echo "$TIMESTAMP" | cut -d'_' -f1)
            TIME_PART=$(echo "$TIMESTAMP" | cut -d'_' -f2)
            FORMATTED_DATE="${DATE_PART:0:4}-${DATE_PART:4:2}-${DATE_PART:6:2}"
            FORMATTED_TIME="${TIME_PART:0:2}:${TIME_PART:2:2}:${TIME_PART:4:2}"
            DISPLAY_TIME="${FORMATTED_DATE} ${FORMATTED_TIME}"
        else
            DISPLAY_TIME="N/A"
        fi

        MODEL_ARRAY[$i]=$MODEL

        echo -e "${GREEN}[$i]${NC} ${CYAN}${MODEL}${NC}"
        echo "    Config: ${CONFIG_NAME}"
        echo "    Date: ${DISPLAY_TIME}"
        echo "    Taille: ${SIZE_MB} MB (${FILE_COUNT} fichiers)"
        echo ""

        i=$((i+1))
    done

    # Exporter le tableau pour l'utiliser dans d'autres fonctions
    for key in "${!MODEL_ARRAY[@]}"; do
        echo "$key:${MODEL_ARRAY[$key]}"
    done > /tmp/model_array.txt
}

# Fonction pour télécharger un modèle spécifique
download_model() {
    local MODEL_NAME=$1
    local TARGET_DIR="${DOWNLOAD_DIR}/${MODEL_NAME}"

    echo -e "${YELLOW}📥 Téléchargement du modèle: ${MODEL_NAME}${NC}"
    echo "─────────────────────────────────────────────────────"

    # Créer le répertoire de destination
    mkdir -p "$TARGET_DIR"

    # Télécharger tous les fichiers
    echo -e "${CYAN}Téléchargement depuis s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/${MODEL_NAME}/${NC}"

    aws s3 sync \
        "s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/${MODEL_NAME}/" \
        "$TARGET_DIR" \
        --no-progress

    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}✅ Modèle téléchargé avec succès !${NC}"
        echo -e "${GREEN}📂 Emplacement: ${TARGET_DIR}${NC}"

        # Afficher le contenu
        echo ""
        echo -e "${YELLOW}📋 Contenu du modèle:${NC}"
        ls -lh "$TARGET_DIR"

        # Chercher le fichier de log s'il existe
        if [ -f "$TARGET_DIR/training.log" ]; then
            echo ""
            echo -e "${YELLOW}📜 Dernières lignes du log d'entraînement:${NC}"
            tail -n 20 "$TARGET_DIR/training.log"
        fi

        return 0
    else
        echo -e "${RED}❌ Erreur lors du téléchargement${NC}"
        return 1
    fi
}

# Fonction pour télécharger le dernier modèle
download_latest() {
    echo -e "${YELLOW}🔍 Recherche du dernier modèle...${NC}"

    # Récupérer le dernier modèle (tri par date)
    LATEST_MODEL=$(aws s3 ls s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/ | grep "PRE" | awk '{print $2}' | sed 's/\///g' | sort -r | head -n 1)

    if [ -z "$LATEST_MODEL" ]; then
        echo -e "${RED}❌ Aucun modèle trouvé${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ Dernier modèle trouvé: ${LATEST_MODEL}${NC}"
    echo ""

    download_model "$LATEST_MODEL"
}

# Fonction pour comparer les modèles
compare_models() {
    echo -e "${YELLOW}📊 Comparaison des modèles disponibles${NC}"
    echo "─────────────────────────────────────────────────────"

    MODELS=$(aws s3 ls s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/ | grep "PRE" | awk '{print $2}' | sed 's/\///g' | sort -r)

    if [ -z "$MODELS" ]; then
        echo -e "${YELLOW}⚠️  Aucun modèle trouvé${NC}"
        return 1
    fi

    printf "%-40s %-20s %-15s %-10s\n" "Modèle" "Date" "Taille (MB)" "Fichiers"
    echo "──────────────────────────────────────────────────────────────────────────────────"

    for MODEL in $MODELS; do
        SIZE=$(aws s3 ls s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/${MODEL}/ --recursive --summarize 2>/dev/null | grep "Total Size" | awk '{print $3}')
        SIZE_MB=$(echo "scale=2; $SIZE / 1024 / 1024" | bc 2>/dev/null || echo "N/A")

        FILE_COUNT=$(aws s3 ls s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/${MODEL}/ --recursive 2>/dev/null | wc -l)

        TIMESTAMP=$(echo "$MODEL" | grep -oE '[0-9]{8}_[0-9]{6}$' || echo "N/A")
        if [ "$TIMESTAMP" != "N/A" ]; then
            DATE_PART=$(echo "$TIMESTAMP" | cut -d'_' -f1)
            FORMATTED_DATE="${DATE_PART:0:4}-${DATE_PART:4:2}-${DATE_PART:6:2}"
        else
            FORMATTED_DATE="N/A"
        fi

        printf "%-40s %-20s %-15s %-10s\n" "$MODEL" "$FORMATTED_DATE" "$SIZE_MB" "$FILE_COUNT"
    done

    echo ""
}

# Menu interactif
show_menu() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                   Options                              ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "  1) Lister tous les modèles disponibles"
    echo "  2) Télécharger le dernier modèle"
    echo "  3) Télécharger un modèle spécifique"
    echo "  4) Comparer tous les modèles"
    echo "  5) Ouvrir le dossier de téléchargement"
    echo "  q) Quitter"
    echo ""
}

# Parse arguments
case "${1:-}" in
    --latest|-l)
        download_latest
        exit 0
        ;;
    --list)
        list_models
        exit 0
        ;;
    --compare|-c)
        compare_models
        exit 0
        ;;
    --model|-m)
        if [ -z "$2" ]; then
            echo -e "${RED}❌ Spécifiez le nom du modèle${NC}"
            echo "Usage: $0 --model MODEL_NAME"
            exit 1
        fi
        download_model "$2"
        exit 0
        ;;
    --help|-h)
        echo "Usage: $0 [options]"
        echo ""
        echo "Options:"
        echo "  --latest, -l          Télécharger le dernier modèle"
        echo "  --list                Lister tous les modèles"
        echo "  --compare, -c         Comparer tous les modèles"
        echo "  --model, -m NAME      Télécharger un modèle spécifique"
        echo "  --help, -h            Afficher cette aide"
        echo ""
        echo "Exemples:"
        echo "  $0 --latest"
        echo "  $0 --model train_s3_optimized_20241214_153045"
        echo "  $0 --compare"
        exit 0
        ;;
esac

# Mode interactif
while true; do
    show_menu
    read -p "Choix: " choice

    case $choice in
        1)
            list_models
            echo ""
            read -p "Appuyez sur Entrée pour continuer..."
            clear
            ;;
        2)
            download_latest
            echo ""
            read -p "Appuyez sur Entrée pour continuer..."
            clear
            ;;
        3)
            # Lister les modèles et demander le choix
            list_models > /tmp/models_list.txt

            # Charger le tableau
            declare -A MODEL_ARRAY
            while IFS=: read -r key value; do
                MODEL_ARRAY[$key]=$value
            done < /tmp/model_array.txt

            echo ""
            read -p "Entrez le numéro du modèle à télécharger: " model_num

            if [ -n "${MODEL_ARRAY[$model_num]}" ]; then
                download_model "${MODEL_ARRAY[$model_num]}"
            else
                echo -e "${RED}❌ Choix invalide${NC}"
            fi

            echo ""
            read -p "Appuyez sur Entrée pour continuer..."
            clear
            ;;
        4)
            compare_models
            echo ""
            read -p "Appuyez sur Entrée pour continuer..."
            clear
            ;;
        5)
            if [ -d "$DOWNLOAD_DIR" ]; then
                echo -e "${YELLOW}📂 Ouverture du dossier: ${DOWNLOAD_DIR}${NC}"
                open "$DOWNLOAD_DIR" 2>/dev/null || xdg-open "$DOWNLOAD_DIR" 2>/dev/null || echo -e "${YELLOW}⚠️  Impossible d'ouvrir automatiquement. Chemin: ${DOWNLOAD_DIR}${NC}"
            else
                echo -e "${YELLOW}⚠️  Aucun modèle téléchargé pour le moment${NC}"
            fi
            echo ""
            read -p "Appuyez sur Entrée pour continuer..."
            clear
            ;;
        q|Q)
            echo -e "${GREEN}👋 Au revoir !${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Choix invalide${NC}"
            sleep 1
            clear
            ;;
    esac
done
