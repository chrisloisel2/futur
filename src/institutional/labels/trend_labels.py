"""
src/institutional/labels/trend_labels.py
─────────────────────────────────────────────────────────────────────────────
Labels de tendance pour BTC/ETH — moteur INSTITUTIONAL.

PRINCIPE CLÉ :
    Le seuil UP/DOWN est basé sur la volatilité réalisée SCALÉE AU HORIZON,
    pas sur la volatilité annualisée brute.

    vol_horizon = vol_annualisée × sqrt(horizon_bars / bars_per_year)

    Pour BTC 24h : vol_horizon ≈ 2.3%, threshold ≈ 2% → ~20% UP / 20% DOWN / 60% FLAT

    ERREUR FRÉQUENTE :
    Passer vol_annualisée (~50%) directement → threshold ~25% → tout FLAT → modèle prédit du bruit.

LABELS :
    trend_cont_{h}h : +1 UP / 0 FLAT / -1 DOWN
    vol_adj_dir_{h}h : identique avec k configurable
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

BARS_PER_YEAR_1H: int = 24 * 365   # 8 760 barres par an en 1h


# ─── Core ────────────────────────────────────────────────────────────────────

def vol_over_horizon(
    vol_annual: pd.Series,
    horizon_bars: int,
    bars_per_year: int = BARS_PER_YEAR_1H,
) -> pd.Series:
    """
    Scale la volatilité annualisée vers l'horizon du label.
    vol_h = vol_annual × sqrt(h / bars_per_year)
    """
    return vol_annual * np.sqrt(horizon_bars / bars_per_year)


def trend_continuation_label(
    close: pd.Series,
    vol_annual: pd.Series,
    horizon_bars: int,
    *,
    k: float = 1.0,
    cost_bps: float = 10.0,
    bars_per_year: int = BARS_PER_YEAR_1H,
) -> pd.Series:
    """
    Label de continuation de tendance sur `horizon_bars`.

    Seuil : threshold = k × vol_horizon + cost_fraction
    UP   : return > threshold
    DOWN : return < -threshold
    FLAT : |return| ≤ threshold  (la majorité)

    Paramètres recommandés BTC/ETH :
        k=1.0, cost_bps=10  → 60% FLAT, 20% UP, 20% DOWN (24h)
        k=0.8, cost_bps=10  → 55% FLAT, 22% UP, 23% DOWN (24h)

    IMPORTANT : les labels sont NaN pour les `horizon_bars` dernières barres
    (données futures non disponibles). Exclure du training.
    """
    vol_h     = vol_over_horizon(vol_annual, horizon_bars, bars_per_year)
    cost_frac = cost_bps / 10_000.0
    threshold = k * vol_h + cost_frac

    fwd_ret = np.log(close.shift(-horizon_bars) / close)

    label = pd.Series(0, index=close.index, dtype=np.int8)
    label[fwd_ret >  threshold]  = 1
    label[fwd_ret < -threshold] = -1

    # NaN là où forward_ret est NaN (dernières barres)
    label[fwd_ret.isna()] = pd.NA
    return label.astype("Int8")


def trend_label_distribution(
    close: pd.Series,
    vol_annual: pd.Series,
    horizon_bars: int,
    k: float = 1.0,
    cost_bps: float = 10.0,
) -> Dict[str, float]:
    """Retourne la distribution UP/FLAT/DOWN d'un label (utile pour vérifier k)."""
    label = trend_continuation_label(close, vol_annual, horizon_bars, k=k, cost_bps=cost_bps)
    valid = label.dropna()
    n = len(valid)
    return {
        "UP":   float((valid == 1).sum() / n),
        "FLAT": float((valid == 0).sum() / n),
        "DOWN": float((valid == -1).sum() / n),
        "n":    n,
        "k":    k,
        "horizon_bars": horizon_bars,
    }


def calibrate_k(
    close: pd.Series,
    vol_annual: pd.Series,
    horizon_bars: int,
    target_flat_rate: float = 0.55,
    k_range: tuple = (0.3, 2.0),
    cost_bps: float = 10.0,
) -> float:
    """
    Calibre `k` pour approcher un taux FLAT cible sur les données d'entraînement.

    Ne calibrer QUE sur le train set — jamais sur val/test.
    """
    low, high = k_range
    for _ in range(30):
        mid = (low + high) / 2.0
        dist = trend_label_distribution(close, vol_annual, horizon_bars, k=mid, cost_bps=cost_bps)
        if dist["FLAT"] < target_flat_rate:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


# ─── Labels multi-horizons ───────────────────────────────────────────────────

def build_btc_eth_labels(
    close: pd.Series,
    vol_annual: pd.Series,
    horizons_h: Optional[list] = None,
    k: float = 1.0,
    cost_bps: float = 10.0,
) -> pd.DataFrame:
    """
    Construit tous les labels trend pour BTC/ETH.

    Horizons par défaut : [24, 72, 168] (1j, 3j, 7j)

    Retourne DataFrame avec colonnes :
        trend_cont_24h, trend_cont_72h, trend_cont_168h
        vol_24h, vol_72h, vol_168h  (pour référence)
    """
    if horizons_h is None:
        horizons_h = [24, 72, 168]

    out = pd.DataFrame(index=close.index)

    for h in horizons_h:
        col = f"trend_cont_{h}h"
        out[col] = trend_continuation_label(
            close, vol_annual, h, k=k, cost_bps=cost_bps
        )
        # Forward return brut (utile pour expectancy@k et carry audit)
        out[f"fwd_ret_{h}h"] = np.log(close.shift(-h) / close)

        vol_h = vol_over_horizon(vol_annual, h)
        out[f"vol_h_{h}h"] = vol_h
        out[f"threshold_{h}h"] = k * vol_h + cost_bps / 10_000

    return out
