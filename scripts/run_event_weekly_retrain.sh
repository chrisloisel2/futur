#!/usr/bin/env bash
# Ré-entraînement HEBDO des moteurs événementiels + re-verdicts WF + wave portfolio.
# La learning curve documentée (AUC/PF ↑ avec la taille du train) travaille seule :
# chaque semaine de données Vision/collecteur améliore mécaniquement les moteurs.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
LOG=reports/liq_cascade/weekly_retrain.log
{
  echo "=== WEEKLY RETRAIN $(date -u +%F_%T) ==="
  for e in cascade crowding premium; do
    $PY scripts/train_event_engine.py --engine "$e" --rebuild-cache 2>&1 | tail -3
  done
  $PY scripts/run_three_engine_wave_portfolio.py 2>&1 | tail -8
  $PY scripts/validate_cascade_proxy_vs_real.py 2>&1 | tail -5
  echo "=== DONE $(date -u +%F_%T) ==="
} >> "$LOG" 2>&1
