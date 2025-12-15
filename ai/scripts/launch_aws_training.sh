#!/bin/bash
# Script pour lancer l'entraînement sur AWS EC2
# Usage: ./scripts/launch_aws_training.sh [config_name] [instance_type]

set -e

# Couleurs pour les logs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     AWS Training Launcher - Auto Setup & Deploy       ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

# Configuration par défaut
CONFIG_NAME=${1:-"train_s3_optimized"}
INSTANCE_TYPE=${2:-"g4dn.xlarge"}  # GPU T4, 16GB RAM, ~$0.50/h
AWS_REGION=${AWS_REGION:-"eu-west-3"}
KEY_NAME=${KEY_NAME:-"trading-ml-key"}
SECURITY_GROUP=${SECURITY_GROUP:-"trading-ml-sg"}
S3_BUCKET=${S3_BUCKET:-"qbia"}
S3_MODELS_PREFIX="models/trading"

# Vérifier les prérequis
echo -e "${YELLOW}🔍 Vérification des prérequis...${NC}"

if ! command -v aws &> /dev/null; then
    echo -e "${RED}❌ AWS CLI non installé. Installez-le avec: brew install awscli${NC}"
    exit 1
fi

if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}❌ AWS CLI non configuré. Exécutez: aws configure${NC}"
    exit 1
fi

echo -e "${GREEN}✅ AWS CLI configuré${NC}"

# Créer la clé SSH si elle n'existe pas
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$AWS_REGION" &> /dev/null; then
    echo -e "${YELLOW}📝 Création de la clé SSH '${KEY_NAME}'...${NC}"
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --region "$AWS_REGION" \
        --query 'KeyMaterial' \
        --output text > ~/.ssh/${KEY_NAME}.pem
    chmod 400 ~/.ssh/${KEY_NAME}.pem
    echo -e "${GREEN}✅ Clé SSH créée: ~/.ssh/${KEY_NAME}.pem${NC}"
else
    echo -e "${GREEN}✅ Clé SSH existante${NC}"
fi

