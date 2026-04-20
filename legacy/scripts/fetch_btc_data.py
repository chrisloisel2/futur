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

from ai.level_0.live_features import compute_live_features
from ai.level_0.feature_engineering import compute_long_features, compute_short_features

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
    # ── 1. Renommer pour compatibilité compute_live_features ─────────────────
    df = df_raw.rename(columns={
        "taker_buy_base": "taker_buy_base_asset_volume",
        "taker_buy_quote": "taker_buy_quote_asset_volume",
        "quote_volume":   "quote_asset_volume",
    })

    # ── 2. SNAPSHOT_FEATURES (base 39) ───────────────────────────────────────
    # compute_live_features applique ffill().fillna(0) à la fin → aucun NaN
    df = compute_live_features(df)

    # ── 3. atr_14 absolu (requis par train_pipeline.py en plus de atr_pct_14) ─
    hl  = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    df["atr_14"] = tr.ewm(span=14, adjust=False).mean().ffill().fillna(0.0)
    # rv_24 est DÉJÀ calculé par compute_live_features — ne pas re-calculer
    # (re-calcul sans ffill introduirait des NaN sur les zones de gap Binance)

    # ── 4. FEATURES_LONG extras (10 features) ───────────────────────────────
    df = compute_long_features(df)

    # ── 5. FEATURES_SHORT extras (11 features) ───────────────────────────────
    df = compute_short_features(df)

    # ── 6. future_ret_h : log-return 1 barre en avant (label de training) ────
    log_close = np.log(df["close"])
    df["future_ret_h"] = log_close.shift(-1) - log_close
    # NB : la dernière barre aura NaN — intentionnel, dropé au save

    # ── 7. Renommer OHLCV en majuscules (convention du CSV de training) ───────
    df = df.rename(columns={
        "open":    "Open",
        "high":    "High",
        "low":     "Low",
        "close":   "Close",
        "volume":  "Volume",
        "taker_buy_base_asset_volume": "Taker_Buy_Base",
        "taker_buy_quote_asset_volume": "Taker_Buy_Quote",
        "number_of_trades": "Trades",
        "quote_asset_volume": "Quote_Volume",
    })

    # ── 8. Sélection et vérification des colonnes ────────────────────────────
    from ai.level_0.features import FEATURES_LONG, FEATURES_SHORT
    SNAPSHOT_FEATURES = [
        "rv_12", "rv_24", "rv_48", "rv_72", "rv_168",
        "rv_ratio_24_72", "rv_ratio_12_48",
        "atr_pct_14", "boll_width_20",
        "mom_logret_6", "mom_logret_12", "mom_logret_24", "mom_logret_72",
        "mom_sharpe_6", "mom_sharpe_12", "mom_sharpe_24",
        "rsi_14", "cci_20",
        "dist_ema_20", "dist_ema_50", "dist_ema_200",
        "ema_spread_20_50", "ema_spread_50_200",
        "boll_pos_20", "close_in_bar", "intrabar_range_pct",
        "eff_ratio_12", "eff_ratio_24",
        "taker_buy_ratio_base", "delta_taker_pressure",
        "vol_ratio_24", "trades_ratio_24",
        "zscore_close_24", "zscore_ret_24", "skew_ret_24",
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ]
    ohlcv = ["Open", "High", "Low", "Close", "Volume",
             "Taker_Buy_Base", "Taker_Buy_Quote", "Trades", "Quote_Volume"]
    extras = ["atr_14", "future_ret_h"]
    all_features = list(dict.fromkeys(SNAPSHOT_FEATURES + FEATURES_LONG + FEATURES_SHORT))
    keep = ohlcv + all_features + extras

    available = [c for c in keep if c in df.columns]
    missing   = [c for c in keep if c not in df.columns]
    if missing:
        log.warning(f"Colonnes manquantes après feature engineering : {missing}")

    df = df[available].copy()
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
    # Supprimer les dernières lignes sans future_ret_h (barre courante, incomplete)
    if "future_ret_h" in df.columns:
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
    args = parser.parse_args()

    output = Path(args.output)
    since  = args.since

    # Mode incrémental : repartir de la dernière barre connue
    if args.update and output.exists():
        df_existing = pd.read_csv(output, usecols=["datetime"], low_memory=False)
        last_dt = pd.to_datetime(df_existing["datetime"]).max()
        # Repartir 24h avant la dernière barre pour recomputer les features correctement
        since = (last_dt - pd.Timedelta(hours=240)).strftime("%Y-%m-%d %H:%M:%S")
        log.info(f"Mode update — repartir depuis {since} (dernière barre : {last_dt})")

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
    log.info("Terminé.")


if __name__ == "__main__":
    main()
