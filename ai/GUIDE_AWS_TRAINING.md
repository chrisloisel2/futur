# Guide Complet - Entraînement ML sur AWS EC2

Ce guide explique comment utiliser l'infrastructure d'entraînement automatisée sur AWS pour résoudre les problèmes de mémoire RAM et bénéficier de GPUs pour un entraînement plus rapide.

## 🎯 Problème Résolu

**Problème**: L'entraînement local crash avec l'erreur `Killed: 9` en raison d'une mémoire RAM insuffisante.

**Cause**: Charger 2.6M lignes × 53 features nécessite 16-32 GB de RAM, ce qui dépasse la capacité de la plupart des machines locales.

**Solution**: Déploiement automatisé sur AWS EC2 avec:
- Instances GPU (NVIDIA T4) pour entraînement 10x plus rapide
- Jusqu'à 64GB de RAM
- Stockage automatique des modèles dans S3
- Coût maîtrisé (~$2.50 pour un entraînement optimisé)

---

## 📋 Table des Matières

1. [Prérequis](#prérequis)
2. [Installation et Configuration](#installation-et-configuration)
3. [Configurations Disponibles](#configurations-disponibles)
4. [Utilisation](#utilisation)
5. [Coûts](#coûts)
6. [Monitoring](#monitoring)
7. [Téléchargement des Modèles](#téléchargement-des-modèles)
8. [Troubleshooting](#troubleshooting)
9. [Architecture Technique](#architecture-technique)
10. [FAQ](#faq)

---

## 🔧 Prérequis

### 1. Compte AWS
- Créer un compte AWS: https://aws.amazon.com/
- Carte bancaire requise (facturation à l'usage)
- Free tier disponible pour les tests (hors GPU)

### 2. AWS CLI
```bash
# macOS
brew install awscli

# Linux
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Vérifier l'installation
aws --version
```

### 3. Configuration AWS CLI
```bash
aws configure

# Entrer vos credentials:
# AWS Access Key ID: [Votre clé d'accès]
# AWS Secret Access Key: [Votre clé secrète]
# Default region name: us-east-1
# Default output format: json
```

**Où trouver vos credentials ?**
1. Console AWS → IAM → Users → Votre utilisateur
2. Security credentials → Create access key
3. Télécharger le CSV avec les clés

### 4. Permissions IAM Requises

Votre utilisateur AWS doit avoir les permissions suivantes:
- `EC2FullAccess` (pour créer/gérer les instances)
- `S3FullAccess` (pour uploader le code et télécharger les modèles)
- `IAMFullAccess` (pour créer le rôle EC2S3AccessRole)

---

## ⚙️ Installation et Configuration

### Étape 1: Cloner le Projet

Si ce n'est pas déjà fait:
```bash
cd /Users/christopher/Desktop/futur/ai
```

### Étape 2: Créer l'Infrastructure AWS

Cette étape est **à faire une seule fois** pour créer le rôle IAM qui permet à EC2 d'accéder à S3:

```bash
cd /Users/christopher/Desktop/futur/ai

# Exécuter le script de setup
./scripts/setup_aws_infrastructure.sh
```

Ce script va créer:
- ✅ Rôle IAM `EC2S3AccessRole`
- ✅ Policy S3 pour read (code/données) et write (modèles)
- ✅ Instance profile pour EC2

**Sortie attendue:**
```
✅ AWS CLI configuré
🔧 Création du rôle IAM EC2S3AccessRole...
✅ Rôle IAM créé
🔐 Création de la policy S3ModelsWriteAccess...
✅ Policy créée
🔗 Attachement des policies...
✅ Policies attachées
📦 Création de l'instance profile...
✅ Instance profile créé
✅ Infrastructure AWS configurée avec succès !
```

### Étape 3: Vérifier la Configuration

```bash
# Vérifier que le rôle existe
aws iam get-role --role-name EC2S3AccessRole

# Vérifier l'accès S3
aws s3 ls s3://qbia/
```

---

## 📦 Configurations Disponibles

Le projet contient 4 configurations d'entraînement, de la plus légère à la plus complète:

### 1. `train_s3_tiny.yaml` - Test Ultra-Léger

**Idéal pour**: Tester que tout fonctionne avant un long entraînement

| Paramètre | Valeur |
|-----------|---------|
| Symboles | 1 (BTCUSDT) |
| Période | 3 mois (2024) |
| Lookback | 20 timesteps |
| Batch size | 4 |
| Modèle | d_model=128, n_layers=2 |
| Epochs | 3 |
| **RAM requise** | **4 GB** |
| **Instance recommandée** | t3.large |
| **Durée estimée** | 30 minutes |
| **Coût estimé** | **~$0.05** |

```bash
./scripts/launch_aws_training.sh train_s3_tiny t3.large
```

### 2. `train_s3_light.yaml` - Léger

**Idéal pour**: Entraînement rapide sur Bitcoin uniquement

| Paramètre | Valeur |
|-----------|---------|
| Symboles | 1 (BTCUSDT) |
| Période | 2024 (1 an) |
| Lookback | 40 timesteps |
| Batch size | 8 |
| Modèle | d_model=256, n_layers=3 |
| Epochs | 20 |
| **RAM requise** | **8 GB** |
| **Instance recommandée** | g4dn.xlarge |
| **Durée estimée** | 1-2 heures |
| **Coût estimé** | **~$0.75** |

```bash
./scripts/launch_aws_training.sh train_s3_light g4dn.xlarge
```

### 3. `train_s3_optimized.yaml` - Optimisé (RECOMMANDÉ)

**Idéal pour**: Bon équilibre performance/coût avec les 3 principales cryptos

| Paramètre | Valeur |
|-----------|---------|
| Symboles | 3 (BTC, ETH, BNB) |
| Période | 2023-2024 (2 ans) |
| Lookback | 60 timesteps |
| Batch size | 16 |
| Modèle | d_model=384, n_layers=4 |
| Epochs | 30 |
| **RAM requise** | **16 GB** |
| **Instance recommandée** | g4dn.xlarge |
| **Durée estimée** | 4-6 heures |
| **Coût estimé** | **~$2.50** |

```bash
./scripts/launch_aws_training.sh train_s3_optimized g4dn.xlarge
```

### 4. `train_s3.yaml` - Complet

**Idéal pour**: Entraînement complet sur 8 cryptos et 5 ans de données

| Paramètre | Valeur |
|-----------|---------|
| Symboles | 8 (BTC, ETH, BNB, ADA, SOL, XRP, DOGE, MATIC) |
| Période | 2020-2024 (5 ans) |
| Lookback | 100 timesteps |
| Batch size | 32 |
| Modèle | d_model=512, n_layers=6 |
| Epochs | 50 |
| **RAM requise** | **32 GB** |
| **Instance recommandée** | g4dn.2xlarge |
| **Durée estimée** | 12-16 heures |
| **Coût estimé** | **~$10** |

```bash
./scripts/launch_aws_training.sh train_s3 g4dn.2xlarge
```

---

## 🚀 Utilisation

### Lancement d'un Entraînement

#### Commande Basique (utilise les valeurs par défaut)
```bash
cd /Users/christopher/Desktop/futur/ai
./scripts/launch_aws_training.sh
```

Par défaut:
- Configuration: `train_s3_optimized`
- Instance: `g4dn.xlarge`

#### Commande Personnalisée
```bash
./scripts/launch_aws_training.sh [CONFIG_NAME] [INSTANCE_TYPE]

# Exemples:
./scripts/launch_aws_training.sh train_s3_tiny t3.large
./scripts/launch_aws_training.sh train_s3_optimized g4dn.xlarge
./scripts/launch_aws_training.sh train_s3 g4dn.2xlarge
```

### Ce Qui Se Passe Automatiquement

1. **Vérification des prérequis** ✅
   - AWS CLI installé et configuré

2. **Création des ressources AWS** 🔧
   - Clé SSH (si elle n'existe pas)
   - Security Group (si il n'existe pas)
   - Autorise SSH uniquement depuis votre IP

3. **Upload du code vers S3** 📤
   - Archive du code source
   - Upload vers `s3://qbia/code/trading-ml/`

4. **Lancement de l'instance EC2** 🚀
   - AMI Deep Learning avec GPU drivers
   - Configuration du volume (100GB SSD)
   - Attachement du rôle IAM

5. **Installation sur l'instance** ⚙️
   - Python, PyTorch, CUDA
   - Téléchargement du code depuis S3
   - Installation des dépendances

6. **Lancement de l'entraînement** 🎯
   - En arrière-plan avec `nohup`
   - Logs dans `training.log`

7. **Upload automatique des résultats** 📊
   - Checkpoints vers S3 après chaque epoch
   - Log d'entraînement
   - Destination: `s3://qbia/models/trading/CONFIG_TIMESTAMP/`

### Sortie du Script

```
╔════════════════════════════════════════════════════════╗
║     AWS Training Launcher - Auto Setup & Deploy       ║
╚════════════════════════════════════════════════════════╝

🔍 Vérification des prérequis...
✅ AWS CLI configuré
✅ Clé SSH existante
✅ Groupe de sécurité existant: sg-xxxxx
📤 Upload du code vers S3...
✅ Code uploadé vers s3://qbia/code/trading-ml/
🔍 Recherche de l'AMI optimisée pour ML...
✅ AMI sélectionnée: ami-xxxxx
🚀 Lancement de l'instance EC2 (g4dn.xlarge)...
✅ Instance lancée: i-xxxxx
⏳ Attente du démarrage de l'instance...
✅ Instance démarrée: 54.123.45.67
⏳ Attente de la disponibilité SSH...
✅ SSH accessible
⏳ Attente de la fin de l'initialisation...
✅ Initialisation terminée
🎯 Configuration de l'entraînement sur l'instance...
✅ Entraînement lancé en arrière-plan

╔════════════════════════════════════════════════════════╗
║              Entraînement Lancé sur AWS                ║
╚════════════════════════════════════════════════════════╝

✅ Instance ID: i-0abc123def456
✅ IP Publique: 54.123.45.67
✅ Type: g4dn.xlarge
✅ Configuration: train_s3_optimized

📊 Commandes utiles:

  # Se connecter à l'instance
  ssh -i ~/.ssh/trading-ml-key.pem ubuntu@54.123.45.67

  # Suivre les logs d'entraînement
  ssh -i ~/.ssh/trading-ml-key.pem ubuntu@54.123.45.67 'tail -f /home/ubuntu/trading-ml/training.log'

  # Télécharger le modèle depuis S3
  aws s3 ls s3://qbia/models/trading/

  # Arrêter l'instance (après entraînement)
  aws ec2 terminate-instances --instance-ids i-0abc123def456 --region us-east-1

💡 L'entraînement est lancé en arrière-plan. Le modèle sera automatiquement uploadé vers S3.

✅ Informations sauvegardées dans: /tmp/aws_training_instance.json
```

Les informations de l'instance sont sauvegardées dans `/tmp/aws_training_instance.json` pour faciliter le monitoring.

---

## 📊 Monitoring

### Option 1: Script de Monitoring Interactif (RECOMMANDÉ)

```bash
./scripts/monitor_aws_training.sh
```

Ce script affiche un dashboard interactif qui se rafraîchit toutes les 30 secondes:

```
╔════════════════════════════════════════════════════════╗
║          AWS Training Monitor - Live Status           ║
╚════════════════════════════════════════════════════════╝

📊 Statut de l'instance
─────────────────────────────────────────────────────
Instance ID: i-0abc123def456
État: running
Type: g4dn.xlarge
Lancée: 2024-12-14T15:30:45Z
Configuration: train_s3_optimized

💰 Coût estimé
─────────────────────────────────────────────────────
Durée: 2.50h
Prix/heure: $0.526
Coût estimé: $1.32

📜 Dernières lignes du log d'entraînement
─────────────────────────────────────────────────────
Epoch 12/30 - Batch 450/500
Loss: 0.0234
Validation Accuracy: 72.3%
Time remaining: ~2h 15min

╔════════════════════════════════════════════════════════╗
║                    Actions                             ║
╚════════════════════════════════════════════════════════╝

  1) Rafraîchir le statut (auto-refresh 30s)
  2) Voir tous les logs (tail -f)
  3) Se connecter en SSH
  4) Télécharger les logs
  5) Télécharger les checkpoints depuis S3
  6) Arrêter l'instance
  q) Quitter

Choix (auto-refresh dans 30s):
```

**Fonctionnalités:**
- ✅ Statut en temps réel
- ✅ Calcul du coût estimé
- ✅ Affichage des derniers logs
- ✅ Accès SSH direct
- ✅ Téléchargement des checkpoints
- ✅ Arrêt de l'instance

### Option 2: Commandes Manuelles

#### Voir les Logs en Temps Réel
```bash
ssh -i ~/.ssh/trading-ml-key.pem ubuntu@[IP_PUBLIQUE] 'tail -f /home/ubuntu/trading-ml/training.log'
```

#### Se Connecter en SSH
```bash
ssh -i ~/.ssh/trading-ml-key.pem ubuntu@[IP_PUBLIQUE]

# Une fois connecté:
cd /home/ubuntu/trading-ml
ls -lh checkpoints_*
tail -f training.log
```

#### Vérifier le Statut de l'Instance
```bash
aws ec2 describe-instances \
    --instance-ids [INSTANCE_ID] \
    --query 'Reservations[0].Instances[0].State.Name' \
    --output text
```

#### Calculer le Coût Actuel
```bash
# Le script de monitoring le fait automatiquement
./scripts/monitor_aws_training.sh
```

---

## 📥 Téléchargement des Modèles

### Option 1: Script de Téléchargement Interactif (RECOMMANDÉ)

```bash
./scripts/download_models_from_s3.sh
```

Menu interactif:
```
╔════════════════════════════════════════════════════════╗
║         S3 Model Downloader - Trading ML               ║
╚════════════════════════════════════════════════════════╝

╔════════════════════════════════════════════════════════╗
║                   Options                              ║
╚════════════════════════════════════════════════════════╝

  1) Lister tous les modèles disponibles
  2) Télécharger le dernier modèle
  3) Télécharger un modèle spécifique
  4) Comparer tous les modèles
  5) Ouvrir le dossier de téléchargement
  q) Quitter

Choix:
```

### Option 2: Ligne de Commande

#### Télécharger le Dernier Modèle
```bash
./scripts/download_models_from_s3.sh --latest
```

#### Lister Tous les Modèles
```bash
./scripts/download_models_from_s3.sh --list
```

Sortie:
```
📋 Modèles disponibles dans s3://qbia/models/trading/
─────────────────────────────────────────────────────
[1] train_s3_optimized_20241214_153045
    Config: train_s3_optimized
    Date: 2024-12-14 15:30:45
    Taille: 234.56 MB (12 fichiers)

[2] train_s3_light_20241213_093022
    Config: train_s3_light
    Date: 2024-12-13 09:30:22
    Taille: 89.23 MB (8 fichiers)
```

#### Télécharger un Modèle Spécifique
```bash
./scripts/download_models_from_s3.sh --model train_s3_optimized_20241214_153045
```

#### Comparer Tous les Modèles
```bash
./scripts/download_models_from_s3.sh --compare
```

Sortie:
```
Modèle                                   Date                 Taille (MB)     Fichiers
──────────────────────────────────────────────────────────────────────────────────
train_s3_optimized_20241214_153045       2024-12-14          234.56          12
train_s3_light_20241213_093022           2024-12-13          89.23           8
train_s3_20241210_143012                 2024-12-10          512.34          18
```

### Option 3: AWS CLI Direct

```bash
# Lister les modèles
aws s3 ls s3://qbia/models/trading/

# Télécharger un modèle
aws s3 sync s3://qbia/models/trading/train_s3_optimized_20241214_153045/ ./my_model/

# Télécharger uniquement le checkpoint final
aws s3 cp s3://qbia/models/trading/train_s3_optimized_20241214_153045/checkpoint_final.pt ./
```

### Structure d'un Modèle Téléchargé

```
downloaded_models/train_s3_optimized_20241214_153045/
├── checkpoint_epoch_10.pt      # Checkpoint de l'epoch 10
├── checkpoint_epoch_20.pt      # Checkpoint de l'epoch 20
├── checkpoint_final.pt         # Checkpoint final (meilleur modèle)
├── training.log                # Log complet de l'entraînement
├── config.yaml                 # Configuration utilisée
└── metrics.json                # Métriques d'entraînement (si disponible)
```

---

## 💰 Coûts

### Coûts par Instance

| Type d'Instance | vCPU | RAM | GPU | Prix/Heure | Cas d'Usage |
|-----------------|------|-----|-----|-----------|-------------|
| `t3.large` | 2 | 8 GB | - | $0.0832 | Test tiny |
| `g4dn.xlarge` | 4 | 16 GB | T4 (16GB) | $0.526 | Optimized, Light |
| `g4dn.2xlarge` | 8 | 32 GB | T4 (16GB) | $0.752 | Complet |
| `p3.2xlarge` | 8 | 61 GB | V100 (16GB) | $3.06 | Ultra-rapide (overkill) |

### Coûts par Configuration

| Configuration | Instance | Durée | Coût Total | Coût/Epoch |
|--------------|----------|-------|-----------|-----------|
| `train_s3_tiny` | t3.large | 30 min | **$0.05** | $0.02 |
| `train_s3_light` | g4dn.xlarge | 1-2h | **$0.75** | $0.04 |
| `train_s3_optimized` | g4dn.xlarge | 4-6h | **$2.50** | $0.08 |
| `train_s3` | g4dn.2xlarge | 12-16h | **$10.00** | $0.20 |

### Coûts S3

Le stockage S3 est négligeable:
- **Stockage**: $0.023/GB/mois
- **Upload**: Gratuit
- **Download**: $0.09/GB (premiers 10 TB/mois)

**Exemple**: 5 modèles de 500 MB chacun = 2.5 GB = **$0.06/mois**

### Optimiser les Coûts

#### 1. Utiliser des Spot Instances (Économie de 70%)

Les Spot Instances sont des instances AWS non utilisées vendues à prix réduit (-70%). Le risque d'interruption est faible pour les GPU.

**Modification dans `launch_aws_training.sh`:**
```bash
# Remplacer:
aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE"

# Par:
aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --instance-market-options '{"MarketType":"spot","SpotOptions":{"MaxPrice":"0.20","SpotInstanceType":"one-time"}}'
```

**Prix Spot moyens:**
- g4dn.xlarge: **$0.15/h** (au lieu de $0.526)
- g4dn.2xlarge: **$0.22/h** (au lieu de $0.752)

**Économie sur train_s3_optimized**: $0.75 au lieu de $2.50 ✅

#### 2. Arrêter l'Instance Immédiatement Après l'Entraînement

L'instance continue de tourner (et de coûter) tant qu'elle n'est pas arrêtée.

**Solution**: Modifier le script `train_and_upload.sh` pour auto-shutdown:
```bash
# À la fin du training, ajouter:
echo "🛑 Auto-shutdown dans 5 minutes..."
sudo shutdown -h +5
```

#### 3. Utiliser des Configurations Plus Légères

- **train_s3_tiny** pour tester: $0.05
- **train_s3_light** pour Bitcoin uniquement: $0.75
- **train_s3_optimized** pour 3 cryptos principales: $2.50

Éviter `train_s3` (8 cryptos) sauf si vraiment nécessaire.

#### 4. Configurer des Alertes de Budget

```bash
# Créer une alerte si le coût dépasse $20/mois
aws budgets create-budget \
    --account-id $(aws sts get-caller-identity --query Account --output text) \
    --budget file://budget.json
```

**Fichier `budget.json`:**
```json
{
  "BudgetName": "ML-Training-Budget",
  "BudgetLimit": {
    "Amount": "20",
    "Unit": "USD"
  },
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
```

---

## 🛑 Arrêt de l'Instance

### Option 1: Via le Script de Monitoring
```bash
./scripts/monitor_aws_training.sh
# Choisir l'option 6 pour arrêter l'instance
```

### Option 2: Ligne de Commande
```bash
# Arrêter (STOP) - L'instance peut être redémarrée
aws ec2 stop-instances --instance-ids [INSTANCE_ID]

# Terminer (TERMINATE) - L'instance est supprimée définitivement
aws ec2 terminate-instances --instance-ids [INSTANCE_ID] --region us-east-1
```

**⚠️ Important:**
- **STOP**: L'instance peut être redémarrée, mais vous continuez à payer le stockage EBS (~$0.10/jour)
- **TERMINATE**: L'instance est supprimée définitivement (RECOMMANDÉ après entraînement)

### Option 3: Auto-Shutdown

Modifier `/home/ubuntu/trading-ml/train_and_upload.sh` sur l'instance pour ajouter:
```bash
echo "✅ Modèle uploadé vers: s3://${S3_BUCKET}/${S3_PREFIX}/${CONFIG_NAME}_${TIMESTAMP}/"
echo "🛑 Auto-shutdown dans 5 minutes..."
sudo shutdown -h +5
```

---

## 🔍 Troubleshooting

### Problème 1: `aws: command not found`

**Cause**: AWS CLI non installé

**Solution**:
```bash
# macOS
brew install awscli

# Linux
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
```

### Problème 2: `Unable to locate credentials`

**Cause**: AWS CLI non configuré

**Solution**:
```bash
aws configure
# Entrer vos Access Key ID et Secret Access Key
```

### Problème 3: `An error occurred (UnauthorizedOperation)`

**Cause**: Votre utilisateur AWS n'a pas les permissions nécessaires

**Solution**: Ajouter les policies suivantes à votre utilisateur IAM:
- `AmazonEC2FullAccess`
- `AmazonS3FullAccess`
- `IAMFullAccess`

### Problème 4: `No default VPC for this user`

**Cause**: Votre compte AWS n'a pas de VPC par défaut

**Solution**: Créer un VPC par défaut:
```bash
aws ec2 create-default-vpc
```

### Problème 5: `Instance limit exceeded`

**Cause**: Vous avez atteint la limite d'instances pour votre compte

**Solution**: Demander une augmentation de quota via la console AWS:
1. Service Quotas → AWS services → Amazon EC2
2. Running On-Demand G and VT instances
3. Request quota increase

### Problème 6: L'Entraînement Crash avec OOM (Out of Memory)

**Cause**: Configuration trop gourmande pour l'instance choisie

**Solution**: Utiliser une configuration plus légère ou une instance avec plus de RAM:
```bash
# Essayer train_s3_optimized au lieu de train_s3
./scripts/launch_aws_training.sh train_s3_optimized g4dn.xlarge

# Ou utiliser une instance avec plus de RAM
./scripts/launch_aws_training.sh train_s3 g4dn.2xlarge
```

### Problème 7: `Permission denied (publickey)`

**Cause**: Problème avec la clé SSH

**Solution**:
```bash
# Vérifier que la clé existe
ls -l ~/.ssh/trading-ml-key.pem

# Vérifier les permissions
chmod 400 ~/.ssh/trading-ml-key.pem

# Tester la connexion
ssh -i ~/.ssh/trading-ml-key.pem ubuntu@[IP_PUBLIQUE]
```

### Problème 8: L'Instance se Lance Mais l'Entraînement ne Démarre Pas

**Diagnostic**:
```bash
# Se connecter en SSH
ssh -i ~/.ssh/trading-ml-key.pem ubuntu@[IP_PUBLIQUE]

# Vérifier les logs système
sudo tail -f /var/log/cloud-init-output.log

# Vérifier les logs d'entraînement
tail -f /home/ubuntu/trading-ml/training.log
tail -f /home/ubuntu/trading-ml/training_output.log
```

**Solutions possibles**:
- Vérifier que le code a bien été téléchargé depuis S3
- Vérifier que les dépendances sont installées
- Vérifier les logs pour les erreurs Python

### Problème 9: Coût Inattendu

**Cause**: Instance oubliée en cours d'exécution

**Solution**:
```bash
# Lister toutes les instances running
aws ec2 describe-instances \
    --filters "Name=instance-state-name,Values=running" \
    --query 'Reservations[*].Instances[*].[InstanceId,InstanceType,LaunchTime]' \
    --output table

# Arrêter toutes les instances training-ml
aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=trading-ml-training" "Name=instance-state-name,Values=running" \
    --query 'Reservations[*].Instances[*].InstanceId' \
    --output text | xargs -n 1 aws ec2 terminate-instances --instance-ids
```

---

## 🏗️ Architecture Technique

### Workflow Complet

```
┌─────────────────┐
│  Local Machine  │
│                 │
│  1. Modify code │
└────────┬────────┘
         │
         │ ./launch_aws_training.sh
         ▼
┌─────────────────┐
│   Upload to S3  │
│                 │
│ s3://qbia/code/ │
└────────┬────────┘
         │
         │ Launch EC2
         ▼
┌─────────────────┐
│  EC2 Instance   │
│  + GPU (NVIDIA) │
│  + 16-32GB RAM  │
└────────┬────────┘
         │
         │ Download code from S3
         ▼
┌─────────────────┐
│  Setup & Train  │
│                 │
│  - Install deps │
│  - python train.py
└────────┬────────┘
         │
         │ Every epoch
         ▼
┌─────────────────┐
│ Upload to S3    │
│                 │
│ s3://qbia/models/
└────────┬────────┘
         │
         │ download_models_from_s3.sh
         ▼
┌─────────────────┐
│  Local Machine  │
│                 │
│  Downloaded     │
│  model ready    │
└─────────────────┘
```

### Composants AWS Utilisés

1. **EC2 (Elastic Compute Cloud)**
   - Instances GPU pour l'entraînement
   - AMI Deep Learning avec CUDA/PyTorch pré-installé
   - Auto-scaling possible

2. **S3 (Simple Storage Service)**
   - Stockage du code source
   - Stockage des données d'entraînement (parquet)
   - Stockage des modèles entraînés
   - Lifecycle policies pour archivage automatique

3. **IAM (Identity and Access Management)**
   - Rôle `EC2S3AccessRole` pour accès S3 depuis EC2
   - Policies granulaires (read/write séparés)

4. **VPC (Virtual Private Cloud)**
   - Security Group pour restreindre l'accès SSH
   - Réseau isolé

5. **CloudWatch (Optionnel)**
   - Logs centralisés
   - Métriques GPU/CPU
   - Alertes de budget

### Sécurité

#### Clés SSH
- Générées automatiquement lors du premier lancement
- Stockées dans `~/.ssh/trading-ml-key.pem`
- Permissions: 400 (lecture seule pour propriétaire)

#### Security Group
- SSH (port 22) autorisé uniquement depuis votre IP
- Créé automatiquement avec `curl https://checkip.amazonaws.com`
- Aucun autre port ouvert

#### IAM Role
- **Principe du moindre privilège**
- Read-only sur `s3://qbia/code/*` et `s3://qbia/bourse/*`
- Write-only sur `s3://qbia/models/*`
- Aucun accès à d'autres services AWS

#### Bonnes Pratiques
- ✅ Ne jamais commiter les clés SSH dans Git
- ✅ Utiliser AWS Secrets Manager pour les credentials API
- ✅ Configurer MFA sur votre compte AWS
- ✅ Activer CloudTrail pour l'audit
- ✅ Utiliser des tags pour tracer les ressources

---

## ❓ FAQ

### Q1: Puis-je lancer plusieurs entraînements en parallèle ?

**Oui**, chaque lancement crée une nouvelle instance EC2 indépendante.

```bash
# Terminal 1
./scripts/launch_aws_training.sh train_s3_optimized g4dn.xlarge

# Terminal 2 (quelques minutes plus tard)
./scripts/launch_aws_training.sh train_s3_light g4dn.xlarge
```

**Attention**: Le coût sera multiplié par le nombre d'instances actives.

### Q2: Que se passe-t-il si mon ordinateur s'éteint ?

**Rien !** L'entraînement continue sur AWS EC2.

L'instance est autonome et indépendante de votre machine locale. Vous pouvez:
- Éteindre votre ordinateur
- Fermer le terminal
- Perdre la connexion Internet

Pour reconnecter plus tard:
```bash
# Récupérer l'instance ID
cat /tmp/aws_training_instance.json

# Monitorer
./scripts/monitor_aws_training.sh
```

### Q3: Comment reprendre un entraînement interrompu ?

Si l'entraînement crash ou est interrompu:

1. **Télécharger le dernier checkpoint depuis S3**:
```bash
aws s3 sync s3://qbia/models/trading/train_s3_optimized_20241214_153045/ ./checkpoints_resume/
```

2. **Modifier `train.py` pour reprendre depuis le checkpoint**:
```python
# Dans train.py, ajouter:
if os.path.exists('checkpoints_resume/checkpoint_epoch_20.pt'):
    checkpoint = torch.load('checkpoints_resume/checkpoint_epoch_20.pt')
    model.load_state_dict(checkpoint['model_state_dict'])
    optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    start_epoch = checkpoint['epoch'] + 1
```

3. **Relancer l'entraînement**:
```bash
./scripts/launch_aws_training.sh train_s3_optimized g4dn.xlarge
```

### Q4: Puis-je utiliser mes propres données ?

**Oui**, uploadez vos données dans S3 et modifiez le fichier de configuration:

```yaml
# configs/train_custom.yaml
data:
  data_source: "s3"
  s3_bucket: "qbia"
  s3_prefix: "bourse/my_custom_data"  # Changer ici
  symbols_filter:
    - "MY_SYMBOL_1"
    - "MY_SYMBOL_2"
```

**Format attendu**: Parquet avec colonnes `[timestamp, open, high, low, close, volume]`

### Q5: Comment comparer les performances de différents modèles ?

```bash
# Télécharger tous les modèles
./scripts/download_models_from_s3.sh --compare

# Analyser les logs
for model in downloaded_models/*/; do
  echo "=== $(basename $model) ==="
  grep "Final validation" "$model/training.log" | tail -n 1
done
```

Pour une comparaison plus poussée, créer un script Python:
```python
import json
import glob

models = glob.glob('downloaded_models/*/metrics.json')
for model_path in models:
    with open(model_path) as f:
        metrics = json.load(f)
        print(f"{model_path}: val_loss={metrics['val_loss']}, val_acc={metrics['val_accuracy']}")
```

### Q6: Quelle est la différence entre STOP et TERMINATE ?

| Commande | Effet | Coût | Peut Redémarrer ? |
|----------|-------|------|------------------|
| `stop-instances` | Arrête l'instance | Stockage EBS (~$0.10/jour) | Oui |
| `terminate-instances` | Supprime l'instance | $0 | Non |

**Recommandation**: Toujours utiliser `terminate-instances` après l'entraînement, car les modèles sont déjà dans S3.

### Q7: Puis-je utiliser une région AWS différente ?

**Oui**, modifier la variable d'environnement:

```bash
export AWS_REGION="eu-west-1"
./scripts/launch_aws_training.sh
```

**Note**: Les prix varient selon les régions. `us-east-1` est généralement la moins chère.

### Q8: Comment réduire le coût au maximum ?

1. **Utiliser Spot Instances** (-70%)
2. **Choisir train_s3_light** au lieu de train_s3
3. **Terminer l'instance immédiatement** après l'entraînement
4. **Utiliser t3.large** (pas de GPU) pour les tests rapides
5. **Entraîner uniquement sur Bitcoin** pour débuter

**Coût minimal pour un entraînement réel**: ~$0.20 (Spot + train_s3_light)

### Q9: Les données sont-elles téléchargées à chaque entraînement ?

**Non**, le système utilise un cache local sur l'instance EC2:

1. **Premier lancement**: Télécharge les données depuis S3 vers `/tmp/trading_data_cache/`
2. **Lancements suivants**: Utilise le cache

**Mais**: À chaque nouvelle instance, le cache est vide. Pour optimiser:
- Créer un snapshot EBS du cache
- Ou uploader un cache pré-rempli dans S3

### Q10: Puis-je utiliser mon propre modèle ?

**Oui**, modifier `ai/models/multi_modal_trading.py` et uploader:

```bash
# Modifier le modèle
vim ai/models/multi_modal_trading.py

# Relancer (le nouveau code sera uploadé automatiquement)
./scripts/launch_aws_training.sh
```

---

## 📚 Ressources Supplémentaires

### Documentation AWS
- [EC2 Pricing](https://aws.amazon.com/ec2/pricing/)
- [S3 Pricing](https://aws.amazon.com/s3/pricing/)
- [Deep Learning AMI](https://aws.amazon.com/machine-learning/amis/)
- [Spot Instances](https://aws.amazon.com/ec2/spot/)

### PyTorch & ML
- [PyTorch Documentation](https://pytorch.org/docs/)
- [Transformer Architecture](https://arxiv.org/abs/1706.03762)
- [Time Series Forecasting](https://pytorch.org/tutorials/beginner/transformer_tutorial.html)

### Fichiers du Projet
- `train.py` - Script d'entraînement principal
- `configs/` - Configurations d'entraînement
- `scripts/launch_aws_training.sh` - Lancement automatisé
- `scripts/monitor_aws_training.sh` - Monitoring interactif
- `scripts/download_models_from_s3.sh` - Téléchargement des modèles

---

## 🎯 Quick Start Checklist

- [ ] AWS CLI installé (`brew install awscli`)
- [ ] AWS configuré (`aws configure`)
- [ ] Infrastructure créée (`./scripts/setup_aws_infrastructure.sh`)
- [ ] Test lancé (`./scripts/launch_aws_training.sh train_s3_tiny t3.large`)
- [ ] Monitoring actif (`./scripts/monitor_aws_training.sh`)
- [ ] Modèle téléchargé (`./scripts/download_models_from_s3.sh --latest`)
- [ ] Instance terminée (via script de monitoring)

---

## 📞 Support

En cas de problème:

1. **Vérifier les logs**:
   ```bash
   ./scripts/monitor_aws_training.sh
   # Option 2: Voir tous les logs
   ```

2. **Vérifier la section Troubleshooting** de ce guide

3. **Vérifier les coûts AWS**:
   ```bash
   aws ce get-cost-and-usage \
       --time-period Start=2024-12-01,End=2024-12-15 \
       --granularity MONTHLY \
       --metrics BlendedCost
   ```

4. **Créer une issue** sur le repository du projet

---

**Bonne chance avec vos entraînements ! 🚀**
