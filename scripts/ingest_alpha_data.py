#!/usr/bin/env python3
"""
scripts/ingest_alpha_data.py
============================
Ingère les données alpha GRATUITES dans MongoDB et enrichit la collection OHLCV/features.

Sources (sans clé API) :
  ├── Binance Futures : funding rate (depuis 2020), OI, L/S ratio, taker flow
  ├── Alternative.me  : Fear & Greed Index (depuis 2018, daily)
  └── CoinGecko       : dominance BTC, market cap global

Pipeline :
  1. Télécharge l'historique complet de chaque source
  2. Stocke dans des collections dédiées (derivatives, sentiment)
  3. Enrichit chaque barre OHLCV 1h avec ces features par jointure temporelle
  4. Met à jour la collection OHLCV/features (upsert par champ)

Usage :
  python scripts/ingest_alpha_data.py            # Full depuis 2020
  python scripts/ingest_alpha_data.py --update   # Incrémental (30 derniers jours)
  python scripts/ingest_alpha_data.py --no-enrich # Stocke sans toucher à OHLCV
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from pymongo import MongoClient, UpdateOne, ASCENDING

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MONGO_URI = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
DB_NAME   = os.getenv("FUTUR_MONGO_DB", os.getenv("MONGODB_DB", "trader"))
FEATURE_COLLECTION = os.getenv(
    "FUTUR_MONGO_FEATURE_COLLECTION",
    os.getenv(
        "MONGODB_FEATURE_COLLECTION",
        os.getenv("FUTUR_MONGO_OHLCV_COLLECTION", os.getenv("MONGODB_HIST_COLLECTION", "historical_ohlcv_enriched")),
    ),
)
SYMBOL    = "BTCUSDT"

# ─────────────────────────────────────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "futur-alpha-ingest/1.0"})

def _get(url: str, params: dict = None, retries: int = 5, ok_404: bool = False) -> dict | list:
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=15)
            if ok_404 and r.status_code == 404:
                return []
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if ok_404 and e.response is not None and e.response.status_code == 404:
                return []
            if i == retries - 1:
                raise
            log.warning(f"HTTP error ({e}), retry {i+1}/{retries}...")
            time.sleep(2 ** i)
        except Exception as e:
            if i == retries - 1:
                raise
            log.warning(f"HTTP error ({e}), retry {i+1}/{retries}...")
            time.sleep(2 ** i)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# 1. BINANCE FUTURES — Funding Rate (historique complet depuis sept. 2020)
# ─────────────────────────────────────────────────────────────────────────────

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
FUNDING_START_MS = int(datetime(2020, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)


def fetch_funding_rates(since_ms: int = FUNDING_START_MS) -> pd.DataFrame:
    """Télécharge l'historique complet des funding rates BTCUSDT."""
    log.info("Funding rates : téléchargement depuis %s",
             pd.Timestamp(since_ms, unit="ms", tz="UTC").date())
    rows = []
    start = since_ms
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    while start < now_ms:
        data = _get(FUNDING_URL, {"symbol": SYMBOL, "startTime": start, "limit": 1000})
        if not data:
            break
        rows.extend(data)
        last = int(data[-1]["fundingTime"])
        log.info("  funding: %d rows | up to %s",
                 len(rows), pd.Timestamp(last, unit="ms", tz="UTC").date())
        if len(data) < 1000:
            break
        start = last + 1
        time.sleep(0.05)

    if not rows:
        log.warning("Aucun funding rate récupéré")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce").fillna(0.0)
    df["mark_price"]   = pd.to_numeric(df.get("markPrice", 0), errors="coerce").fillna(0.0)

    # Features dérivées
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["funding_rate_8h_annualized"] = df["funding_rate"] * 3 * 365 * 100  # annualisé %
    df["funding_cumul_3d"]  = df["funding_rate"].rolling(9,  min_periods=1).sum()   # 3×8h=9 périodes
    df["funding_cumul_7d"]  = df["funding_rate"].rolling(21, min_periods=1).sum()
    df["funding_zscore_30d"] = (
        (df["funding_rate"] - df["funding_rate"].rolling(90, min_periods=1).mean())
        / df["funding_rate"].rolling(90, min_periods=1).std().replace(0, 1)
    )
    df["funding_extreme_long"]  = (df["funding_rate"] >  0.001).astype(int)  # longs chauds
    df["funding_extreme_short"] = (df["funding_rate"] < -0.001).astype(int)  # shorts chauds

    return df[["timestamp","funding_rate","mark_price",
               "funding_rate_8h_annualized","funding_cumul_3d","funding_cumul_7d",
               "funding_zscore_30d","funding_extreme_long","funding_extreme_short"]]


