"""
src/institutional/engines/vol_forecast_layer/backfill.py
─────────────────────────────────────────────────────────────────────────────
Remplit `actual_realized_rv` pour les décisions VOL_FORECAST_LAYER_V1 dont
l'horizon de forecast est écoulé (target_realized_at <= now) et qui sont
encore NULL. Réutilise EXACTEMENT la même formule de RV réalisée que
`current_realized_vol` (src/institutional/engines/vol_forecast_layer/
realized_vol.py::compute_daily_realized_vol), appliquée au jour CIBLE
(target_period_start, i.e. day+1) plutôt qu'au jour du forecast lui-même --
comparaison forecast/résultat apples-to-apples.

N'écrit JAMAIS que dans les cellules `actual_realized_rv`/`rv_backfilled_at`
actuellement NULL -- ne touche jamais une ligne déjà backfillée (idempotent,
pas de réécriture rétroactive, conforme à la discipline "ledger append-only"
du registre).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from src.institutional.engines.vol_forecast_layer.realized_vol import (
    compute_daily_realized_vol,
)


def backfill_actual_realized_rv(
    decisions: pd.DataFrame,
    symbol: str = "BTCUSDT",
    now: "pd.Timestamp | datetime | None" = None,
    rv_daily: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Retourne une COPIE de `decisions` avec `actual_realized_rv`/
    `rv_backfilled_at` remplis pour toute ligne où :
      - actual_realized_rv est actuellement NULL, ET
      - target_realized_at <= now.

    `rv_daily` (colonnes day/sameday_rv) est injectable pour les tests
    (évite de dépendre du disque). Ne modifie AUCUNE autre colonne, ni les
    lignes déjà backfillées, ni les lignes dont l'horizon n'est pas encore
    écoulé (restent NULL/pending)."""
    if decisions.empty:
        return decisions.copy()

    now_ts = pd.Timestamp(now if now is not None else datetime.now(timezone.utc))
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")

    out = decisions.copy()
    for col in ("target_realized_at", "target_period_start", "actual_realized_rv"):
        if col not in out.columns:
            raise ValueError(f"backfill_actual_realized_rv: colonne manquante: {col}")

    target_realized_at = pd.to_datetime(out["target_realized_at"], utc=True)
    pending_mask = out["actual_realized_rv"].isna() & (target_realized_at <= now_ts)
    if not pending_mask.any():
        return out

    rv_daily = rv_daily if rv_daily is not None else compute_daily_realized_vol(symbol)
    rv_by_day = dict(zip(rv_daily["day"], rv_daily["sameday_rv"])) if not rv_daily.empty else {}

    target_days = pd.to_datetime(out["target_period_start"], utc=True).dt.floor("D")
    for idx in out.index[pending_mask]:
        tday = target_days.loc[idx]
        val = rv_by_day.get(tday)
        if val is not None:
            out.at[idx, "actual_realized_rv"] = val
            out.at[idx, "rv_backfilled_at"] = now_ts.isoformat()
        # sinon : jour cible pas encore disponible dans l'enrichi (collecte
        # pas encore rafraîchie jusque-là) -- reste NULL, retenté au run suivant.
    return out
