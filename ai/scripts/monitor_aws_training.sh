#!/bin/bash
# Script pour monitorer un entraînement en cours sur AWS
# Usage: ./scripts/monitor_aws_training.sh [instance_id]

set -e

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Charger les infos de l'instance depuis le fichier JSON
if [ -f /tmp/aws_training_instance.json ]; then
    INSTANCE_ID=$(cat /tmp/aws_training_instance.json | python3 -c "import sys, json; print(json.load(sys.stdin)['instance_id'])")
    PUBLIC_IP=$(cat /tmp/aws_training_instance.json | python3 -c "import sys, json; print(json.load(sys.stdin)['public_ip'])")
    KEY_PATH=$(cat /tmp/aws_training_instance.json | python3 -c "import sys, json; print(json.load(sys.stdin)['key_path'])" | sed 's/~/$HOME/')
    REGION=$(cat /tmp/aws_training_instance.json | python3 -c "import sys, json; print(json.load(sys.stdin)['region'])")
    CONFIG_NAME=$(cat /tmp/aws_training_instance.json | python3 -c "import sys, json; print(json.load(sys.stdin)['config_name'])")
    LAUNCHED_AT=$(cat /tmp/aws_training_instance.json | python3 -c "import sys, json; print(json.load(sys.stdin)['launched_at'])")
else
    if [ -z "$1" ]; then
        echo -e "${RED}❌ Aucune instance trouvée. Fournissez l'instance ID:${NC}"
        echo "Usage: $0 [instance_id]"
        exit 1
    fi
    INSTANCE_ID=$1
    REGION=${AWS_REGION:-"us-east-1"}
fi

echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║          AWS Training Monitor - Live Status           ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

# Fonction pour afficher le statut
show_status() {
    echo -e "${YELLOW}📊 Statut de l'instance${NC}"
    echo "─────────────────────────────────────────────────────"

    # Récupérer les infos de l'instance
    INSTANCE_STATE=$(aws ec2 describe-instances \
        --instance-ids "$INSTANCE_ID" \
        --region "$REGION" \
        --query 'Reservations[0].Instances[0].State.Name' \
        --output text 2>/dev/null)

    if [ -z "$INSTANCE_STATE" ]; then
        echo -e "${RED}❌ Instance non trouvée ou terminée${NC}"
        exit 1
    fi

    INSTANCE_TYPE=$(aws ec2 describe-instances \
        --instance-ids "$INSTANCE_ID" \
        --region "$REGION" \
        --query 'Reservations[0].Instances[0].InstanceType' \
        --output text)

    LAUNCH_TIME=$(aws ec2 describe-instances \
        --instance-ids "$INSTANCE_ID" \
        --region "$REGION" \
        --query 'Reservations[0].Instances[0].LaunchTime' \
        --output text)

    echo -e "${GREEN}Instance ID:${NC} ${INSTANCE_ID}"
    echo -e "${GREEN}État:${NC} ${INSTANCE_STATE}"
    echo -e "${GREEN}Type:${NC} ${INSTANCE_TYPE}"
    echo -e "${GREEN}Lancée:${NC} ${LAUNCH_TIME}"

    if [ ! -z "$CONFIG_NAME" ]; then
        echo -e "${GREEN}Configuration:${NC} ${CONFIG_NAME}"
    fi

    echo ""
}

# Fonction pour calculer le coût estimé
estimate_cost() {
    echo -e "${YELLOW}💰 Coût estimé${NC}"
    echo "─────────────────────────────────────────────────────"

    # Prix par heure selon le type d'instance
    case $INSTANCE_TYPE in
        "g4dn.xlarge")
            PRICE_PER_HOUR=0.526
            ;;
        "g4dn.2xlarge")
            PRICE_PER_HOUR=0.752
            ;;
        "p3.2xlarge")
            PRICE_PER_HOUR=3.06
            ;;
        "t3.large")
            PRICE_PER_HOUR=0.0832
            ;;
        *)
            PRICE_PER_HOUR=0.50
            ;;
    esac

    # Calculer la durée
    LAUNCH_TIMESTAMP=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${LAUNCH_TIME:0:19}" +%s 2>/dev/null || echo 0)
    CURRENT_TIMESTAMP=$(date +%s)
    ELAPSED_SECONDS=$((CURRENT_TIMESTAMP - LAUNCH_TIMESTAMP))
    ELAPSED_HOURS=$(echo "scale=2; $ELAPSED_SECONDS / 3600" | bc)

    ESTIMATED_COST=$(echo "scale=2; $ELAPSED_HOURS * $PRICE_PER_HOUR" | bc)

    echo -e "${GREEN}Durée:${NC} ${ELAPSED_HOURS}h"
    echo -e "${GREEN}Prix/heure:${NC} \$${PRICE_PER_HOUR}"
    echo -e "${GREEN}Coût estimé:${NC} \$${ESTIMATED_COST}"
    echo ""
}

