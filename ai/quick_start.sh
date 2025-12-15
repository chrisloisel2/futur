#!/bin/bash
# Quick start script pour l'entraînement avec données S3

set -e  # Exit on error

echo "========================================="
echo "   Quick Start - Entraînement avec S3"
echo "========================================="
echo ""

# Couleurs pour l'output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Paramètres d'optimisation (ajustés par les questions interactives)
TRAIN_DEVICE="auto"
TRAIN_PREFIX=()
TRAIN_EXTRA_ARGS=()
MPS_MEMORY_FRACTION=""
MATMUL_PRECISION="high"
CPU_THREADS_LIMIT=""
USE_LIGHT_CONFIG=1
CONFIG_MAIN_LIGHT="configs/train_s3_light.yaml"
CONFIG_MAIN_FULL="configs/train_s3.yaml"

# Fonction pour afficher les étapes
step() {
    echo -e "${BLUE}[ÉTAPE]${NC} $1"
}

success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

info() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

# Optimisations spécifiques Mac/M3
apply_mac_m3_optimizations() {
    local cpu_brand="Inconnu"
    if command -v sysctl &>/dev/null; then
        cpu_brand=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Inconnu")
    fi

    info "CPU détecté : ${cpu_brand}"
    read -p "Activer les optimisations Mac puce M3 (MPS, limite mémoire) ? (y/N) : " confirm_m3
    if [[ $confirm_m3 == [yY] ]]; then
        TRAIN_DEVICE="mps"
        MPS_MEMORY_FRACTION="${MPS_MEMORY_FRACTION:-0.60}"
        export PYTORCH_ENABLE_MPS_FALLBACK=1
        TRAIN_EXTRA_ARGS+=(--mps_memory_fraction "$MPS_MEMORY_FRACTION")
        info "Optimisations M3 activées (device=mps, mémoire max=${MPS_MEMORY_FRACTION})"
    else
        TRAIN_DEVICE="auto"
    fi
}

