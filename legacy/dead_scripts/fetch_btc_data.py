#!/usr/bin/env python3
"""
scripts/fetch_btc_data.py — Ingestion automatique des données BTC/USDT 1h
=========================================================================

Usage :
    # Première fois — télécharge tout depuis 2017
    python scripts/fetch_btc_data.py

    # Mise à jour incrémentale — repart de la dernière barre connue
    python scripts/fetch_btc_data.py --update

    # Forcer une date de début
    python scripts/fetch_btc_data.py --since 2020-01-01

Sortie :
    data/BTCUSD_1h_features.csv   (format compatible train_pipeline.py)

Ce script :
  1. Télécharge les chandeliers 1h BTCUSDT depuis Binance (paginé, ~72 requêtes)
  2. Calcule les SNAPSHOT_FEATURES (39) + future_ret_h
  3. Merge avec le CSV existant si --update
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import requests

# ── Chemin racine du projet ───────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mongo_ingestion import (
    DEFAULT_HISTORICAL_COLLECTION,
    DEFAULT_MONGO_URI,
    DEFAULT_TRADER_DB,
    get_latest_ohlcv_timestamp,
    normalize_symbol,
    upsert_ohlcv_dataframe,
)
from data_pipeline.features import compute_hourly_features


def _load_project_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de charger {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_live_features = _load_project_module("futur_live_features", ROOT / "ai" / "level_0" / "live_features.py")
_feature_engineering = _load_project_module(
    "futur_feature_engineering",
    ROOT / "ai" / "level_0" / "feature_engineering.py",
)
_feature_lists = _load_project_module("futur_feature_lists", ROOT / "ai" / "level_0" / "features.py")

compute_live_features = _live_features.compute_live_features
compute_long_features = _feature_engineering.compute_long_features
compute_short_features = _feature_engineering.compute_short_features
FEATURES_LONG = _feature_lists.FEATURES_LONG
FEATURES_SHORT = _feature_lists.FEATURES_SHORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
BINANCE_URL = "https://api.binance.com/api/v3/klines"
SYMBOL      = "BTCUSDT"
INTERVAL    = "1h"
LIMIT       = 1000
OUTPUT_CSV  = ROOT / "data" / "BTCUSD_1h_features.csv"

# Date de début du marché BTC sur Binance
BINANCE_START = "2017-08-17"


# ═════════════════════════════════════════════════════════════════════════════
# 1. Fetch Binance
# ═════════════════════════════════════════════════════════════════════════════

def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: Optional[int] = None,
) -> List[list]:
    """Télécharge une page de 1000 chandeliers depuis Binance."""
    params = {
        "symbol":    symbol,
        "interval":  interval,
        "startTime": start_ms,
        "limit":     LIMIT,
    }
    if end_ms:
        params["endTime"] = end_ms

    for attempt in range(5):
        try:
            r = requests.get(BINANCE_URL, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 4:
                raise
            log.warning(f"Binance error ({e}), retry {attempt+1}/5 …")
            time.sleep(2 ** attempt)
    return []


def fetch_all_klines(
    symbol: str = SYMBOL,
    interval: str = INTERVAL,
    since: str = BINANCE_START,
    until: Optional[str] = None,
) -> pd.DataFrame:
    """
    Télécharge tous les chandeliers depuis `since` jusqu'à maintenant.
    Retourne un DataFrame Binance raw (lowercase columns).
    """
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]

    start_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)
    end_ms   = (int(pd.Timestamp(until, tz="UTC").timestamp() * 1000)
                if until else None)
    now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    if end_ms is None:
        end_ms = now_ms

    all_rows: List[list] = []
    page = 0
    while start_ms < end_ms:
        rows = fetch_klines(symbol, interval, start_ms, end_ms)
        if not rows:
            break
        all_rows.extend(rows)
        last_open = int(rows[-1][0])
        log.info(
            f"  page {page+1:3d} | {len(rows)} bars | "
            f"{pd.Timestamp(last_open, unit='ms', tz='UTC').strftime('%Y-%m-%d')} "
            f"| total {len(all_rows):,}"
        )
        start_ms = last_open + 1
        page += 1
        if len(rows) < LIMIT:
            break
        time.sleep(0.08)   # ~12 req/s — bien sous la limite Binance

    if not all_rows:
        raise RuntimeError("Aucune donnée reçue de Binance")

    df = pd.DataFrame(all_rows, columns=cols)
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume",
              "quote_volume", "taker_buy_base", "taker_buy_quote"):
        df[c] = df[c].astype(float)
    df["number_of_trades"] = df["number_of_trades"].astype(int)
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 2. Feature engineering
# ═════════════════════════════════════════════════════════════════════════════

def _binance_to_training_format(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Prend le DataFrame Binance (lowercase) et retourne un DataFrame
    compatible avec train_pipeline.py : OHLCV uppercase + toutes les features
    pré-calculées (SNAPSHOT_FEATURES + FEATURES_LONG + FEATURES_SHORT).

    Toutes les features sont calculées ici, avant sauvegarde, pour que le CSV
    soit self-contained — train_pipeline.py n'aura pas à les recalculer.
    """
    df = compute_hourly_features(df_raw, symbol=SYMBOL, include_labels=True)
    df.index.name = "datetime"
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 3. Merge / save
# ═════════════════════════════════════════════════════════════════════════════

