#!/bin/bash
#
# Script de nettoyage pour le serveur
# À exécuter AVANT de relancer l'entraînement
#

set -e

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Nettoyage des Windows Incompatibles                          ║"
echo "║  CRITICAL: Les anciens NPZ ont y_rv: [N,12] au lieu de [N]    ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Change to project directory
cd "$(dirname "$0")"
echo "Working directory: $(pwd)"
echo ""

# Liste des dossiers à supprimer
DIRS=(
    "training_output_corrected"
)

echo "🔍 Checking for incompatible windows..."
echo ""

FOUND=0
for DIR in "${DIRS[@]}"; do
    if [ -d "$DIR" ]; then
        echo "  ❌ Found: $DIR/"
        FOUND=1
    fi
done

if [ $FOUND -eq 0 ]; then
    echo "  ✅ No incompatible windows found"
    echo ""
    echo "You can proceed with training:"
    echo "  python3 ai/train_advanced.py --config ai/configs/train_corrected.yaml"
    echo ""
    exit 0
fi

echo ""
echo "⚠️  WARNING: These directories contain incompatible windows!"
echo "   Old structure: y_rv is [N, 12]"
echo "   New structure: y_rv is [N] (scalar)"
echo ""
echo "Files to delete:"
for DIR in "${DIRS[@]}"; do
    if [ -d "$DIR" ]; then
        echo "  - $DIR/"
    fi
done
echo ""

# Confirm deletion
read -p "Delete these directories? This will force regeneration. (Y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]] && [[ ! -z $REPLY ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo "🧹 Deleting incompatible windows..."
echo ""

for DIR in "${DIRS[@]}"; do
    if [ -d "$DIR" ]; then
        echo "  Deleting: $DIR/"
        rm -rf "$DIR"
        echo "  ✅ Deleted"
    fi
done

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  ✅ Cleanup Complete!                                          ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. The corrected data_pipeline_memory_efficient.py is in place"
echo "  2. Run training (windows will be regenerated with correct structure):"
echo ""
echo "     python3 ai/train_advanced.py --config ai/configs/train_corrected.yaml"
echo ""
echo "  3. Expected behavior:"
echo "     - Phase 2: Fit scaler (or load existing)"
echo "     - Phase 3: Create windows with y_rv: [N] (scalar)"
echo "     - Phase 4: Dataset will show rv: TensorSpec(shape=(), ...)"
echo "     - Training will start without dimension errors"
echo ""
echo "  4. Monitor first epoch:"
echo "     ✅ dir_accuracy >= 0.53"
echo "     ✅ No ValueError about dimensions"
echo ""