# Limiter l'utilisation CPU par pourcentage (approx via threads)
configure_cpu_limit() {
    read -p "Limiter l'entraînement à quel pourcentage max de CPU ? (laisser vide pour aucune limite) : " cpu_pct
    cpu_pct=${cpu_pct//[^0-9]/}
    if [[ -z "$cpu_pct" ]]; then
        return
    fi

    if ((cpu_pct <= 0 || cpu_pct > 100)); then
        info "Valeur CPU invalide (${cpu_pct}%), aucune limite appliquée."
        return
    fi

    local cores
    cores=$(sysctl -n hw.logicalcpu 2>/dev/null || nproc || echo 8)
    local threads=$(( (cpu_pct * cores + 99) / 100 ))
    if ((threads < 1)); then
        threads=1
    fi

    CPU_THREADS_LIMIT=$threads
    TRAIN_EXTRA_ARGS+=(--cpu_threads "$CPU_THREADS_LIMIT")
    TRAIN_PREFIX=(nice -n 10)

    export OMP_NUM_THREADS="$CPU_THREADS_LIMIT"
    export MKL_NUM_THREADS="$CPU_THREADS_LIMIT"
    export VECLIB_MAXIMUM_THREADS="$CPU_THREADS_LIMIT"
    export NUMEXPR_MAX_THREADS="$CPU_THREADS_LIMIT"
    export PYTORCH_NUM_THREADS="$CPU_THREADS_LIMIT"
    export UV_THREADPOOL_SIZE="$CPU_THREADS_LIMIT"

    info "Limite CPU : ${cpu_pct}% (~${CPU_THREADS_LIMIT} threads) avec priorité réduite (nice)."
}

# Activer les optimisations math Torch pour de meilleurs résultats
enable_precision_optimizations() {
    TRAIN_EXTRA_ARGS+=(--matmul_precision "$MATMUL_PRECISION")
    info "Précision matricielle ${MATMUL_PRECISION} activée (torch.set_float32_matmul_precision)."
}

# Helper pour lancer l'entraînement avec les paramètres communs
run_train() {
    local config_path="$1"
    shift
    PYTHONPATH=/Users/christopher/Desktop/futur/ai/TRAIN:$PYTHONPATH \
        "${TRAIN_PREFIX[@]}" \
        python3 train.py \
        --config "$config_path" \
        --device "$TRAIN_DEVICE" \
        --log_level INFO \
        "${TRAIN_EXTRA_ARGS[@]}" \
        "$@"
}

# Vérifier les dépendances
step "Vérification des dépendances..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 n'est pas installé"
    exit 1
fi
success "Python3 trouvé"

# Vérifier boto3
if ! python3 -c "import boto3" 2>/dev/null; then
    echo "❌ boto3 n'est pas installé"
    info "Installation: pip install boto3"
    exit 1
fi
success "boto3 installé"

# Vérifier les credentials AWS
step "Vérification des credentials AWS..."

# Méthode 1: Test avec Python et boto3 (plus fiable)
if python3 -c "
import boto3
import sys
try:
    s3 = boto3.client('s3')
    s3.head_bucket(Bucket='qbia')
    sys.exit(0)
except Exception as e:
    sys.exit(1)
" 2>/dev/null; then
    success "Accès S3 OK"
else
    # Méthode 2: Test avec aws CLI si disponible
    if command -v aws &> /dev/null; then
        if aws s3 ls s3://qbia/bourse/mintrad/ --max-items 1 &>/dev/null; then
            success "Accès S3 OK"
        else
            echo "❌ Impossible d'accéder au bucket S3"
            info "Vérifiez vos credentials AWS"
            info "Option 1: export AWS_PROFILE=votre_profile"
            info "Option 2: aws configure"
            exit 1
        fi
    else
        echo "❌ Impossible de vérifier l'accès S3"
        info "aws CLI non trouvé, mais boto3 est installé"
        info "Les tests devraient fonctionner si vos credentials sont configurés"
        read -p "Continuer quand même? (y/N) : " confirm
        if [[ $confirm != [yY] ]]; then
            exit 1
        fi
    fi
fi

echo ""
echo "========================================="
echo "   Optimisations & ressources"
echo "========================================="
echo ""
apply_mac_m3_optimizations
configure_cpu_limit
enable_precision_optimizations
read -p "Utiliser la configuration allégée (RAM réduite, 3 symboles, 2024) ? (Y/n) : " use_light
if [[ $use_light == [nN] ]]; then
    USE_LIGHT_CONFIG=0
    info "Configuration complète sélectionnée (plus lourde)."
else
    USE_LIGHT_CONFIG=1
    info "Configuration allégée sélectionnée (recommandé pour éviter le crash mémoire)."
fi

echo ""
echo "========================================="
echo "   Options de test/entraînement"
echo "========================================="
echo ""
echo "Que voulez-vous faire ?"
echo ""
echo "  1) Test rapide du chargement S3"
echo "  2) Test du pipeline complet"
echo "  3) Entraînement DEBUG (1 epoch, 2 symboles, 2024)"
echo "  4) Entraînement QUICK (10 epochs, 5 symboles, 2023-2024)"
echo "  5) Entraînement COMPLET (50 epochs, 8 symboles, 2020-2024)"
echo ""
read -p "Votre choix (1-5) : " choice

case $choice in
    1)
        step "Test du chargement S3..."
        cd /Users/christopher/Desktop/futur
        python3 ai/test_s3_data_source.py
        success "Test S3 terminé!"
        ;;

    2)
        step "Test du pipeline complet..."
        cd /Users/christopher/Desktop/futur
        python3 ai/test_pipeline_s3.py
        success "Test pipeline terminé!"
        ;;

    3)
        step "Lancement de l'entraînement DEBUG..."
        if [[ $USE_LIGHT_CONFIG -eq 1 ]]; then
            info "Mode allégé: 3 symboles (2024), 1 epoch (debug)"
        else
            info "Mode complet: configuration principale, 1 epoch (debug)"
        fi
        cd /Users/christopher/Desktop/futur/ai
        if [[ $USE_LIGHT_CONFIG -eq 1 ]]; then
            info "Mode allégé actif -> utilisation de ${CONFIG_MAIN_LIGHT}"
            run_train "$CONFIG_MAIN_LIGHT" --debug_mode --fast_dev_run
        else
            run_train "$CONFIG_MAIN_FULL" --debug_mode
        fi
        success "Entraînement DEBUG terminé!"
        ;;

    4)
        step "Lancement de l'entraînement QUICK..."
        if [[ $USE_LIGHT_CONFIG -eq 1 ]]; then
            info "Mode allégé: 3 symboles, 2024, batch 16, 10 epochs"
        else
            info "Configuration: 5 symboles, 2023-2024, 10 epochs"
        fi

        cd /Users/christopher/Desktop/futur/ai

        if [[ $USE_LIGHT_CONFIG -eq 1 ]]; then
            info "Mode allégé actif -> utilisation de ${CONFIG_MAIN_LIGHT} (3 symboles, 2024, batch 16)"
            run_train "$CONFIG_MAIN_LIGHT"
        else
            # Créer config temporaire
            cat > /tmp/train_quick.yaml << 'EOF'
