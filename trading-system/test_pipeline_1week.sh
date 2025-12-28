#!/bin/bash
export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

echo "=========================================="
echo "Testing Production Pipeline (1 Week)"
echo "=========================================="
echo ""

python -m src.app.main backtest \
  --start-date 2024-01-01 \
  --end-date 2024-01-07 \
  --symbols BTCUSDT \
  --config configs/base.yaml

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    python view_results.py
fi

exit $EXIT_CODE
