#!/usr/bin/env python3
"""
scripts/build_multi_asset_data.py — Télécharge et feature-engineering ETH + SOL
=================================================================================

Télécharge les klines 1h depuis Binance pour ETH et SOL, calcule toutes les
features identiques à BTCUSD_1h_alpha.csv, et sauvegarde en CSV.

Usage :
  python scripts/build_multi_asset_data.py                      # ETH + SOL
  python scripts/build_multi_asset_data.py --symbols ETHUSDT    # ETH seulement
  python scripts/build_multi_asset_data.py --update             # Mise à jour incrémentale
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.level_0.feature_engineering import (
    compute_long_features, compute_short_features,
    compute_flow_features, compute_event_features, compute_vwap_features,
)
from ai.level_0.labels import compute_label_columns
from data_pipeline.features import compute_hourly_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paramètres ────────────────────────────────────────────────────────────────
BINANCE_URL = "https://api.binance.com/api/v3/klines"
INTERVAL    = "1h"
LIMIT       = 1000

def _auto_since(sym: str) -> str:
    """Récupère la date de la première bougie 1h disponible sur Binance."""
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": sym, "interval": "1h",
                    "startTime": int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
                    "limit": 1},
            timeout=5,
        )
        data = r.json()
        if data and isinstance(data, list):
            ts = int(data[0][0])
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            # Buffer de 7 jours pour éviter des barres incomplètes en début de série
            from datetime import timedelta
            return (dt + timedelta(days=7)).strftime("%Y-%m-%d")
    except Exception:
        pass
    return "2019-01-01"


def _build_config() -> dict:
    """
    Construit la config complète : actifs déjà téléchargés + 40 nouveaux.
    Utilise la date auto-détectée depuis Binance pour chaque symbole.
    """
    # Actifs déjà téléchargés (dates fixes)
    base = {
        "ETHUSDT":   "2017-09-01",
        "SOLUSDT":   "2020-08-11",
        "BNBUSDT":   "2017-11-01",
        "ADAUSDT":   "2018-04-01",
        "XRPUSDT":   "2018-05-01",
        "LTCUSDT":   "2017-12-01",
        "LINKUSDT":  "2019-01-01",
        "MATICUSDT": "2019-04-01",
        "ATOMUSDT":  "2019-04-01",
        "DOGEUSDT":  "2019-07-01",
        "DOTUSDT":   "2020-08-01",
        "AVAXUSDT":  "2020-09-01",
    }
    # 40 actifs supplémentaires — dates auto-détectées
    extended = [
        "XLMUSDT","TRXUSDT","ETCUSDT","VETUSDT","NEOUSDT",
        "ALGOUSDT","ZECUSDT","BATUSDT","ONEUSDT","ENJUSDT",
        "THETAUSDT","DASHUSDT","QTUMUSDT","IOTAUSDT","FETUSDT",
        "ZILUSDT","ANKRUSDT","ZRXUSDT","ICXUSDT","ONTUSDT",
        "SHIBUSDT","FTMUSDT","NEARUSDT","SANDUSDT","MANAUSDT",
        "GALAUSDT","CRVUSDT","MKRUSDT","COMPUSDT","AAVEUSDT",
        "UNIUSDT","SNXUSDT","YFIUSDT","RUNEUSDT",
        "EGLDUSDT","RENUSDT","BANDUSDT",
    ]
    config = {}
    for sym, since in base.items():
        config[sym] = {"since": since, "output": ROOT / "data" / f"{sym}_1h_features.csv"}
    for sym in extended:
        since = _auto_since(sym)
        config[sym] = {"since": since, "output": ROOT / "data" / f"{sym}_1h_features.csv"}
        log.info(f"  {sym} → depuis {since}")
        import time as _time; _time.sleep(0.05)
    return config


SYMBOLS_CONFIG = _build_config()


# ─────────────────────────────────────────────────────────────────────────────
# Binance fetch
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_page(symbol: str, start_ms: int, end_ms: int) -> List[list]:
    params = {
        "symbol":    symbol,
        "interval":  INTERVAL,
        "startTime": start_ms,
        "endTime":   end_ms,
        "limit":     LIMIT,
    }
    for attempt in range(5):
        try:
            r = requests.get(BINANCE_URL, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 4:
                raise
            log.warning(f"Retry {attempt+1}/5 : {e}")
            time.sleep(2 ** attempt)
    return []


def fetch_1h_ohlcv(symbol: str, since: str, until: Optional[str] = None) -> pd.DataFrame:
    """Télécharge tous les chandeliers 1h pour `symbol` depuis `since`."""
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]

    start_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)
    end_ms   = (int(pd.Timestamp(until, tz="UTC").timestamp() * 1000)
                if until else int(datetime.now(timezone.utc).timestamp() * 1000))

    all_rows: List[list] = []
    page = 0
    while start_ms < end_ms:
        rows = _fetch_page(symbol, start_ms, end_ms)
        if not rows:
            break
        all_rows.extend(rows)
        last_open = int(rows[-1][0])
        log.info(
            f"  {symbol} page {page+1:3d} | {len(rows)} bars | "
            f"{pd.Timestamp(last_open, unit='ms', tz='UTC').strftime('%Y-%m-%d')} "
            f"| total {len(all_rows):,}"
        )
        start_ms = last_open + 1
        page    += 1
        if len(rows) < LIMIT:
            break
        time.sleep(0.08)

    if not all_rows:
        raise RuntimeError(f"Aucune donnée reçue pour {symbol}")

    df = pd.DataFrame(all_rows, columns=cols)
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume",
              "quote_volume", "taker_buy_base", "taker_buy_quote"):
        df[c] = df[c].astype(float)
    df["number_of_trades"] = df["number_of_trades"].astype(int)
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    log.info(f"  {symbol} : {len(df):,} barres  "
             f"{df.index[0].date()} → {df.index[-1].date()}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Feature engineering (identique à BTC)
# ─────────────────────────────────────────────────────────────────────────────

def build_features(df_raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Prend le DataFrame Binance et calcule toutes les features.
    Identique au pipeline BTC, sans les features macro bundle
    (funding, OI, fear/greed) qui ne sont pas disponibles pour ETH/SOL —
    ces colonnes seront imputées à 0 lors de l'entraînement.
    """
    log.info(f"  {symbol} : calcul feature factory canonique max_public_v1…")
    df = compute_hourly_features(df_raw, symbol=symbol, include_labels=True)
    from ai.level_0.constants import TARGET_COL
    df = df[df[TARGET_COL].notna()].copy()
    log.info(f"  {symbol} : {len(df):,} barres valides après feature engineering")

    return df


