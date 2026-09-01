"""
src/institutional/live_alpha_lab/overlay.py
─────────────────────────────────────────────────────────────────────────────
Traduit le forecast de VOL_FORECAST_LAYER_V1 en multiplicateur de sizing
défensif pour P1_VOL_OVERLAY -- reprend TEL QUEL le design documenté par le
worker VOL_FORECAST_LAYER (freeze_spec.json::part2_design_proposal) :

    multiplier = clip(1.0 - k * max(combined_forecast_z, 0), floor, 1.0)

Seule différence CONTROL vs VOL_OVERLAY (item 3 de la mission, "Ne change
rien d'autre entre CONTROL et OVERLAY") : ce multiplicateur, appliqué
uniformément à tous les target_position_fraction du portefeuille. Rien
d'autre ne diffère (mêmes budgets, mêmes alphas, mêmes limites).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
VOL_LEDGER = ROOT / "reports" / "live_alpha_lab" / "VOL_FORECAST_LAYER_V1" / "decisions.parquet"

K = 0.5      # sensibilité -- valeur d'ingénierie déclarée, pas fittée
FLOOR = 0.3  # jamais moins de 30% du sizing nominal, même en forecast RV extrême


def vol_overlay_multiplier(as_of: pd.Timestamp) -> float:
    """1.0 si aucun forecast disponible (fail-open sur le sizing, PAS sur le
    signal -- l'overlay est optionnel, son absence ne doit jamais bloquer
    P1_CONTROL ni un run sans VOL_FORECAST_LAYER)."""
    if not VOL_LEDGER.exists():
        return 1.0
    df = pd.read_parquet(VOL_LEDGER)
    if df.empty or "combined_forecast_z" not in df.columns:
        return 1.0
    df = df[pd.to_datetime(df["forecast_made_at" if "forecast_made_at" in df.columns
                            else "event_time"], utc=True) <= as_of]
    if df.empty:
        return 1.0
    latest = df.sort_values(df.columns[0]).iloc[-1]
    z = float(latest["combined_forecast_z"])
    mult = 1.0 - K * max(z, 0.0)
    return max(FLOOR, min(1.0, mult))
