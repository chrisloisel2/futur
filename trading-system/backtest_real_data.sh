#!/bin/bash
# Backtest with Real S3 Data

export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

echo "=========================================="
echo "Running Backtest on REAL S3 Data"
echo "=========================================="
echo ""
echo "📊 Data source: s3://qbia/bourse/processed/market/"
echo "📈 Symbol: BTCUSDT"
echo "📅 Period: 2024-01-01 to 2024-12-01"
echo ""

python -m src.app.main backtest \
  --start-date 2024-01-01 \
  --end-date 2024-12-01 \
  --symbols BTCUSDT \
  --config configs/base.yaml

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "Backtest Results (Real Data)"
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
        echo ""
        echo "🔍 To view formatted results:"
        echo "   python view_results.py"
    else
        echo "❌ No backtest results found"
    fi
else
    echo ""
    echo "❌ Backtest failed with exit code $EXIT_CODE"
fi

exit $EXIT_CODE
