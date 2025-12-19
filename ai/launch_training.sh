#!/bin/bash
#
# Script de lancement pour l'entraînement avancé
# Usage: ./launch_training.sh [config_file]
#

set -e  # Exit on error

echo "=========================================="
echo "  Advanced Training Pipeline Launcher"
echo "=========================================="
echo ""

# Configuration file
CONFIG_FILE="${1:-ai/configs/train_advanced.yaml}"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Error: Configuration file not found: $CONFIG_FILE"
    echo ""
    echo "Usage: $0 [config_file]"
    echo "Example: $0 ai/configs/train_advanced.yaml"
    exit 1
fi

echo "✓ Configuration: $CONFIG_FILE"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 not found"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "✓ Python: $PYTHON_VERSION"

# Check TensorFlow
if ! python3 -c "import tensorflow" 2>/dev/null; then
    echo "⚠️  Warning: TensorFlow not found"
    echo "   Install with: pip install -r ai/requirements_training.txt"
    read -p "   Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check GPU
echo ""
echo "Checking GPU availability..."
python3 -c "import tensorflow as tf; gpus = tf.config.list_physical_devices('GPU'); print(f'✓ Found {len(gpus)} GPU(s)') if gpus else print('⚠️  No GPU found, training will use CPU')"
echo ""

# Create output directory
OUTPUT_DIR="training_output"
mkdir -p "$OUTPUT_DIR"
echo "✓ Output directory: $OUTPUT_DIR"
echo ""

# Confirm before starting
read -p "Start training? (Y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Nn]$ ]]; then
    echo "Training cancelled."
    exit 0
fi

echo ""
echo "=========================================="
echo "  Starting Training..."
echo "=========================================="
echo ""

# Run training
python3 ai/train_advanced.py --config "$CONFIG_FILE"

# Training complete
echo ""
echo "=========================================="
echo "  Training Complete!"
echo "=========================================="
echo ""
echo "📊 View results:"
echo "   - Metrics: $OUTPUT_DIR/metrics/"
echo "   - Logs: $OUTPUT_DIR/logs/"
echo ""
echo "📈 Launch TensorBoard:"
echo "   tensorboard --logdir=$OUTPUT_DIR/tensorboard/ --port=6006"
echo "   Then open: http://localhost:6006"
echo ""
