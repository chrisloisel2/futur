"""
src/institutional/labels/triple_barrier.py
─────────────────────────────────────────────────────────────────────────────
Labels Triple Barrier (López de Prado, AFML).

3 barrières :
  - UPPER : rendement > k_up × vol → label +1
  - LOWER : rendement < -k_down × vol → label -1
  - TIME  : max_bars atteint sans toucher les barrières → label 0 (censuré)

Les paramètres (k_up, k_down) sont basés sur la volatilité réalisée,
jamais sur des seuils fixes en pourcentage.

Implémentation vectorisée numpy — rapide sur plusieurs milliers de barres.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class TripleBarrierConfig:
    max_bars: int = 72              # horizon max (en barres)
    k_up: float = 1.0               # multiplicateur barrière haute (× vol)
    k_down: float = 1.0             # multiplicateur barrière basse (× vol)
    vol_window: int = 24            # fenêtre vol réalisée pour les barrières
    min_vol: float = 0.001          # vol minimum (évite barrières à 0)
    cost_bps: float = 10.0          # coût aller-retour en bps (pour filtrer)


def _realized_vol_rolling(
    close: np.ndarray,
    window: int,
    annualize: bool = False,
) -> np.ndarray:
    """Volatilité réalisée rolling (std des log-returns) sur un array numpy."""
    n = len(close)
    log_r = np.log(close[1:] / close[:-1])
    vol = np.full(n, np.nan)

    for i in range(window, n):
        vol[i] = log_r[i - window:i].std()

    if annualize:
        vol = vol * np.sqrt(24 * 365)

    return vol


def compute_triple_barrier_labels(
    close: pd.Series,
    config: TripleBarrierConfig,
    vol_series: Optional[pd.Series] = None,
    sample_mask: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """
    Calcule les labels Triple Barrier pour chaque barre.

    Paramètres
    ----------
    close       : prix de clôture (index DatetimeIndex)
    config      : paramètres des barrières
    vol_series  : volatilité réalisée précalculée (si None, calculée ici)
    sample_mask : masque booléen des barres à labeler (None = toutes)

    Retourne
    --------
    DataFrame avec colonnes :
      - label        : +1 / -1 / 0
      - time_to_bar  : nombre de barres jusqu'à la barrière touchée
      - realized_ret : rendement réalisé jusqu'à la barrière
      - touched_upper : bool
      - touched_lower : bool
      - censored     : bool (barrière temps atteinte)
      - barrier_upper : niveau de la barrière haute
      - barrier_lower : niveau de la barrière basse
    """
    prices = close.values.astype(np.float64)
    n = len(prices)

    if vol_series is None:
        vol_arr = _realized_vol_rolling(prices, config.vol_window)
    else:
        vol_arr = vol_series.values.astype(np.float64)

    # S'assurer que vol ≥ min_vol
    vol_arr = np.maximum(np.nan_to_num(vol_arr, nan=config.min_vol), config.min_vol)

    labels = np.zeros(n, dtype=np.int8)
    time_to_bar = np.full(n, config.max_bars, dtype=np.int32)
    realized_ret = np.full(n, np.nan)
    touched_upper = np.zeros(n, dtype=bool)
    touched_lower = np.zeros(n, dtype=bool)
    censored = np.zeros(n, dtype=bool)
    barrier_upper = np.full(n, np.nan)
    barrier_lower = np.full(n, np.nan)

    # Coût en fraction (bps → fraction)
    cost_frac = config.cost_bps / 10_000

    for t in range(n):
        p0 = prices[t]
        v0 = vol_arr[t]

        if np.isnan(p0) or np.isnan(v0):
            censored[t] = True
            continue

        bu = p0 * (1 + config.k_up * v0)
        bl = p0 * (1 - config.k_down * v0)
        barrier_upper[t] = bu
        barrier_lower[t] = bl

        hit = False
        for s in range(1, config.max_bars + 1):
            if t + s >= n:
                break
            pt = prices[t + s]

            if pt >= bu:
                labels[t] = 1
                time_to_bar[t] = s
                realized_ret[t] = (pt - p0) / p0 - cost_frac
                touched_upper[t] = True
                hit = True
                break
            elif pt <= bl:
                labels[t] = -1
                time_to_bar[t] = s
                realized_ret[t] = (pt - p0) / p0 - cost_frac
                touched_lower[t] = True
                hit = True
                break

        if not hit:
            last = min(t + config.max_bars, n - 1)
            realized_ret[t] = (prices[last] - p0) / p0 - cost_frac
            censored[t] = True
            labels[t] = 0

    result = pd.DataFrame(
        {
            "label": labels,
            "time_to_bar": time_to_bar,
            "realized_ret": realized_ret,
            "touched_upper": touched_upper,
            "touched_lower": touched_lower,
            "censored": censored,
            "barrier_upper": barrier_upper,
            "barrier_lower": barrier_lower,
        },
        index=close.index,
    )

    # Masque de sampling (ne retourner que les barres sélectionnées)
    if sample_mask is not None:
        result = result[sample_mask]

    return result


def get_embargo_mask(
    events_index: pd.DatetimeIndex,
    all_index: pd.DatetimeIndex,
    embargo_bars: int = 10,
) -> pd.Series:
    """
    Crée un masque d'embargo pour purged cross-validation.

    Les barres dans `all_index` qui sont dans les `embargo_bars` barres
    suivant chaque événement dans `events_index` sont masquées.

    Usage : empêcher la fuite entre train/test via la superposition des labels.
    """
    embargo = pd.Series(True, index=all_index)

    for t in events_index:
        # Trouver l'index position de t dans all_index
        try:
            pos = all_index.get_loc(t)
        except KeyError:
            continue
        start = pos + 1
        end = min(pos + embargo_bars + 1, len(all_index))
        embargo.iloc[start:end] = False

    return embargo