def merge_with_existing(df_new: pd.DataFrame, csv_path: Path) -> pd.DataFrame:
    """
    Fusionne le nouveau DataFrame avec le CSV existant.
    Les nouvelles barres remplacent / complètent les anciennes.
    """
    if not csv_path.exists():
        return df_new

    log.info(f"Chargement du CSV existant : {csv_path}")
    df_old = pd.read_csv(csv_path, low_memory=False)
    df_old["datetime"] = pd.to_datetime(df_old["datetime"], utc=True)
    df_old = df_old.set_index("datetime")
    df_old = df_old[~df_old.index.duplicated(keep="last")]

    # Aligner les colonnes
    common_cols = list(set(df_old.columns) & set(df_new.columns))
    df_merged   = pd.concat([
        df_old[common_cols],
        df_new[common_cols],
    ], axis=0)
    df_merged = df_merged[~df_merged.index.duplicated(keep="last")]
    df_merged = df_merged.sort_index()

    n_added = len(df_merged) - len(df_old)
    log.info(f"Merge : {len(df_old):,} + {len(df_new):,} → {len(df_merged):,} barres "
             f"({n_added:+d} nouvelles)")
    return df_merged


def save_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Supprimer les dernières barres sans future_ret_4h (TARGET_COL — 4 barres de NaN en fin)
    if "future_ret_4h" in df.columns:
        df = df[df["future_ret_4h"].notna()].copy()
    elif "future_ret_h" in df.columns:
        df = df[df["future_ret_h"].notna()].copy()
    df.index.name = "datetime"
    df.to_csv(path)
    log.info(f"Sauvegardé : {path}  ({len(df):,} barres  "
             f"{df.index[0].date()} → {df.index[-1].date()})")


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch BTC 1h data from Binance")
    parser.add_argument(
        "--update", action="store_true",
        help="Repartir de la dernière barre du CSV existant (mise à jour incrémentale)",
    )
    parser.add_argument(
        "--since", default=BINANCE_START,
        help=f"Date de début ISO (défaut : {BINANCE_START})",
    )
    parser.add_argument(
        "--output", default=str(OUTPUT_CSV),
        help=f"Chemin de sortie (défaut : {OUTPUT_CSV})",
    )
    parser.add_argument(
        "--symbol", default=SYMBOL,
        help=f"Symbole Binance (défaut : {SYMBOL})",
    )
    parser.add_argument(
        "--no-mongo", action="store_true",
        help="Ne pas ingérer les données dans MongoDB (CSV seulement)",
    )
    parser.add_argument(
        "--mongo-uri", default=DEFAULT_MONGO_URI,
        help=f"URI MongoDB (défaut : {DEFAULT_MONGO_URI})",
    )
    parser.add_argument(
        "--mongo-db", default=DEFAULT_TRADER_DB,
        help=f"Base MongoDB OHLCV (défaut : {DEFAULT_TRADER_DB})",
    )
    parser.add_argument(
        "--mongo-collection", default=DEFAULT_HISTORICAL_COLLECTION,
        help=f"Collection MongoDB OHLCV (défaut : {DEFAULT_HISTORICAL_COLLECTION})",
    )
    args = parser.parse_args()

    output = Path(args.output)
    since  = args.since
    mongo_symbol = normalize_symbol(args.symbol)

    # Mode incrémental : repartir de la dernière barre connue
    if args.update and output.exists():
        df_existing = pd.read_csv(output, usecols=["datetime"], low_memory=False)
        last_dt = pd.to_datetime(df_existing["datetime"]).max()
        # Repartir 24h avant la dernière barre pour recomputer les features correctement
        since = (last_dt - pd.Timedelta(hours=240)).strftime("%Y-%m-%d %H:%M:%S")
        log.info(f"Mode update — repartir depuis {since} (dernière barre : {last_dt})")
    elif args.update and not args.no_mongo:
        try:
            last_dt = get_latest_ohlcv_timestamp(
                symbol=mongo_symbol,
                interval=INTERVAL,
                mongo_uri=args.mongo_uri,
                mongo_db=args.mongo_db,
                collection_name=args.mongo_collection,
            )
            if last_dt is not None:
                last_ts = pd.to_datetime(last_dt, utc=True)
                since = (last_ts - pd.Timedelta(hours=240)).strftime("%Y-%m-%d %H:%M:%S")
                log.info(f"Mode update Mongo — repartir depuis {since} (dernière barre : {last_dt})")
        except Exception as exc:
            log.warning(f"Impossible de lire le dernier timestamp MongoDB ({exc}); fallback --since={since}")

    log.info(f"Téléchargement {args.symbol} 1h depuis {since} …")
    df_raw = fetch_all_klines(
        symbol   = args.symbol,
        interval = INTERVAL,
        since    = since,
    )
    log.info(f"Téléchargé : {len(df_raw):,} barres raw")

    log.info("Calcul des features …")
    df_feat = _binance_to_training_format(df_raw)
    log.info(f"Features calculées : {df_feat.shape}")

    if args.update and output.exists():
        df_feat = merge_with_existing(df_feat, output)

    save_csv(df_feat, output)

    if not args.no_mongo:
        df_mongo = df_feat
        if "future_ret_4h" in df_mongo.columns:
            df_mongo = df_mongo[df_mongo["future_ret_4h"].notna()].copy()
        elif "future_ret_h" in df_mongo.columns:
            df_mongo = df_mongo[df_mongo["future_ret_h"].notna()].copy()
        if df_mongo.empty:
            log.warning("Aucune ligne complete a ingérer dans MongoDB")
        else:
            stats = upsert_ohlcv_dataframe(
                df_mongo,
                symbol=mongo_symbol,
                interval=INTERVAL,
                source="binance",
                mongo_uri=args.mongo_uri,
                mongo_db=args.mongo_db,
                collection_name=args.mongo_collection,
            )
            log.info(
                "MongoDB upsert : %s.%s %s %s | processed=%s upserted=%s modified=%s",
                args.mongo_db,
                args.mongo_collection,
                mongo_symbol,
                INTERVAL,
                stats["processed"],
                stats["upserted"],
                stats["modified"],
            )
    log.info("Terminé.")


if __name__ == "__main__":
    main()