# Créer le groupe de sécurité si nécessaire
if ! aws ec2 describe-security-groups --group-names "$SECURITY_GROUP" --region "$AWS_REGION" &> /dev/null; then
    echo -e "${YELLOW}🔒 Création du groupe de sécurité '${SECURITY_GROUP}'...${NC}"
    SG_ID=$(aws ec2 create-security-group \
        --group-name "$SECURITY_GROUP" \
        --description "Security group for ML training instances" \
        --region "$AWS_REGION" \
        --query 'GroupId' \
        --output text)

    # Autoriser SSH depuis votre IP
    MY_IP=$(curl -s https://checkip.amazonaws.com)
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 22 \
        --cidr "${MY_IP}/32" \
        --region "$AWS_REGION"

    echo -e "${GREEN}✅ Groupe de sécurité créé${NC}"
else
    SG_ID=$(aws ec2 describe-security-groups \
        --group-names "$SECURITY_GROUP" \
        --region "$AWS_REGION" \
        --query 'SecurityGroups[0].GroupId' \
        --output text)
    echo -e "${GREEN}✅ Groupe de sécurité existant: ${SG_ID}${NC}"
fi

# Créer le script d'initialisation pour l'instance
cat > /tmp/user_data.sh <<'EOF'
#!/bin/bash
set -e

# Mise à jour du système
apt-get update
apt-get install -y python3-pip python3-venv git awscli

# Installer NVIDIA drivers pour GPU (si instance GPU)
if lspci | grep -i nvidia > /dev/null; then
    apt-get install -y ubuntu-drivers-common
    ubuntu-drivers autoinstall

    # Installer CUDA
    wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
    dpkg -i cuda-keyring_1.1-1_all.deb
    apt-get update
    apt-get -y install cuda-toolkit-12-3
fi

# Créer le répertoire de travail
mkdir -p /home/ubuntu/trading-ml
cd /home/ubuntu/trading-ml

# Créer le script de téléchargement du code
cat > setup_training.sh <<'INNEREOF'
#!/bin/bash
set -e

echo "📥 Téléchargement du code depuis S3..."
aws s3 sync s3://BUCKET_PLACEHOLDER/code/trading-ml/ /home/ubuntu/trading-ml/ --exclude "*.pyc" --exclude "__pycache__/*"

echo "🔧 Installation des dépendances..."
pip3 install --upgrade pip
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
pip3 install pandas numpy boto3 pyyaml s3fs pyarrow fastparquet tqdm

echo "✅ Configuration terminée"
INNEREOF

chmod +x setup_training.sh
chown ubuntu:ubuntu setup_training.sh

# Signal que l'instance est prête
touch /tmp/instance_ready
EOF

# Remplacer le placeholder du bucket
sed -i.bak "s/BUCKET_PLACEHOLDER/${S3_BUCKET}/g" /tmp/user_data.sh

# Uploader le code source vers S3
echo -e "${YELLOW}📤 Upload du code vers S3...${NC}"

# Déterminer le répertoire du projet
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Créer une archive du code
tar -czf /tmp/trading-ml-code.tar.gz \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='checkpoints*' \
    --exclude='.git' \
    .

# Upload vers S3
aws s3 cp /tmp/trading-ml-code.tar.gz "s3://${S3_BUCKET}/code/trading-ml/code.tar.gz"

# Upload aussi les fichiers individuels pour faciliter les mises à jour
aws s3 sync . "s3://${S3_BUCKET}/code/trading-ml/" \
    --exclude "__pycache__/*" \
    --exclude "*.pyc" \
    --exclude "checkpoints*" \
    --exclude ".git/*"

echo -e "${GREEN}✅ Code uploadé vers s3://${S3_BUCKET}/code/trading-ml/${NC}"

# Trouver l'AMI Ubuntu Deep Learning (avec GPU drivers pré-installés)
echo -e "${YELLOW}🔍 Recherche de l'AMI optimisée pour ML...${NC}"
AMI_ID=$(aws ec2 describe-images \
    --region "$AWS_REGION" \
    --owners amazon \
    --filters "Name=name,Values=Deep Learning AMI GPU PyTorch * (Ubuntu 22.04)*" \
    --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
    --output text)

if [ -z "$AMI_ID" ] || [ "$AMI_ID" == "None" ]; then
    echo -e "${YELLOW}⚠️  AMI Deep Learning non trouvée, utilisation d'Ubuntu 22.04 standard${NC}"
    AMI_ID=$(aws ec2 describe-images \
        --region "$AWS_REGION" \
        --owners 099720109477 \
        --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
        --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
        --output text)
fi

echo -e "${GREEN}✅ AMI sélectionnée: ${AMI_ID}${NC}"

# Lancer l'instance EC2
echo -e "${YELLOW}🚀 Lancement de l'instance EC2 (${INSTANCE_TYPE})...${NC}"
INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --region "$AWS_REGION" \
    --user-data file:///tmp/user_data.sh \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3"}}]' \
    --iam-instance-profile Name=EC2S3AccessRole \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=trading-ml-training},{Key=Project,Value=TradingML}]" \
    --query 'Instances[0].InstanceId' \
    --output text)

echo -e "${GREEN}✅ Instance lancée: ${INSTANCE_ID}${NC}"

# Attendre que l'instance soit en cours d'exécution
echo -e "${YELLOW}⏳ Attente du démarrage de l'instance...${NC}"
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$AWS_REGION"

# Récupérer l'IP publique
PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --region "$AWS_REGION" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)

echo -e "${GREEN}✅ Instance démarrée: ${PUBLIC_IP}${NC}"

# Attendre que l'instance soit accessible via SSH
echo -e "${YELLOW}⏳ Attente de la disponibilité SSH...${NC}"
for i in {1..30}; do
    if ssh -i ~/.ssh/${KEY_NAME}.pem -o StrictHostKeyChecking=no -o ConnectTimeout=5 ubuntu@${PUBLIC_IP} "echo OK" &> /dev/null; then
        echo -e "${GREEN}✅ SSH accessible${NC}"
        break
    fi
    echo -n "."
    sleep 10
done
echo ""

# Attendre que l'initialisation soit terminée
echo -e "${YELLOW}⏳ Attente de la fin de l'initialisation...${NC}"
for i in {1..60}; do
    if ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${PUBLIC_IP} "[ -f /tmp/instance_ready ]" &> /dev/null; then
        echo -e "${GREEN}✅ Initialisation terminée${NC}"
        break
    fi
    echo -n "."
    sleep 10
done
echo ""

# Configurer et lancer l'entraînement
echo -e "${YELLOW}🎯 Configuration de l'entraînement sur l'instance...${NC}"

ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${PUBLIC_IP} <<ENDSSH
cd /home/ubuntu/trading-ml

