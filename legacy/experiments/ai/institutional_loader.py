"""
institutional_loader.py — Chargement depuis ohlcv_institutional_features_btcusdt
==================================================================================

Remplace l'ingestion CSV/Parquet+feature_engineering manuel par une lecture
directe de la collection MongoDB qui contient déjà les 905 features pré-calculées.

Points d'entrée :
  load_institutional_data()        → DataFrame complet (features + labels bruts)
  build_institutional_labels()     → ajoute y_long, y_short, tradeable_net, future_ret_4h
  get_split_masks()                → masques train/val/test chronologiques
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from pymongo import MongoClient, ASCENDING

LOG = logging.getLogger(__name__)

INSTITUTIONAL_COLLECTION = "ohlcv_institutional_features_btcusdt"
MONGO_URI = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
MONGO_DB  = os.getenv("FUTUR_MONGO_DB",  os.getenv("MONGODB_DB",  "trading"))

META_COLS = frozenset({
    "symbol", "interval", "feature_version", "source_origin",
    "enriched_at", "symbol_compact",
})


def load_institutional_data(
    interval: str = "1h",
    start: Optional[str] = None,
    end:   Optional[str] = None,
    limit: Optional[int] = None,
    columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Charge les données depuis ohlcv_institutional_features_btcusdt.

    Parameters
    ----------
    interval : str
        Intervalle temporel ('1h' ou '1m').
    columns : list[str] | None
        Si fourni, projette uniquement ces colonnes + timestamp + OHLCV + labels.
        Réduire à ~100 colonnes est 10× plus rapide que lire les 907.
        Si None, charge toutes les colonnes (lent pour 76K lignes × 907 cols).

    Retourne un DataFrame indexé par timestamp UTC.
    """
    # Colonnes systématiques toujours incluses
    ALWAYS = {"timestamp", "open", "high", "low", "close", "volume", "dollar_volume"}

    # Labels toujours inclus pour les 5 horizons
    LABEL_PREFIXES = [
        f"label_future_log_return_{h}" for h in [3, 5, 10, 20, 50]
    ] + [
        f"label_triple_barrier_{h}"    for h in [3, 5, 10, 20, 50]
    ] + [
        f"label_future_return_{h}"     for h in [3, 5, 10, 20, 50]
    ]

    projection: dict = {"_id": 0}
    if columns is not None:
        requested = set(columns) | ALWAYS | set(LABEL_PREFIXES)
        projection = {"_id": 0, **{c: 1 for c in requested}}

    query: dict = {"interval": interval}
    if start or end:
        ts_q: dict = {}
        if start:
            ts_q["$gte"] = pd.Timestamp(start, tz="UTC").to_pydatetime()
        if end:
            ts_q["$lte"] = pd.Timestamp(end, tz="UTC").to_pydatetime()
        query["timestamp"] = ts_q

    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=30_000,
        socketTimeoutMS=600_000,
        connectTimeoutMS=30_000,
        maxPoolSize=4,
    )
    try:
        col = client[MONGO_DB][INSTITUTIONAL_COLLECTION]

        # Si on veut toutes les colonnes, on doit agréger par batch pour ne pas
        # saturer la mémoire du curseur sur 76K × 907 champs.
        # Si projection partielle : lecture directe plus légère.
        LOG.info(
            "Chargement %s interval=%s cols=%s …",
            INSTITUTIONAL_COLLECTION, interval,
            len(columns) if columns else "all",
        )
        cursor = (
            col.find(query, projection if columns else {"_id": 0})
            .sort("timestamp", ASCENDING)
            .batch_size(2_000)
        )
        if limit:
            cursor = cursor.limit(int(limit))

        docs = list(cursor)
        if not docs:
            raise ValueError(
                f"Aucune donnée pour interval={interval!r}. "
                f"Vérifier la collection {INSTITUTIONAL_COLLECTION}."
            )

        df = pd.DataFrame(docs)
        LOG.info("Chargé %d lignes × %d colonnes brutes", len(df), len(df.columns))
    finally:
        client.close()

    # Index timestamp UTC
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()

    # Supprimer les métadonnées non-utilisées
    df.drop(columns=[c for c in META_COLS if c in df.columns], inplace=True, errors="ignore")

    # Convertir float32 → float64 (sklearn attend float64)
    f32_cols = df.select_dtypes(include=["float32"]).columns
    if len(f32_cols):
        df[f32_cols] = df[f32_cols].astype("float64")

    # Nettoyer inf dans les features (pas dans les labels — ils peuvent être NaN intentionnellement)
    feat_cols = [c for c in df.columns if not c.startswith("label_")]
    df[feat_cols] = df[feat_cols].replace([np.inf, -np.inf], np.nan)

    n_feat   = len([c for c in df.columns if not c.startswith("label_")])
    n_labels = len([c for c in df.columns if c.startswith("label_")])
    LOG.info(
        "Dataset prêt : %d lignes | %d features | %d labels | range %s → %s",
        len(df), n_feat, n_labels,
        df.index[0].date(), df.index[-1].date(),
    )
    return df


