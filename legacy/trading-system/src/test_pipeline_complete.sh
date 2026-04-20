#!/bin/bash
# test_pipeline_complete.sh

set -euo pipefail

echo "==================================================================="
echo "TEST COMPLET PIPELINE ML TRADING"
echo "==================================================================="
echo ""

# -------------------------------------------------------------------
# Resolve paths (works from anywhere)
# -------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR"
ROOT_DIR="$(cd "$SRC_DIR/.." && pwd)"

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------
SYMBOL="BTCUSDT"
START_DATE="2024-01-01"
END_DATE="2024-01-31"
BAR_DURATION=1  # minutes
HORIZON=60      # minutes

OUTPUT_DIR="$ROOT_DIR/artifacts/paper/test_pipeline_diagnostic"
METRICS_FILE="$OUTPUT_DIR/paper_metrics.json"
TRADES_FILE="$OUTPUT_DIR/trades.csv"

ARTIFACT_FILE="$ROOT_DIR/artifacts/models/edge/production_v4_2_best_trading.pt"
CALIBRATOR_FILE="$ROOT_DIR/artifacts/models/edge/production_v4_2_calibrator.pkl"

echo "Configuration:"
echo "  Symbol: $SYMBOL"
echo "  Period: $START_DATE to $END_DATE"
echo "  Bar duration: ${BAR_DURATION}m"
echo "  Horizon: ${HORIZON} minutes"
echo "  Root dir: $ROOT_DIR"
echo "  Output dir: $OUTPUT_DIR"
echo ""

# -------------------------------------------------------------------
# Phase 5: Paper Trading Test
# -------------------------------------------------------------------
echo "─────────────────────────────────────────────────────────────────"
echo "PHASE 5: Paper Trading (Backtest)"
echo "─────────────────────────────────────────────────────────────────"

mkdir -p "$OUTPUT_DIR"

# Ensure required files exist
if [[ ! -f "$ARTIFACT_FILE" ]]; then
  echo "ERROR: Missing artifact: $ARTIFACT_FILE" >&2
  exit 1
fi

if [[ ! -f "$CALIBRATOR_FILE" ]]; then
  echo "ERROR: Missing calibrator: $CALIBRATOR_FILE" >&2
  exit 1
fi

# Run from ROOT so imports + relative paths are stable
cd "$ROOT_DIR"

python3 scripts/paper_test_trained.py \
  --artifact "$ARTIFACT_FILE" \
  --calibrator "$CALIBRATOR_FILE" \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --symbol "$SYMBOL" \
  --sweep-thresholds 0.60 \
  --output-dir "$OUTPUT_DIR"

echo ""

# Hard check output existence
if [[ ! -f "$METRICS_FILE" ]]; then
  echo "ERROR: paper_metrics.json not generated: $METRICS_FILE" >&2
  echo "INFO: Listing output dir:"
  ls -lah "$OUTPUT_DIR" || true
  exit 1
fi

if [[ ! -f "$TRADES_FILE" ]]; then
  echo "ERROR: trades.csv not generated: $TRADES_FILE" >&2
  echo "INFO: Listing output dir:"
  ls -lah "$OUTPUT_DIR" || true
  exit 1
fi

# -------------------------------------------------------------------
# Phase 6: Validation Metrics
# -------------------------------------------------------------------
echo "─────────────────────────────────────────────────────────────────"
echo "PHASE 6: Validation Métriques"
echo "─────────────────────────────────────────────────────────────────"

python3 - <<EOF
import json
import pandas as pd
from pathlib import Path

metrics_path = Path(r"$METRICS_FILE")
trades_path = Path(r"$TRADES_FILE")

metrics = json.loads(metrics_path.read_text())

print("✓ Backtest metrics:")
print(f"  ROI net: {metrics.get('roi', 0.0):.2%}")
print(f"  Sharpe: {metrics.get('sharpe', 0.0):.2f}")
print(f"  Max DD: {metrics.get('max_dd', 0.0):.2%}")
print(f"  Win rate: {metrics.get('win_rate', 0.0):.2%}")
print(f"  Trades: {int(metrics.get('n_trades', 0))}")

trades = pd.read_csv(trades_path)

print(f"\\n✓ Trade details:")
print(f"  Total trades: {len(trades)}")

if len(trades) > 0:
    if "holding_bars" in trades.columns:
        print(f"  Avg holding_bars: {trades['holding_bars'].mean():.1f}")
    if "reason" in trades.columns:
        print(f"  Exit reasons:")
        vc = trades["reason"].value_counts()
        for reason, count in vc.items():
            print(f"    {reason}: {count}")
else:
    print("  No trades generated (threshold/edge/cooldown constraints).")
EOF

echo ""
echo "==================================================================="
echo "TEST TERMINÉ AVEC SUCCÈS ✓"
echo "==================================================================="
