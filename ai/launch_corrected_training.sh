#!/bin/bash
#
# Lance l'entraînement avec le modèle corrigé
# IMPORTANT: Supprime les anciens windows car la structure a changé
#

set -e

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Entraînement avec Modèle Mathématiquement Corrigé            ║"
echo "║  Direction: Binaire (UP/DOWN)                                  ║"
echo "║  RV: Agrégée scalaire (RMS)                                    ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Clean ALL previous outputs (important!)
echo "🧹 Nettoyage des anciens outputs..."

if [ -d "training_output" ]; then
    echo "   Suppression: training_output/"
    rm -rf training_output/
fi

if [ -d "training_output_returns_only" ]; then
    echo "   Suppression: training_output_returns_only/"
    rm -rf training_output_returns_only/
fi

if [ -d "training_output_corrected" ]; then
    echo "   Suppression: training_output_corrected/"
    rm -rf training_output_corrected/
fi

echo "✅ Nettoyage terminé"
echo ""

# Configuration
CONFIG="ai/configs/train_corrected.yaml"

if [ ! -f "$CONFIG" ]; then
    echo "❌ Error: Config file not found: $CONFIG"
    exit 1
fi

echo "📄 Configuration: $CONFIG"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 not found"
    exit 1
fi

echo "✓ Python: $(python3 --version)"
echo ""

# Show config summary
echo "══════════════════════════════════════════════════════════════"
echo "  Configuration Corrigée"
echo "══════════════════════════════════════════════════════════════"
echo ""
echo "Corrections Mathématiques:"
echo "  ✓ Direction: Binaire (UP=1 si cum>=0, DOWN=0 sinon)"
echo "  ✓ Suppression classe FLAT (était 7% des données)"
echo "  ✓ RV: Agrégée scalaire (RMS des RV futures)"
echo "  ✓ Loss RV: Huber avec clipping (stable)"
echo ""
echo "Model:"
echo "  - d_model: 128 (restored)"
echo "  - n_heads: 4 (restored)"
echo "  - dropout: 0.15"
echo ""
echo "Training:"
echo "  - lr: 0.0003 (stable)"
echo "  - weight_decay: 0.0001"
echo "  - batch_size: 128"
echo "  - epochs: 20"
echo ""
echo "Loss Weights:"
echo "  - w_ret: 1.0"
echo "  - w_dir: 0.8 (reduced from 1.5)"
echo "  - w_rv: 0.3 (reduced from 0.4)"
echo ""
echo "══════════════════════════════════════════════════════════════"
echo ""

# Confirm
read -p "Start training? (Y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Nn]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  Starting Training..."
echo "══════════════════════════════════════════════════════════════"
echo ""

# Set PYTHONPATH
export PYTHONPATH="/Users/christopher/Desktop/futur:$PYTHONPATH"

# Run training
python3 ai/train_advanced.py --config "$CONFIG"

# Training complete
EXIT_CODE=$?

echo ""
echo "══════════════════════════════════════════════════════════════"
if [ $EXIT_CODE -eq 0 ]; then
    echo "  ✅ Training Complete!"
else
    echo "  ❌ Training Failed (exit code: $EXIT_CODE)"
fi
echo "══════════════════════════════════════════════════════════════"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo "📊 Results:"
    echo "   - Output dir: training_output_corrected/"
    echo "   - Metrics: training_output_corrected/metrics/"
    echo "   - Logs: training_output_corrected/logs/"
    echo ""
    echo "📈 Launch TensorBoard:"
    echo "   tensorboard --logdir=training_output_corrected/tensorboard/ --port=6006"
    echo "   Then open: http://localhost:6006"
    echo ""
    echo "📋 Métriques attendues (Epoch 1):"
    echo "   ✅ dir_accuracy >= 0.53 (50% + marge statistique)"
    echo "   ✅ loss train ≈ loss val (pas d'overfitting)"
    echo "   ✅ ret_mae < 0.02"
    echo ""
    echo "🎯 Si dir_accuracy < 0.53 après 3 epochs → STOP"
    echo "   (Le modèle n'apprend pas de signal directionnel)"
    echo ""
fi

exit $EXIT_CODE
