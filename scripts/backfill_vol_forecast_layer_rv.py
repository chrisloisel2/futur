#!/usr/bin/env python3
"""
scripts/backfill_vol_forecast_layer_rv.py
─────────────────────────────────────────────────────────────────────────────
VOL_FORECAST_LAYER_V1 -- passe de backfill RV. Remplit `actual_realized_rv`
pour les lignes du ledger dont l'horizon de forecast (target_realized_at) est
écoulé et encore NULL/pending. À lancer APRÈS
scripts/run_vol_forecast_layer_shadow.py (une fois -- ou répété, idempotent
-- assez de temps réel écoulé pour que la RV réalisée de `target_period_start`
soit observable dans data/enriched/BTCUSDT_1h_enriched.parquet).

Ne remplit JAMAIS qu'une cellule NULL -> valeur ; ne réécrit jamais une
cellule déjà backfillée (voir src/institutional/engines/vol_forecast_layer/
backfill.py docstring).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.institutional.engines.vol_forecast_layer.backfill import backfill_actual_realized_rv

ALPHA_ID = "VOL_FORECAST_LAYER_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"


def main() -> int:
    if not LEDGER.exists():
        print(f"[{ALPHA_ID}] pas de ledger ({LEDGER}) — lancer "
              f"scripts/run_vol_forecast_layer_shadow.py d'abord.")
        return 0

    decisions = pd.read_parquet(LEDGER)
    n_pending_before = int(decisions["actual_realized_rv"].isna().sum())
    if n_pending_before == 0:
        print(f"[{ALPHA_ID}] aucune ligne pending — rien à backfiller ({len(decisions)} décisions).")
        return 0

    now = datetime.now(timezone.utc)
    n_before_notna = int(decisions["actual_realized_rv"].notna().sum())
    updated = backfill_actual_realized_rv(decisions, symbol="BTCUSDT", now=now)
    n_filled = int(updated["actual_realized_rv"].notna().sum()) - n_before_notna

    updated.to_parquet(LEDGER, index=False)
    n_pending_after = int(updated["actual_realized_rv"].isna().sum())
    print(f"[{ALPHA_ID}] backfill : {n_pending_before} pending -> {n_filled} nouvellement remplies, "
          f"{n_pending_after} encore pending (horizon pas encore écoulé ou data pas dispo).")

    meta_path = OUT_DIR / "backfill_state.json"
    meta_path.write_text(json.dumps({
        "alpha_id": ALPHA_ID, "last_backfill_run": now.isoformat(),
        "n_pending_before": n_pending_before, "n_filled_this_run": n_filled,
        "n_pending_after": n_pending_after,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
