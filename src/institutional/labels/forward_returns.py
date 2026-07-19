"""
src/institutional/labels/forward_returns.py
─────────────────────────────────────────────────────────────────────────────
Labels de rendements futurs (forward returns).

Ces labels sont toujours calculés avec le décalage explicite (shift négatif)
et jamais utilisés comme features — uniquement comme cibles de modèle.

IMPORTANT : ces colonnes NE DOIVENT PAS être présentes dans le DataFrame
d'entraînement avant le split train/test — seule la partie test les contient.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


VOL_THRESHOLD_FACTOR = 0.5   # seuil de classification = 0.5 × vol réalisée
COST_BPS = 10.0              # coût aller-retour en bps (ajuster selon execution)


def forward_log_return(close: pd.Series, horizon: int) -> pd.Series:
    """
    Rendement futur log sur `horizon` barres.
    Causalité : shift(-horizon) → les valeurs sont NaN pour les dernières `horizon` barres.
    """
    return np.log(close.shift(-horizon) / close)


def forward_returns_multi(
    close: pd.Series,
    horizons: Optional[List[int]] = None,
) -> pd.DataFrame:
    """
    Rendements futurs sur plusieurs horizons.

    IMPORTANT : ces colonnes sont des LABELS, jamais des features.
    Elles doivent être séparées des features avant entraînement.
    """
    if horizons is None:
        horizons = [4, 8, 12, 24, 48, 72, 168]  # 4h, 8h, 12h, 1d, 2d, 3d, 7d

    out = pd.DataFrame(index=close.index)
    for h in horizons:
        out[f"fwd_ret_{h}h"] = forward_log_return(close, h)

    return out


def direction_label(
    forward_ret: pd.Series,
    vol_series: pd.Series,
    k: float = VOL_THRESHOLD_FACTOR,
    cost_bps: float = COST_BPS,
) -> pd.Series:
    """
    Classifie la direction en 3 classes :
      +1 = UP   (rendement futur > k × vol + coût)
       0 = FLAT (rendement dans la bande de bruit)
      -1 = DOWN (rendement futur < -(k × vol + coût))

    Les seuils sont basés sur la volatilité réalisée courante, jamais fixes.
    """
    cost_frac = cost_bps / 10_000
    threshold = k * vol_series + cost_frac

    label = pd.Series(0, index=forward_ret.index, dtype=np.int8)
    label[forward_ret > threshold] = 1
    label[forward_ret < -threshold] = -1

    return label


def trend_continuation_label(
    close: pd.Series,
    vol_series: pd.Series,
    horizon: int = 24,
    k: float = 1.0,
    cost_bps: float = COST_BPS,
) -> pd.Series:
    """
    Label de continuation de tendance pour BTC/ETH.

    Objectif : capturer la poursuite d'une tendance établie plutôt que
    des micro-anomalies (qui sont le domaine de TRM).

    Horizon recommandé : 24, 48, 72, 168 (1j, 2j, 3j, 7j).

    Label :
      +1 si rendement futur > coût + vol × k  (continuation haussière)
       0 si bruit
      -1 si rendement futur < -(coût + vol × k)  (continuation baissière)
    """
    fwd = forward_log_return(close, horizon)
    return direction_label(fwd, vol_series, k=k, cost_bps=cost_bps)


def carry_label(
    funding_rate: pd.Series,
    forward_price_ret: pd.Series,
    cost_bps: float = COST_BPS,
) -> pd.Series:
    """
    Label de capture de carry (funding).

    +1 si funding élevé ET prix n'a pas bougé défavorablement
     0 si pas de signal
    -1 si payer du funding avec mouvement défavorable

    Utilisé pour le Signal Engine Carry/Funding.
    """
    cost_frac = cost_bps / 10_000
    # Carry net = funding reçu - coût - variation adverse du prix
    net_carry = funding_rate - cost_frac - forward_price_ret.clip(upper=0).abs()

    label = pd.Series(0, index=funding_rate.index, dtype=np.int8)
    label[net_carry > 0] = 1
    label[net_carry < -cost_frac] = -1

    return label


def compute_all_labels(
    close: pd.Series,
    vol_series: pd.Series,
    horizons: Optional[List[int]] = None,
    funding_rate: Optional[pd.Series] = None,
    cost_bps: float = COST_BPS,
) -> pd.DataFrame:
    """
    Calcule l'ensemble complet des labels de rendement.

    Retourne un DataFrame avec uniquement des colonnes de labels.
    Ce DataFrame NE DOIT JAMAIS être joint aux features avant split.
    """
    if horizons is None:
        horizons = [4, 12, 24, 72, 168]

    labels = pd.DataFrame(index=close.index)

    # Forward returns bruts
    fwd = forward_returns_multi(close, horizons)
    labels = pd.concat([labels, fwd], axis=1)

    # Classification directionnelle
    for h in horizons:
        fwd_col = f"fwd_ret_{h}h"
        if fwd_col in labels.columns:
            labels[f"dir_label_{h}h"] = direction_label(
                labels[fwd_col], vol_series, cost_bps=cost_bps
            )

    # Trend continuation (horizons longs : 1j, 3j, 7j)
    for h in [24, 72, 168]:
        labels[f"trend_cont_{h}h"] = trend_continuation_label(
            close, vol_series, horizon=h, cost_bps=cost_bps
        )

    # Carry label (si funding disponible)
    if funding_rate is not None:
        for h in [8, 24]:
            fwd_col = f"fwd_ret_{h}h"
            if fwd_col in labels.columns:
                labels[f"carry_label_{h}h"] = carry_label(
                    funding_rate, labels[fwd_col], cost_bps=cost_bps
                )

    return labels