def build_institutional_labels(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    horizon: int = 5,
    cost_pct: float = 0.0010,
    cost_short_mult: float = 1.5,
    tradeable_quantile: float = 0.88,
    long_min_abs_return: float = 0.010,
    short_min_abs_return: float = 0.012,
    gray_zone_factor: float = 0.15,
) -> pd.DataFrame:
    """
    Construit les labels d'entraînement depuis les colonnes pré-calculées.

    Labels produits
    ---------------
    y_long        (int)  : 1 = bon long, 0 = mauvais, -1 = zone grise (exclue du train)
    y_short       (int)  : 1 = bon short, 0 = mauvais, -1 = zone grise
    tradeable_net (int)  : 1 si |ret| couvre les coûts
    future_ret_4h (float): alias de label_future_log_return_{horizon} — compatible backtest engine
    y_long_tb     (int)  : version triple-barrier (plus propre, utilise path + stops)
    y_short_tb    (int)  : idem short

    Anti-leakage : seuils calibrés UNIQUEMENT sur train_mask.
    """
    log_col = f"label_future_log_return_{horizon}"
    tb_col  = f"label_triple_barrier_{horizon}"

    for col in [log_col]:
        if col not in df.columns:
            raise ValueError(
                f"Colonne {col!r} manquante. "
                f"Horizons disponibles : {sorted(h for h in [3,5,10,20,50] if f'label_future_log_return_{h}' in df.columns)}"
            )

    df = df.copy()
    ret = df[log_col].values.astype(np.float64)

    # Seuils calibrés sur train uniquement
    train_ret_abs = np.abs(ret[train_mask & np.isfinite(ret)])
    thr_long  = max(np.nanpercentile(train_ret_abs, tradeable_quantile * 100), long_min_abs_return + cost_pct)
    thr_short = max(np.nanpercentile(train_ret_abs, tradeable_quantile * 100), short_min_abs_return + cost_pct * cost_short_mult)
    gray_l = thr_long  * (1.0 - gray_zone_factor)
    gray_s = thr_short * (1.0 - gray_zone_factor)

    LOG.info(
        "Seuils labels [h=%d bar] : thr_long=%.3f%%  thr_short=%.3f%%  "
        "(calibrés sur %d bars train)",
        horizon, thr_long * 100, thr_short * 100, int(train_mask.sum()),
    )

    # ── y_long (basé sur log-return brut) ─────────────────────────────────────
    y_long = np.zeros(len(df), dtype=np.int32)
    y_long[ret >  thr_long]                              = 1
    y_long[(ret >= gray_l) & (ret < thr_long)]           = -1  # zone grise

    # ── y_short (inversé : baisse > seuil = bon short) ────────────────────────
    y_short = np.zeros(len(df), dtype=np.int32)
    y_short[ret < -thr_short]                            = 1
    y_short[(ret <= -gray_s) & (ret > -thr_short)]       = -1  # zone grise

    # ── tradeable_net ──────────────────────────────────────────────────────────
    tradeable_net = ((ret > thr_long) | (ret < -thr_short)).astype(np.int32)

    # ── Labels triple-barrier (qualité supérieure, utilise les stops ATR) ─────
    if tb_col in df.columns:
        tb = df[tb_col].values
        y_long_tb  = np.where(tb ==  1.0, 1, np.where(tb == -1.0, 0, -1)).astype(np.int32)
        y_short_tb = np.where(tb == -1.0, 1, np.where(tb ==  1.0, 0, -1)).astype(np.int32)
        df["y_long_tb"]  = y_long_tb
        df["y_short_tb"] = y_short_tb

    df["y_long"]        = y_long
    df["y_short"]       = y_short
    df["tradeable_net"] = tradeable_net
    # Alias pour compatibilité avec backtest/engine.py (lit TARGET_COL = "future_ret_4h")
    df["future_ret_4h"] = ret
    df["future_ret_h"]  = ret   # fallback secondaire dans le moteur

    n = len(df)
    LOG.info(
        "Labels : y_long pos=%d (%.1f%%)  y_short pos=%d (%.1f%%)  tradeable=%d (%.1f%%)",
        (y_long == 1).sum(),  (y_long == 1).sum()  / n * 100,
        (y_short == 1).sum(), (y_short == 1).sum() / n * 100,
        tradeable_net.sum(),  tradeable_net.sum()  / n * 100,
    )
    return df


def get_split_masks(
    df: pd.DataFrame,
    train_end_year: int = 2022,
    val_year:       int = 2023,
    test_from_year: int = 2024,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Découpe chronologique stricte identique au pipeline existant.

    train  : années ≤ train_end_year
    val    : année  = val_year
    test   : années ≥ test_from_year

    Aucun chevauchement. Aucune information future dans train.
    """
    years = np.array(df.index.year, dtype=np.int32)
    train_mask = (years <= train_end_year)
    val_mask   = (years == val_year)
    test_mask  = (years >= test_from_year)

    LOG.info(
        "Split : train=%d  val=%d  test=%d",
        train_mask.sum(), val_mask.sum(), test_mask.sum(),
    )
    return train_mask, val_mask, test_mask
