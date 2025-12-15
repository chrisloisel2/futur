#!/bin/bash
# Test rapide de stabilité après emergency fix

set -e

echo "=========================================="
echo "TRM STABILITY TEST"
echo "=========================================="
echo ""
echo "Configuration: config_stable.yaml"
echo "Epochs: 3 (quick validation)"
echo "Expected: No gradient explosions"
echo ""
echo "=========================================="
echo ""

cd /home/qbee/Bureau/Bourse/futur/ai/TRAIN/trm

# Clean old logs
rm -f logs/trm_training_stable.log

# Run test
python3 train_trm.py \
  --config config_stable.yaml \
  --epochs 3 \
  --device auto

echo ""
echo "=========================================="
echo "TEST COMPLETED"
echo "=========================================="
echo ""
echo "Check logs/trm_training_stable.log for:"
echo "  ✓ [WARMUP] messages"
echo "  ✓ grad_norm < 0.5"
echo "  ✓ No GRADIENT EXPLOSION errors"
echo "  ✓ Predictions in [-0.01, 0.01]"
echo ""
