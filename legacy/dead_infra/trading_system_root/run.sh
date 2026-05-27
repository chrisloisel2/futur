#!/bin/bash
# Trading System - Quick Start Script
# Run all tests to validate the 14 critical fixes

set -e  # Exit on any error

echo "=========================================="
echo "Trading System - Running All Tests"
echo "=========================================="
echo ""

# Set Python path
export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

# Run comprehensive test suite
echo "Running test_all_fixes.py..."
python test_all_fixes.py

echo ""
echo "=========================================="
echo "Next Steps:"
echo "=========================================="
echo ""
echo "1. Run a validation check:"
echo "   PYTHONPATH=\"\$(pwd)/src:\$PYTHONPATH\" python -m src.app.main validate --config configs/base.yaml"
echo ""
echo "2. Run a backtest (with mock data):"
echo "   PYTHONPATH=\"\$(pwd)/src:\$PYTHONPATH\" python -m src.app.main backtest --start-date 2024-01-01 --end-date 2024-12-01 --symbols BTCUSDT --config configs/base.yaml"
echo ""
echo "3. Train models (requires labeled data):"
echo "   PYTHONPATH=\"\$(pwd)/src:\$PYTHONPATH\" python -m src.app.main train --config configs/base.yaml"
echo ""
echo "4. See FIXES_APPLIED.md for detailed documentation"
echo ""