# Télécharger le code
./setup_training.sh

# Extraire l'archive
tar -xzf code.tar.gz

# Créer le script d'entraînement
cat > train_and_upload.sh <<'TRAINEOF'
#!/bin/bash
set -e

CONFIG_NAME="$CONFIG_NAME"
S3_BUCKET="$S3_BUCKET"
S3_PREFIX="$S3_MODELS_PREFIX"

echo "🚀 Démarrage de l'entraînement avec \${CONFIG_NAME}..."

# Lancer l'entraînement
python3 train.py \\
    --config configs/\${CONFIG_NAME}.yaml \\
    --device cuda \\
    --log_level INFO \\
    2>&1 | tee training.log

# Uploader le modèle vers S3
echo "📤 Upload du modèle vers S3..."
CHECKPOINT_DIR=\$(grep "checkpoint_dir" configs/\${CONFIG_NAME}.yaml | awk '{print \$2}' | tr -d '"')

if [ -d "\${CHECKPOINT_DIR}" ]; then
    TIMESTAMP=\$(date +%Y%m%d_%H%M%S)
    aws s3 sync \${CHECKPOINT_DIR} s3://\${S3_BUCKET}/\${S3_PREFIX}/\${CONFIG_NAME}_\${TIMESTAMP}/

    # Uploader aussi le log
    aws s3 cp training.log s3://\${S3_BUCKET}/\${S3_PREFIX}/\${CONFIG_NAME}_\${TIMESTAMP}/training.log

    echo "✅ Modèle uploadé vers: s3://\${S3_BUCKET}/\${S3_PREFIX}/\${CONFIG_NAME}_\${TIMESTAMP}/"
else
    echo "❌ Répertoire de checkpoint non trouvé: \${CHECKPOINT_DIR}"
    exit 1
fi

echo "🎉 Entraînement terminé avec succès!"
TRAINEOF

chmod +x train_and_upload.sh

# Lancer l'entraînement en arrière-plan avec nohup
nohup ./train_and_upload.sh > training_output.log 2>&1 &

echo "✅ Entraînement lancé en arrière-plan"
echo "📊 Pour suivre les logs: ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${PUBLIC_IP} 'tail -f /home/ubuntu/trading-ml/training.log'"
ENDSSH

# Afficher les informations de connexion
echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║              Entraînement Lancé sur AWS                ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}✅ Instance ID:${NC} ${INSTANCE_ID}"
echo -e "${GREEN}✅ IP Publique:${NC} ${PUBLIC_IP}"
echo -e "${GREEN}✅ Type:${NC} ${INSTANCE_TYPE}"
echo -e "${GREEN}✅ Configuration:${NC} ${CONFIG_NAME}"
echo ""
echo -e "${YELLOW}📊 Commandes utiles:${NC}"
echo ""
echo -e "  ${BLUE}# Se connecter à l'instance${NC}"
echo -e "  ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${PUBLIC_IP}"
echo ""
echo -e "  ${BLUE}# Suivre les logs d'entraînement${NC}"
echo -e "  ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${PUBLIC_IP} 'tail -f /home/ubuntu/trading-ml/training.log'"
echo ""
echo -e "  ${BLUE}# Télécharger le modèle depuis S3${NC}"
echo -e "  aws s3 ls s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/"
echo ""
echo -e "  ${BLUE}# Arrêter l'instance (après entraînement)${NC}"
echo -e "  aws ec2 terminate-instances --instance-ids ${INSTANCE_ID} --region ${AWS_REGION}"
echo ""
echo -e "${YELLOW}💡 L'entraînement est lancé en arrière-plan. Le modèle sera automatiquement uploadé vers S3.${NC}"
echo ""

# Sauvegarder les infos de l'instance
cat > /tmp/aws_training_instance.json <<JSONEOF
{
  "instance_id": "${INSTANCE_ID}",
  "public_ip": "${PUBLIC_IP}",
  "instance_type": "${INSTANCE_TYPE}",
  "config_name": "${CONFIG_NAME}",
  "s3_bucket": "${S3_BUCKET}",
  "s3_models_path": "s3://${S3_BUCKET}/${S3_MODELS_PREFIX}/",
  "key_path": "~/.ssh/${KEY_NAME}.pem",
  "region": "${AWS_REGION}",
  "launched_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSONEOF

echo -e "${GREEN}✅ Informations sauvegardées dans: /tmp/aws_training_instance.json${NC}"
echo ""
