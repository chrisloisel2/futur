"""
src/institutional/labels/event_labels.py
─────────────────────────────────────────────────────────────────────────────
Labels pour TRM_EVENT_ENGINE — anomalies, squeezes, déséquilibres.

Différence fondamentale avec trend_labels :
    Les événements ont des seuils plus hauts (impulsion > 2× vol normale)
    et des horizons courts (1h-4h max).

    UP   = continuation après l'événement
    DOWN = retournement après l'événement
    FLAT = pas de mouvement significatif

Ces labels répondent à la question :
    "Après cette anomalie détectée, le marché continue-t-il ou se retourne-t-il ?"
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from src.institutional.labels.trend_labels import (
    vol_over_horizon,
    BARS_PER_YEAR_1H,
)


def event_followthrough_label(
    close: pd.Series,
    vol_annual: pd.Series,
    horizon_bars: int,
    *,
    k: float = 1.5,             # plus haut que trend (événements = mouvements forts)
    cost_bps: float = 15.0,     # frais + slippage élevés sur événements
    bars_per_year: int = BARS_PER_YEAR_1H,
) -> pd.Series:
    """
    Label pour TRM : continuation après événement.

    k plus élevé que trend (1.5 vs 1.0) car les événements déclenchent
    des mouvements plus importants que la tendance normale.

    Horizons recommandés : 1, 4, 8 barres 1h.
    """
    vol_h     = vol_over_horizon(vol_annual, horizon_bars, bars_per_year)
    cost_frac = cost_bps / 10_000.0
    threshold = k * vol_h + cost_frac

    fwd_ret = np.log(close.shift(-horizon_bars) / close)

    label = pd.Series(0, index=close.index, dtype=np.int8)
    label[fwd_ret >  threshold]  = 1
    label[fwd_ret < -threshold] = -1
    label[fwd_ret.isna()] = pd.NA
    return label.astype("Int8")


def squeeze_success_label(
    close: pd.Series,
    oi_sum: Optional[pd.Series],
    funding_rate: Optional[pd.Series],
    vol_annual: pd.Series,
    horizon_bars: int = 4,
    *,
    k: float = 2.0,
    cost_bps: float = 20.0,
) -> pd.Series:
    """
    Label de succès d'un squeeze (long ou short) :
    +1 si la continuation dans la direction du squeeze dépasse le seuil.
    Utilise un k encore plus élevé (2.0) car les squeezes sont des événements extrêmes.
    """
    return event_followthrough_label(
        close, vol_annual, horizon_bars, k=k, cost_bps=cost_bps
    )


def build_trm_event_labels(
    close: pd.Series,
    vol_annual: pd.Series,
    horizons_h: Optional[list] = None,
    k: float = 1.5,
    cost_bps: float = 15.0,
) -> pd.DataFrame:
    """
    Construit tous les labels événementiels pour TRM.
    Horizons par défaut : [1, 4, 8] (1h, 4h, 8h)
    """
    if horizons_h is None:
        horizons_h = [1, 4, 8]

    out = pd.DataFrame(index=close.index)

    for h in horizons_h:
        out[f"event_cont_{h}h"] = event_followthrough_label(
            close, vol_annual, h, k=k, cost_bps=cost_bps
        )
        # Forward return brut (pour expectancy@k)
        out[f"fwd_ret_{h}h"] = np.log(close.shift(-h) / close)

    return out