# ─────────────────────────────────────────────────────────────────────────────
# 2. BINANCE FUTURES — Open Interest, L/S Ratio, Taker Flow (30 derniers jours)
# ─────────────────────────────────────────────────────────────────────────────

BINANCE_FUTURES_DATA = "https://fapi.binance.com/futures/data"

def _fetch_futures_hist(endpoint: str, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Endpoint commun pour OI, L/S ratio, taker volume."""
    data = _get(f"{BINANCE_FUTURES_DATA}/{endpoint}",
                {"symbol": SYMBOL, "period": period, "limit": limit}, ok_404=True)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    ts_col = next((c for c in df.columns if "time" in c.lower() or "Time" in c), None)
    if ts_col:
        df["timestamp"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    return df


def fetch_open_interest() -> pd.DataFrame:
    log.info("Open Interest : téléchargement (500 dernières heures)")
    df = _fetch_futures_hist("openInterestHist")
    if df.empty:
        return df
    df["oi"]       = df["sumOpenInterest"].astype(float)
    df["oi_value"] = df["sumOpenInterestValue"].astype(float)
    df["oi_change_pct"] = df["oi"].pct_change().fillna(0)
    df["oi_zscore_48h"] = (
        (df["oi"] - df["oi"].rolling(48, min_periods=1).mean())
        / df["oi"].rolling(48, min_periods=1).std().replace(0, 1)
    )
    return df[["timestamp","oi","oi_value","oi_change_pct","oi_zscore_48h"]]


def fetch_long_short_ratio() -> pd.DataFrame:
    log.info("Long/Short Ratio : téléchargement")
    df_global = _fetch_futures_hist("globalLongShortAccountRatio")
    df_top    = _fetch_futures_hist("topLongShortPositionRatio")

    result_cols = {"timestamp": None}

    if not df_global.empty:
        df_global["ls_ratio_global"]  = df_global["longShortRatio"].astype(float)
        df_global["ls_long_pct"]      = df_global["longAccount"].astype(float).mul(100)
        df_global["ls_short_pct"]     = df_global["shortAccount"].astype(float).mul(100)
        df_global["ls_bias"]          = (df_global["ls_long_pct"] - df_global["ls_short_pct"])
        result_cols.update({"ls_ratio_global": None, "ls_long_pct": None,
                            "ls_short_pct": None, "ls_bias": None})

    if not df_top.empty:
        df_top["ls_ratio_top_traders"] = df_top["longShortRatio"].astype(float)
        result_cols["ls_ratio_top_traders"] = None

    if df_global.empty and df_top.empty:
        return pd.DataFrame()

    df = df_global if not df_global.empty else df_top
    if not df_top.empty and not df_global.empty:
        df = df_global.merge(df_top[["timestamp","ls_ratio_top_traders"]], on="timestamp", how="outer")

    keep = [c for c in result_cols if c in df.columns]
    return df[keep].sort_values("timestamp").reset_index(drop=True)


def fetch_taker_flow() -> pd.DataFrame:
    log.info("Taker Buy/Sell Volume : téléchargement")
    df = _fetch_futures_hist("takerBuySellVol")
    if df.empty:
        return df
    df["taker_buy_vol_futures"]  = df["buySell"].apply(
        lambda x: float(x) if isinstance(x, (int, float)) else
        float(str(x).split("/")[0]) if "/" in str(x) else float(df["buySell"].iloc[0])
    ) if "buySell" in df.columns else pd.Series(0.0, index=df.index)

    # L'API retourne directement des colonnes buyVol / sellVol sur certaines versions
    if "buyVol" in df.columns:
        df["futures_buy_vol"]  = df["buyVol"].astype(float)
        df["futures_sell_vol"] = df["sellVol"].astype(float)
        df["futures_buy_ratio"] = df["futures_buy_vol"] / (df["futures_buy_vol"] + df["futures_sell_vol"] + 1e-9)
        return df[["timestamp","futures_buy_vol","futures_sell_vol","futures_buy_ratio"]]
    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 3. FEAR & GREED INDEX (Alternative.me — depuis 2018, daily)
# ─────────────────────────────────────────────────────────────────────────────

FNG_URL = "https://api.alternative.me/fng/?limit=2000&format=json"

def fetch_fear_greed() -> pd.DataFrame:
    log.info("Fear & Greed Index : téléchargement (historique complet)")
    data = _get(FNG_URL)
    rows = data.get("data", []) if isinstance(data, dict) else data
    if not rows:
        log.warning("Aucune donnée Fear & Greed")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
    df["fng_value"] = df["value"].astype(int)
    df["fng_class"] = df["value_classification"]

    # Encode la classification
    fng_map = {
        "Extreme Fear": 0, "Fear": 1, "Neutral": 2, "Greed": 3, "Extreme Greed": 4
    }
    df["fng_encoded"] = df["fng_class"].map(fng_map).fillna(2)

    # Features contrarian
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["fng_ma7"]     = df["fng_value"].rolling(7,  min_periods=1).mean()
    df["fng_ma30"]    = df["fng_value"].rolling(30, min_periods=1).mean()
    df["fng_extreme_fear"]  = (df["fng_value"] <= 20).astype(int)  # signal contrarian LONG
    df["fng_extreme_greed"] = (df["fng_value"] >= 80).astype(int)  # signal contrarian SHORT
    df["fng_momentum"] = df["fng_value"] - df["fng_value"].shift(7).fillna(df["fng_value"])

    return df[["timestamp","fng_value","fng_class","fng_encoded",
               "fng_ma7","fng_ma30","fng_extreme_fear","fng_extreme_greed","fng_momentum"]]


# ─────────────────────────────────────────────────────────────────────────────
# 4. MongoDB helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    return MongoClient(MONGO_URI)[DB_NAME]


def store_collection(df: pd.DataFrame, coll_name: str, ts_col: str = "timestamp") -> int:
    """Upsert un DataFrame dans une collection MongoDB."""
    if df.empty:
        log.warning(f"  {coll_name}: DataFrame vide, rien à stocker")
        return 0

    db   = get_db()
    coll = db[coll_name]
    coll.create_index(ts_col)

    ops = []
    for row in df.to_dict("records"):
        ts = row[ts_col]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        doc = {k: (None if (isinstance(v, float) and np.isnan(v)) else
                   (v.to_pydatetime() if hasattr(v, "to_pydatetime") else v))
               for k, v in row.items()}
        ops.append(UpdateOne({ts_col: ts}, {"$set": doc}, upsert=True))

    if not ops:
        return 0

    BATCH = 1000
    total = 0
    for i in range(0, len(ops), BATCH):
        res = coll.bulk_write(ops[i:i+BATCH], ordered=False)
        total += res.upserted_count + res.modified_count

    log.info(f"  {coll_name}: {total} docs upserted/modified ({len(df)} total)")
    return total


# ─────────────────────────────────────────────────────────────────────────────
# 5. Enrichissement OHLCV
# ─────────────────────────────────────────────────────────────────────────────

def _floor_hour(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.floor("h")


def enrich_ohlcv_with_alpha(
    funding_df: pd.DataFrame,
    oi_df:      pd.DataFrame,
    ls_df:      pd.DataFrame,
    taker_df:   pd.DataFrame,
    fng_df:     pd.DataFrame,
) -> int:
    """
    Pour chaque barre OHLCV, trouve les features alpha les plus récentes
    et les injecte dans le document MongoDB par upsert partiel ($set).
    """
    db   = get_db()
    ohlcv = db[FEATURE_COLLECTION]

    total_docs = ohlcv.count_documents({})
    log.info(f"Enrichissement OHLCV : {total_docs} barres à traiter")

    # Préparer des index temporels pour chaque source
    def build_index(df: pd.DataFrame, ts_col: str = "timestamp") -> pd.DataFrame | None:
        if df.empty:
            return None
        df2 = df.copy()
        df2["_ts_floor"] = df2[ts_col].apply(_floor_hour)
        return df2.set_index("_ts_floor").sort_index()

    idx_funding = build_index(funding_df)
    idx_oi      = build_index(oi_df)
    idx_ls      = build_index(ls_df)
    idx_taker   = build_index(taker_df)

    # Fear & Greed : daily → on mappe chaque heure à la valeur du jour
    idx_fng = None
    if not fng_df.empty:
        fng2 = fng_df.copy()
        fng2["_date"] = fng2["timestamp"].dt.date
        fng2 = fng2.set_index("_date").sort_index()
        idx_fng = fng2

    def _native(v):
        if isinstance(v, (np.integer,)):   return int(v)
        if isinstance(v, (np.floating,)):  return None if np.isnan(v) else float(v)
        if isinstance(v, (np.bool_,)):     return bool(v)
        if isinstance(v, float) and np.isnan(v): return None
        return v

    def lookup(index, ts_floor, cols):
        """Récupère les valeurs les plus récentes ≤ ts_floor."""
        if index is None:
            return {}
        try:
            sub = index.loc[:ts_floor]
            if sub.empty:
                return {}
            row = sub.iloc[-1]
            return {c: _native(row[c]) for c in cols if c in row.index}
        except Exception:
            return {}

    ops = []
    cursor = ohlcv.find({}, {"timestamp": 1}).sort("timestamp", ASCENDING)

    for doc in cursor:
        raw_ts = doc["timestamp"]
        if raw_ts is None:
            continue

        ts = pd.Timestamp(raw_ts, tz="UTC") if raw_ts.tzinfo is None else pd.Timestamp(raw_ts)
        ts_floor = _floor_hour(ts)
        ts_date  = ts_floor.date()

        update = {}

        # Funding rate (le funding le plus récent ≤ ts)
        update.update(lookup(idx_funding, ts_floor, [
            "funding_rate","funding_rate_8h_annualized","funding_cumul_3d",
            "funding_cumul_7d","funding_zscore_30d",
            "funding_extreme_long","funding_extreme_short"
        ]))

        # Open Interest
        update.update(lookup(idx_oi, ts_floor, [
            "oi","oi_value","oi_change_pct","oi_zscore_48h"
        ]))

        # Long/Short ratio
        update.update(lookup(idx_ls, ts_floor, [
            "ls_ratio_global","ls_long_pct","ls_short_pct","ls_bias","ls_ratio_top_traders"
        ]))

        # Taker flow futures
        update.update(lookup(idx_taker, ts_floor, [
            "futures_buy_vol","futures_sell_vol","futures_buy_ratio"
        ]))

        # Fear & Greed (daily)
        if idx_fng is not None:
            try:
                sub = idx_fng.loc[:ts_date]
                if not sub.empty:
                    row = sub.iloc[-1]
                    fng_cols = ["fng_value","fng_encoded","fng_ma7","fng_ma30",
                                "fng_extreme_fear","fng_extreme_greed","fng_momentum"]
                    for c in fng_cols:
                        if c in row.index:
                            update[c] = _native(row[c])
            except Exception:
                pass

        if update:
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": update}))

        if len(ops) >= 2000:
            ohlcv.bulk_write(ops, ordered=False)
            ops = []

    if ops:
        ohlcv.bulk_write(ops, ordered=False)

    log.info(f"Enrichissement terminé : {total_docs} barres mises à jour")
    return total_docs


# ─────────────────────────────────────────────────────────────────────────────
# 6. Export CSV enrichi (pour le training)
# ─────────────────────────────────────────────────────────────────────────────

def export_enriched_csv(output_path: Path) -> None:
    """Exporte toutes les barres enrichies depuis MongoDB vers CSV."""
    db    = get_db()
    ohlcv = db[FEATURE_COLLECTION]
    total = ohlcv.count_documents({})
    log.info(f"Export CSV : {total} barres depuis MongoDB → {output_path}")

    BATCH = 5000
    dfs   = []
    for skip in range(0, total, BATCH):
        docs = list(ohlcv.find({}, {"_id": 0}).sort("timestamp", ASCENDING).skip(skip).limit(BATCH))
        if not docs:
            break
        dfs.append(pd.DataFrame(docs))

    if not dfs:
        log.warning("Aucune donnée à exporter")
        return

    df = pd.concat(dfs, ignore_index=True)
    df["datetime"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.drop(columns=["timestamp"], errors="ignore")
    df = df.sort_values("datetime").set_index("datetime")
    df = df[~df.index.duplicated(keep="last")]

    # Renommer OHLCV en majuscules pour compatibilité train_pipeline.py
    rename = {"open": "Open","high": "High","low": "Low","close": "Close","volume": "Volume"}
    df = df.rename(columns=rename)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path)
    log.info(f"✓ CSV exporté : {output_path} ({len(df):,} barres, {len(df.columns)} colonnes)")
    log.info(f"  Période : {df.index[0].date()} → {df.index[-1].date()}")

    alpha_cols = [c for c in df.columns if any(x in c for x in [
        "funding","oi","ls_","fng","futures_","taker_buy"
    ])]
    log.info(f"  Features alpha ({len(alpha_cols)}) : {alpha_cols}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion des données alpha dans MongoDB")
    parser.add_argument("--update",    action="store_true", help="Mode incrémental (30 derniers jours)")
    parser.add_argument("--no-enrich", action="store_true", help="Ne pas enrichir OHLCV")
    parser.add_argument("--no-csv",    action="store_true", help="Ne pas exporter le CSV enrichi")
    args = parser.parse_args()

    since_ms = FUNDING_START_MS
    if args.update:
        since_ms = int((datetime.now(timezone.utc) - timedelta(days=35)).timestamp() * 1000)
        log.info("Mode UPDATE : 35 derniers jours uniquement")

    log.info("=" * 60)
    log.info("INGESTION ALPHA DATA")
    log.info("=" * 60)

    # 1. Funding rates (historique long)
    funding_df = fetch_funding_rates(since_ms)
    store_collection(funding_df, "derivatives_funding")

    # 2. Open Interest (30 jours)
    oi_df = fetch_open_interest()
    store_collection(oi_df, "derivatives_oi")

    # 3. Long/Short ratio (30 jours)
    ls_df = fetch_long_short_ratio()
    store_collection(ls_df, "derivatives_ls")

    # 4. Taker flow futures (30 jours)
    taker_df = fetch_taker_flow()
    if not taker_df.empty:
        store_collection(taker_df, "derivatives_taker")

    # 5. Fear & Greed (historique complet)
    fng_df = fetch_fear_greed()
    store_collection(fng_df, "sentiment_fng")

    log.info("=" * 60)
    log.info("RÉSUMÉ DES DONNÉES COLLECTÉES")
    log.info("=" * 60)
    for name, df in [("Funding rates", funding_df), ("Open Interest", oi_df),
                     ("L/S Ratio", ls_df), ("Fear & Greed", fng_df)]:
        if not df.empty:
            ts_col = "timestamp"
            log.info(f"  ✓ {name}: {len(df):,} records | "
                     f"{df[ts_col].min().date()} → {df[ts_col].max().date()}")
        else:
            log.info(f"  ✗ {name}: vide")

    # 6. Enrichissement OHLCV
    if not args.no_enrich:
        log.info("=" * 60)
        log.info("ENRICHISSEMENT OHLCV")
        log.info("=" * 60)
        enrich_ohlcv_with_alpha(funding_df, oi_df, ls_df, taker_df, fng_df)

    # 7. Export CSV
    if not args.no_csv:
        log.info("=" * 60)
        log.info("EXPORT CSV ENRICHI")
        log.info("=" * 60)
        export_enriched_csv(ROOT / "data" / "BTCUSD_1h_alpha.csv")

    log.info("=" * 60)
    log.info("TERMINÉ")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