def _compute_manual_snapshot(df: pd.DataFrame) -> None:
    """Fallback minimal si compute_live_features échoue."""
    import numpy as np
    c = np.log(df["Close"].values.astype(np.float64))
    df["rv_12"]  = pd.Series(c).diff().rolling(12,  min_periods=1).std().values
    df["rv_24"]  = pd.Series(c).diff().rolling(24,  min_periods=1).std().values
    df["rv_48"]  = pd.Series(c).diff().rolling(48,  min_periods=1).std().values
    df["rv_72"]  = pd.Series(c).diff().rolling(72,  min_periods=1).std().values
    df["rv_168"] = pd.Series(c).diff().rolling(168, min_periods=1).std().values
    df["mom_logret_24"] = pd.Series(c - np.roll(c, 24)).fillna(0)
    df["mom_logret_72"] = pd.Series(c - np.roll(c, 72)).fillna(0)
    df["rsi_14"] = 50.0   # placeholder
    for col in ["rv_12","rv_24","rv_48","rv_72","rv_168",
                "mom_logret_24","mom_logret_72","rsi_14"]:
        df[col] = df[col].fillna(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def build_symbol(symbol: str, since: str, output_path: Path, update: bool) -> None:
    """Télécharge + features + sauvegarde pour un symbole."""
    log.info(f"\n{'='*60}")
    log.info(f"  SYMBOLE : {symbol}  |  depuis : {since}")
    log.info(f"{'='*60}")

    since_actual = since
    if update and output_path.exists():
        existing = pd.read_csv(output_path, index_col=0, parse_dates=True, nrows=1)
        last_bar = pd.read_csv(output_path, index_col=0, parse_dates=True).index[-1]
        since_actual = (last_bar + pd.Timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        log.info(f"  Update mode : depuis {since_actual}")

    raw  = fetch_1h_ohlcv(symbol, since=since_actual)
    feat = build_features(raw, symbol)

    if update and output_path.exists() and since_actual != since:
        old = pd.read_csv(output_path, index_col=0, parse_dates=True)
        feat = pd.concat([old, feat]).sort_index()
        feat = feat[~feat.index.duplicated(keep="last")]
        log.info(f"  Merge : {len(old):,} + → {len(feat):,} barres")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    feat.index.name = "datetime"
    feat.to_csv(output_path)
    log.info(f"  Sauvegardé → {output_path}  ({len(feat):,} barres)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=list(SYMBOLS_CONFIG.keys()),
                    help="Symboles à traiter (défaut: ETHUSDT SOLUSDT)")
    ap.add_argument("--update", action="store_true",
                    help="Mode incrémental depuis la dernière barre")
    args = ap.parse_args()

    for sym in args.symbols:
        if sym not in SYMBOLS_CONFIG:
            log.warning(f"Symbole {sym} non configuré — ignoré")
            continue
        cfg = SYMBOLS_CONFIG[sym]
        build_symbol(sym, cfg["since"], cfg["output"], args.update)

    log.info("\n✓ Tous les symboles traités.")


if __name__ == "__main__":
    main()
