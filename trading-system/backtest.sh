#!/bin/bash
# Quick Backtest Script with proper PYTHONPATH

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

echo "=========================================="
echo "Running Backtest on Mock Data"
echo "=========================================="
echo ""

python -m src.app.main backtest \
  --start-date 2024-01-01 \
  --end-date 2024-12-01 \
  --symbols BTCUSDT \
  --config configs/base.yaml

echo ""
echo "=========================================="
echo "Backtest Results"
echo "=========================================="
echo ""

# Find latest backtest results
LATEST_DIR=$(ls -td artifacts/backtests/backtest_* 2>/dev/null | head -1)

if [ -n "$LATEST_DIR" ]; then
    echo "📊 Metrics from latest backtest:"
    echo ""
    cat "$LATEST_DIR/metrics.json" | python -m json.tool
    echo ""
    echo "✅ Results saved to: $LATEST_DIR"
else
    echo "❌ No backtest results found"
fi
