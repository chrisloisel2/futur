"""
src/institutional/data/asof_join.py
─────────────────────────────────────────────────────────────────────────────
As-of join causal pour séries temporelles hétérogènes.

Principe : pour chaque timestamp t de la série principale (OHLCV 1h),
on joint la valeur la plus récente de la série secondaire qui soit
strictement antérieure ou égale à t. Jamais de valeur future.

Toutes les jointures entre séries de fréquences différentes (ex: OHLCV 1h
et funding 8h) doivent passer par ces fonctions.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Union

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def asof_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    right_cols: Optional[List[str]] = None,
    suffix: str = "",
    max_stale_minutes: Optional[float] = None,
) -> pd.DataFrame:
    """
    Joint right sur left via pd.merge_asof (forward-fill causal).

    Parameters
    ----------
    left : DataFrame avec index DatetimeIndex UTC trié
    right : DataFrame avec index DatetimeIndex UTC trié
    right_cols : colonnes à conserver de right (None = toutes)
    suffix : suffixe à ajouter aux colonnes right en cas de conflit
    max_stale_minutes : si défini, NaN les valeurs plus vieilles que N minutes

    Returns
    -------
    left avec les colonnes right ajoutées (as-of, causal)
    """
    if not isinstance(left.index, pd.DatetimeIndex):
        raise TypeError("left.index doit être un DatetimeIndex UTC")
    if not isinstance(right.index, pd.DatetimeIndex):
        raise TypeError("right.index doit être un DatetimeIndex UTC")

    if not left.index.is_monotonic_increasing:
        raise ValueError("left.index non monotone — as-of join impossible")
    if not right.index.is_monotonic_increasing:
        right = right.sort_index()

    # Assurer UTC
    if left.index.tz is None:
        left = left.copy()
        left.index = left.index.tz_localize("UTC")
    if right.index.tz is None:
        right = right.copy()
        right.index = right.index.tz_localize("UTC")

    r = right[right_cols] if right_cols else right

    # Renommer les colonnes en conflit
    conflict = [c for c in r.columns if c in left.columns]
    if conflict and suffix:
        r = r.rename(columns={c: f"{c}{suffix}" for c in conflict})

    # Merge as-of (backward = valeur la plus récente ≤ t)
    left_reset = left.reset_index()
    right_reset = r.reset_index()

    left_ts_col = left_reset.columns[0]   # "timestamp" ou nom de l'index
    right_ts_col = right_reset.columns[0]

    merged = pd.merge_asof(
        left_reset.sort_values(left_ts_col),
        right_reset.sort_values(right_ts_col),
        left_on=left_ts_col,
        right_on=right_ts_col,
        direction="backward",
    )

    # Masquer les valeurs trop vieilles
    if max_stale_minutes is not None:
        new_cols = [c for c in r.columns if c != right_ts_col]
        stale_mask = (
            merged[left_ts_col] - merged[right_ts_col]
        ).dt.total_seconds() / 60 > max_stale_minutes

        if stale_mask.any():
            logger.debug(
                f"as-of join: {stale_mask.sum()} lignes avec stale > "
                f"{max_stale_minutes}min — NaN appliqué"
            )
            merged.loc[stale_mask, new_cols] = np.nan

    # Supprimer la colonne timestamp dupliquée de right si présente
    if right_ts_col in merged.columns and right_ts_col != left_ts_col:
        merged = merged.drop(columns=[right_ts_col])

    return merged.set_index(left_ts_col).sort_index()


def asof_join_funding(
    ohlcv: pd.DataFrame,
    funding: pd.DataFrame,
    max_stale_hours: float = 10.0,
) -> pd.DataFrame:
    """
    Joint les funding rates (8h) sur l'OHLCV 1h, en causal.
    Limite le staleness à 10h (légèrement > 8h pour tolérance d'horodatage).
    """
    return asof_join(
        ohlcv,
        funding[["funding_rate"]],
        max_stale_minutes=max_stale_hours * 60,
    )


def asof_join_metrics(
    ohlcv: pd.DataFrame,
    metrics: pd.DataFrame,
    max_stale_hours: float = 2.0,
) -> pd.DataFrame:
    """
    Joint les métriques OI/LSR (5m) sur l'OHLCV 1h, en causal.
    """
    cols = [
        c for c in ["oi_sum", "oi_value_sum", "global_long_short_ratio", "taker_buy_sell_ratio"]
        if c in metrics.columns
    ]
    return asof_join(
        ohlcv,
        metrics[cols],
        max_stale_minutes=max_stale_hours * 60,
    )


def build_master_frame(
    ohlcv_1h: pd.DataFrame,
    funding: Optional[pd.DataFrame] = None,
    metrics: Optional[pd.DataFrame] = None,
    spot: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Construit le DataFrame maître pour un actif en joignant toutes les sources.
    Toutes les jointures sont causales (as-of backward).

    Paramètres
    ----------
    ohlcv_1h  : OHLCV 1h (source principale, futures de préférence)
    funding   : funding rates 8h (optionnel)
    metrics   : OI + LSR 5m (optionnel)
    spot      : OHLCV spot 1h pour calcul du basis (optionnel)

    Retourne
    --------
    DataFrame maître avec toutes les sources jointes causalement
    """
    master = ohlcv_1h.copy()

    if funding is not None:
        master = asof_join_funding(master, funding)
        logger.debug(f"Funding joint : {funding['funding_rate'].notna().sum()} points")

    if metrics is not None:
        master = asof_join_metrics(master, metrics)
        logger.debug(f"Métriques jointes : {len(metrics)} points → {len(master)} barres")

    if spot is not None:
        spot_price = spot[["close"]].rename(columns={"close": "spot_close"})
        master = asof_join(master, spot_price, max_stale_minutes=70)
        if "spot_close" in master.columns:
            # basis = (futures - spot) / spot
            master["basis"] = (master["close"] - master["spot_close"]) / master["spot_close"]

    return master
