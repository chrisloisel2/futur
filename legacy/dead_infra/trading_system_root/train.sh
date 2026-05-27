#!/bin/bash
# UNIFIED PRODUCTION TRAINER LAUNCHER

set -e

cd "$(dirname "$0")"

# Default parameters
SYMBOL="${SYMBOL:-BTCUSDT}"
START_DATE="${START_DATE:-2023-01-01}"
END_DATE="${END_DATE:-2025-12-31}"
DEVICE="${DEVICE:-cpu}"
RUN_ID="${RUN_ID:-production_$(date +%Y%m%d_%H%M%S)}"

echo "========================================"
echo "UNIFIED PRODUCTION TRAINING"
echo "========================================"
echo "Symbol:     $SYMBOL"
echo "Period:     $START_DATE → $END_DATE"
echo "Device:     $DEVICE"
echo "Run ID:     $RUN_ID"
echo "========================================"
echo ""

python3 train.py \
  --symbol "$SYMBOL" \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --device "$DEVICE" \
  --run-id "$RUN_ID" \
  "$@"

echo ""
echo "========================================"
echo "TRAINING COMPLETE"
echo "========================================"
echo "Models saved to: artifacts/models/$RUN_ID"
