#!/bin/bash
# Script pour configurer l'infrastructure AWS (IAM, Security Groups, etc.)
# Usage: ./scripts/setup_aws_infrastructure.sh

set -e

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     AWS Infrastructure Setup - Trading ML        ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════╝${NC}"
echo ""

# Configuration
AWS_REGION=${AWS_REGION:-"us-east-1"}
ROLE_NAME="EC2S3AccessRole"
POLICY_NAME="S3ModelsWriteAccess"
INSTANCE_PROFILE_NAME="EC2S3AccessRole"

# Vérifier AWS CLI
if ! command -v aws &> /dev/null; then
    echo -e "${RED}❌ AWS CLI non installé${NC}"
    echo "Installez-le avec: brew install awscli"
    exit 1
fi

# Vérifier la configuration AWS
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}❌ AWS CLI non configuré${NC}"
    echo "Configurez-le avec: aws configure"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${GREEN}✅ AWS Account ID: ${ACCOUNT_ID}${NC}"
echo ""

# Créer le rôle IAM
echo -e "${YELLOW}🔧 Création du rôle IAM '${ROLE_NAME}'...${NC}"
if aws iam get-role --role-name "$ROLE_NAME" &> /dev/null; then
    echo -e "${YELLOW}⚠️  Rôle '${ROLE_NAME}' existe déjà${NC}"
else
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file://$(dirname $0)/../cloudformation/trust-policy.json \
        --description "Role for EC2 instances to access S3 for ML training" \
        --region "$AWS_REGION"
    echo -e "${GREEN}✅ Rôle créé${NC}"
fi

# Attacher la policy S3 Read-Only
echo -e "${YELLOW}🔧 Attachement de AmazonS3ReadOnlyAccess...${NC}"
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess \
    2>/dev/null || echo -e "${YELLOW}⚠️  Policy déjà attachée${NC}"

# Créer la policy custom pour write S3
echo -e "${YELLOW}🔧 Création de la policy custom '${POLICY_NAME}'...${NC}"
POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

if aws iam get-policy --policy-arn "$POLICY_ARN" &> /dev/null; then
    echo -e "${YELLOW}⚠️  Policy '${POLICY_NAME}' existe déjà${NC}"
else
    POLICY_ARN=$(aws iam create-policy \
        --policy-name "$POLICY_NAME" \
        --policy-document file://$(dirname $0)/../cloudformation/s3-write-policy.json \
        --description "Allow writing ML models to S3" \
        --query 'Policy.Arn' \
        --output text)
    echo -e "${GREEN}✅ Policy créée: ${POLICY_ARN}${NC}"
fi

# Attacher la policy custom
echo -e "${YELLOW}🔧 Attachement de ${POLICY_NAME}...${NC}"
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "$POLICY_ARN" \
    2>/dev/null || echo -e "${YELLOW}⚠️  Policy déjà attachée${NC}"

# Créer l'instance profile
echo -e "${YELLOW}🔧 Création de l'instance profile '${INSTANCE_PROFILE_NAME}'...${NC}"
if aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" &> /dev/null; then
    echo -e "${YELLOW}⚠️  Instance profile existe déjà${NC}"
else
    aws iam create-instance-profile \
        --instance-profile-name "$INSTANCE_PROFILE_NAME"

    # Attendre que l'instance profile soit créé
    sleep 5

    # Ajouter le rôle à l'instance profile
    aws iam add-role-to-instance-profile \
        --instance-profile-name "$INSTANCE_PROFILE_NAME" \
        --role-name "$ROLE_NAME"

    echo -e "${GREEN}✅ Instance profile créé${NC}"
fi

echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Infrastructure Setup Complete           ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}✅ Rôle IAM:${NC} ${ROLE_NAME}"
echo -e "${GREEN}✅ Policy:${NC} ${POLICY_NAME}"
echo -e "${GREEN}✅ Instance Profile:${NC} ${INSTANCE_PROFILE_NAME}"
echo ""
echo -e "${YELLOW}📝 Vous pouvez maintenant lancer l'entraînement avec:${NC}"
echo -e "   ./scripts/launch_aws_training.sh train_s3_light"
echo ""
