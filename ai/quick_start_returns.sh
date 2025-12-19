#!/bin/bash
#
# Quick Start - Returns Only Training
# Lance l'entraînement avec la config optimisée (direction désactivée)
#

set -e

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Quick Start - Returns Prediction Training                    ║"
echo "║  (Direction désactivée pour diagnostic)                        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Clean previous output
if [ -d "training_output" ]; then
    echo "🧹 Cleaning previous training_output..."
    rm -rf training_output/
fi

if [ -d "training_output_returns_only" ]; then
    echo "🧹 Cleaning previous training_output_returns_only..."
    rm -rf training_output_returns_only/
fi

echo "✅ Clean"
echo ""

# Configuration
CONFIG="ai/configs/train_returns_only.yaml"

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
echo "  Configuration Summary"
echo "══════════════════════════════════════════════════════════════"
echo ""
echo "Model:"
echo "  - d_model: 128 (restored from 64)"
echo "  - n_heads: 4 (restored from 2)"
echo "  - dropout: 0.15 (reduced from 0.20)"
echo ""
echo "Training:"
echo "  - lr: 0.0003 (reduced from 0.001)"
echo "  - weight_decay: 0.0001 (reduced from 0.001)"
echo "  - batch_size: 128"
echo "  - epochs: 20"
echo ""
echo "Loss Weights:"
echo "  - w_ret: 1.0 (ENABLED)"
echo "  - w_dir: 0.0 (DISABLED - focus on returns)"
echo "  - w_rv: 0.0 (DISABLED)"
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
    echo "   - Output dir: training_output_returns_only/"
    echo "   - Metrics: training_output_returns_only/metrics/"
    echo "   - Logs: training_output_returns_only/logs/"
    echo ""
    echo "📈 Launch TensorBoard:"
    echo "   tensorboard --logdir=training_output_returns_only/tensorboard/ --port=6006"
    echo "   Then open: http://localhost:6006"
    echo ""
    echo "📋 Check metrics:"
    echo "   cat training_output_returns_only/metrics/final_metrics.json"
    echo ""
    echo "✅ Si ret_mae < 0.01 et stable → Succès!"
    echo "   Prochaine étape: Réactiver direction avec class_weight"
    echo ""
fi

exit $EXIT_CODE