# Fonction pour afficher les logs
show_logs() {
    echo -e "${YELLOW}📜 Dernières lignes du log d'entraînement${NC}"
    echo "─────────────────────────────────────────────────────"

    if [ -z "$PUBLIC_IP" ]; then
        PUBLIC_IP=$(aws ec2 describe-instances \
            --instance-ids "$INSTANCE_ID" \
            --region "$REGION" \
            --query 'Reservations[0].Instances[0].PublicIpAddress' \
            --output text)
    fi

    if [ -z "$KEY_PATH" ]; then
        KEY_PATH="$HOME/.ssh/trading-ml-key.pem"
    fi

    if [ "$INSTANCE_STATE" != "running" ]; then
        echo -e "${YELLOW}⚠️  Instance non running (état: ${INSTANCE_STATE})${NC}"
        return
    fi

    # Essayer de récupérer les logs
    ssh -i "$KEY_PATH" -o StrictHostKeyChecking=no -o ConnectTimeout=5 ubuntu@${PUBLIC_IP} \
        'tail -n 20 /home/ubuntu/trading-ml/training.log 2>/dev/null || tail -n 20 /home/ubuntu/trading-ml/training_output.log 2>/dev/null || echo "Logs non encore disponibles"' \
        2>/dev/null || echo -e "${YELLOW}⚠️  Impossible de se connecter à l'instance${NC}"

    echo ""
}

# Menu interactif
while true; do
    clear
    show_status
    estimate_cost
    show_logs

    echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                    Actions                             ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "  1) Rafraîchir le statut (auto-refresh 30s)"
    echo "  2) Voir tous les logs (tail -f)"
    echo "  3) Se connecter en SSH"
    echo "  4) Télécharger les logs"
    echo "  5) Télécharger les checkpoints depuis S3"
    echo "  6) Arrêter l'instance"
    echo "  q) Quitter"
    echo ""

    read -t 30 -p "Choix (auto-refresh dans 30s): " choice 2>/dev/null || choice="1"

    case $choice in
        1)
            # Auto-refresh
            ;;
        2)
            echo -e "${YELLOW}📜 Suivi des logs en temps réel (Ctrl+C pour quitter)...${NC}"
            ssh -i "$KEY_PATH" -o StrictHostKeyChecking=no ubuntu@${PUBLIC_IP} \
                'tail -f /home/ubuntu/trading-ml/training.log 2>/dev/null || tail -f /home/ubuntu/trading-ml/training_output.log'
            ;;
        3)
            echo -e "${YELLOW}🔐 Connexion SSH...${NC}"
            ssh -i "$KEY_PATH" -o StrictHostKeyChecking=no ubuntu@${PUBLIC_IP}
            ;;
        4)
            echo -e "${YELLOW}📥 Téléchargement des logs...${NC}"
            scp -i "$KEY_PATH" -o StrictHostKeyChecking=no \
                ubuntu@${PUBLIC_IP}:/home/ubuntu/trading-ml/training.log \
                ./training_${INSTANCE_ID}.log
            echo -e "${GREEN}✅ Logs téléchargés: ./training_${INSTANCE_ID}.log${NC}"
            read -p "Appuyez sur Entrée pour continuer..."
            ;;
        5)
            echo -e "${YELLOW}📥 Téléchargement des checkpoints depuis S3...${NC}"
            S3_BUCKET=$(cat /tmp/aws_training_instance.json | python3 -c "import sys, json; print(json.load(sys.stdin)['s3_bucket'])" 2>/dev/null || echo "qbia")
            echo "Liste des modèles disponibles:"
            aws s3 ls s3://${S3_BUCKET}/models/trading/ --recursive | grep "\.pt$"
            echo ""
            read -p "Télécharger tout ? (y/n): " download
            if [ "$download" = "y" ]; then
                aws s3 sync s3://${S3_BUCKET}/models/trading/ ./models_downloaded/
                echo -e "${GREEN}✅ Checkpoints téléchargés dans ./models_downloaded/${NC}"
            fi
            read -p "Appuyez sur Entrée pour continuer..."
            ;;
        6)
            echo -e "${RED}⚠️  Voulez-vous vraiment arrêter l'instance ? (y/n)${NC}"
            read confirm
            if [ "$confirm" = "y" ]; then
                echo -e "${YELLOW}🛑 Arrêt de l'instance...${NC}"
                aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" --region "$REGION"
                echo -e "${GREEN}✅ Instance en cours d'arrêt${NC}"
                sleep 3
                exit 0
            fi
            ;;
        q|Q)
            echo -e "${GREEN}👋 Au revoir !${NC}"
            exit 0
            ;;
    esac
done