data:
  data_source: "s3"
  s3_bucket: "qbia"
  s3_prefix: "bourse/mintrad"
  start_year: 2023
  end_year: 2024
  symbols_filter:
    - "BTCUSDT"
    - "ETHUSDT"
    - "BNBUSDT"
    - "SOLUSDT"
    - "XRPUSDT"
  local_cache_dir: "/tmp/trading_data_cache"
  train_split: 0.7
  val_split: 0.15
  test_split: 0.15
  lookback_window: 100
  feature_dim: 52
  batch_size: 32
  shuffle: true
  use_synthetic_data: false

model:
  type: "multi_modal"
  params:
    d_model: 512
    n_heads: 8
    n_layers: 6
    dropout: 0.1
    feature_dim: 52

training:
  epochs: 10
  learning_rate: 0.0001
  checkpoint_dir: "checkpoints_quick"
  gradient_accumulation_steps: 4
EOF

            run_train /tmp/train_quick.yaml

            rm /tmp/train_quick.yaml
        fi
        success "Entraînement QUICK terminé!"
        info "Checkpoints sauvegardés dans: checkpoints_quick/"
        ;;

    5)
        step "Lancement de l'entraînement COMPLET..."
        if [[ $USE_LIGHT_CONFIG -eq 1 ]]; then
            info "Mode allégé: 3 symboles, 2024, 20 epochs (config light)"
        else
            info "Configuration: 8 symboles, 2020-2024, 50 epochs"
        fi
        info "⚠️  Cela peut prendre plusieurs heures!"

        read -p "Êtes-vous sûr? (y/N) : " confirm
        if [[ $confirm != [yY] ]]; then
            echo "Annulé."
            exit 0
        fi

        cd /Users/christopher/Desktop/futur/ai
        if [[ $USE_LIGHT_CONFIG -eq 1 ]]; then
            info "Mode allégé actif -> utilisation de ${CONFIG_MAIN_LIGHT}"
            run_train "$CONFIG_MAIN_LIGHT"
        else
            run_train "$CONFIG_MAIN_FULL"
        fi

        success "Entraînement COMPLET terminé!"
        info "Checkpoints sauvegardés dans: checkpoints_s3/"
        ;;

    *)
        echo "❌ Choix invalide"
        exit 1
        ;;
esac

echo ""
echo "========================================="
echo -e "${GREEN}   ✓ Terminé avec succès!${NC}"
echo "========================================="
