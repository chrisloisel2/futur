#!/bin/bash
# Script utilitaire pour l'entraînement optimisé

set -e  # Exit on any error

echo "🚀 TRAINING UTILITY SCRIPT"
echo "=========================="

# Fonction d'aide
show_help() {
    echo "Usage: $0 [mode] [options]"
    echo ""
    echo "Modes:"
    echo "  normal      - Entraînement normal avec config optimisée"
    echo "  overfit     - Mode debug overfit (256 samples, config spécifique)"
    echo "  custom      - Config personnalisée (spécifiez --config)"
    echo ""
    echo "Options:"
    echo "  --config PATH       - Fichier de config JSON personnalisé"
    echo "  --symbol SYMBOL     - Symbole à trader (défaut: BTCUSDT)"
    echo "  --device DEVICE     - Device (cpu/cuda/mps)"
    echo "  --run-id ID         - ID de run personnalisé"
    echo "  --help              - Affiche cette aide"
    echo ""
    echo "Exemples:"
    echo "  $0 normal                          # Entraînement optimisé standard"
    echo "  $0 overfit                         # Test d'overfitting"
    echo "  $0 custom --config my_config.json  # Config personnalisée"
    echo "  $0 normal --symbol ETHUSDT         # Autre symbole"
}

# Valeurs par défaut
MODE="normal"
CONFIG=""
SYMBOL=""
DEVICE=""
RUN_ID=""
EXTRA_ARGS=""

# Parse des arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        normal|overfit|custom)
            MODE="$1"
            shift
            ;;
        --config)
            CONFIG="$2"
            shift 2
            ;;
        --symbol)
            SYMBOL="$2"
            shift 2
            ;;
        --device)
            DEVICE="$2"
            shift 2
            ;;
        --run-id)
            RUN_ID="$2"
            shift 2
            ;;
        --help)
            show_help
            exit 0
            ;;
        *)
            echo "❌ Argument inconnu: $1"
            show_help
            exit 1
            ;;
    esac
done

# Configuration selon le mode
case $MODE in
    normal)
        echo "📈 Mode: Entraînement optimisé normal"
        CONFIG="config_optimized.json"
        if [ -z "$RUN_ID" ]; then
            RUN_ID="optimized_$(date +%Y%m%d_%H%M%S)"
        fi
        ;;
    overfit)
        echo "🔬 Mode: Debug overfit (test de sanité)"
        EXTRA_ARGS="--debug-overfit"
        CONFIG="config_debug_overfit.json"
        if [ -z "$RUN_ID" ]; then
            RUN_ID="debug_overfit_$(date +%Y%m%d_%H%M%S)"
        fi
        echo "⚠️  Mode debug: 256 samples, dropout=0, weight_decay=0, high grad_clip"
        echo "   Objectif: loss doit tendre vers 0 en quelques centaines d'epochs"
        ;;
    custom)
        echo "⚙️  Mode: Configuration personnalisée"
        if [ -z "$CONFIG" ]; then
            echo "❌ Mode custom nécessite --config"
            exit 1
        fi
        if [ -z "$RUN_ID" ]; then
            RUN_ID="custom_$(date +%Y%m%d_%H%M%S)"
        fi
        ;;
esac

# Vérification que le fichier de config existe
if [ ! -f "$CONFIG" ]; then
    echo "❌ Fichier de configuration introuvable: $CONFIG"
    exit 1
fi

# Construction de la commande
CMD="python3 train.py"
CMD="$CMD --config $CONFIG"
CMD="$CMD --run-id $RUN_ID"

if [ ! -z "$SYMBOL" ]; then
    CMD="$CMD --symbol $SYMBOL"
fi

if [ ! -z "$DEVICE" ]; then
    CMD="$CMD --device $DEVICE"
fi

CMD="$CMD $EXTRA_ARGS"

echo ""
echo "📋 Configuration:"
echo "   Mode:      $MODE"
echo "   Config:    $CONFIG"
echo "   Run ID:    $RUN_ID"
echo "   Symbol:    ${SYMBOL:-'BTCUSDT (défaut)'}"
echo "   Device:    ${DEVICE:-'auto-détection'}"
echo ""
echo "💻 Commande:"
echo "   $CMD"
echo ""

# Demande de confirmation
read -p "🔥 Lancer l'entraînement? (y/N): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Annulé par l'utilisateur"
    exit 0
fi

echo ""
echo "🚀 DÉMARRAGE DE L'ENTRAÎNEMENT..."
echo "================================="

# Exécution
exec $CMD
