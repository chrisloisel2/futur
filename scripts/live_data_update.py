#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/live_data_update.py — Mise à jour live des parquets enrichis
=====================================================================

Fetche les nouvelles barres 1h depuis Binance (depuis la dernière barre
du parquet enrichi), calcule exactement les mêmes features que
assemble_enriched_from_dataout.py, et append au parquet.

GARANTI : les features live sont identiques aux features d'entraînement.

Pipeline :
  1. Charger la queue du parquet enrichi (500 barres de contexte)
  2. Fetcher les nouvelles barres 1h depuis Binance
  3. compute_enriched_ohlcv_features (identique à l'entraînement)
  4. _apply_feature_aliases (identique à l'entraînement)
  5. Append seulement les nouvelles lignes au parquet

Usage :
  python3 scripts/live_data_update.py
  python3 scripts/live_data_update.py --symbols BTCUSDT ETHUSDT
  python3 scripts/live_data_update.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from data_pipeline.enriched_ohlcv_features import compute_enriched_ohlcv_features
from ai.level_0.labels import compute_label_columns

ENRICHED_DIR = ROOT / "data" / "enriched"
BINANCE_URL  = "https://api.binance.com/api/v3/klines"

# Barres de contexte chargées depuis le parquet pour que les features
# fenêtrées (EMA200, rolling 200j, etc.) soient correctement calculées
N_CONTEXT_BARS = 600


# ─── Fetch Binance ────────────────────────────────────────────────────────────

def fetch_binance_1h(symbol: str, since_ms: int) -> pd.DataFrame:
    """
    Charge toutes les barres 1h depuis since_ms jusqu'à maintenant.
    Pagination automatique (max 1000 barres par appel).
    """
    all_rows = []
    start = since_ms

    while True:
        params = {
            "symbol":    symbol,
            "interval":  "1h",
            "startTime": int(start),
            "limit":     1000,
        }
        try:
            r = requests.get(BINANCE_URL, params=params, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"  WARN fetch {symbol}: {e}")
            break

        rows = r.json()
        if not rows:
            break

        all_rows.extend(rows)
        last_open = rows[-1][0]
        if len(rows) < 1000:
            break
        start = last_open + 3_600_000   # +1h en ms

    if not all_rows:
        return pd.DataFrame()

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_volume", "ignore",
    ]
    df = pd.DataFrame(all_rows, columns=cols)
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume",
              "taker_buy_base_asset_volume", "quote_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["number_of_trades"] = pd.to_numeric(df["number_of_trades"],
                                           errors="coerce").fillna(0).astype(int)
    df["Close"] = df["close"]
    df = df.sort_values("datetime").reset_index(drop=True)

    # Exclure la dernière barre (incomplète)
    now_ms = pd.Timestamp.utcnow().floor("h")
    df = df[df["datetime"] < now_ms]

    keep = ["datetime", "open", "high", "low", "close", "Close", "volume",
            "number_of_trades", "taker_buy_base_asset_volume", "quote_volume"]
    return df[[c for c in keep if c in df.columns]]


# ─── Alias — copie exacte de assemble_enriched_from_dataout.py ───────────────

def _apply_feature_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """Identique à assemble_enriched_from_dataout._apply_feature_aliases."""
    direct = {
        "return_5":              "log_return_5",
        "return_10":             "log_return_10",
        "return_20":             "log_return_20",
        "return_50":             "log_return_50",
        "return_accel_5":        "price_acceleration_5",
        "return_accel_10":       "price_acceleration_10",
        "garman_klass_vol_20":   "garman_klass_volatility_20",
        "yang_zhang_vol_20":     "yang_zhang_volatility_20",
        "realized_vol_20":       "realized_volatility_20",
        "atr_pct_20":            "atr_percent_20",
        "body_to_range":         "body_size_pct",
        "lower_wick_to_range":   "lower_wick_range",
        "stoch_k_20":            "stochastic_k_20",
        "regression_slope_50":   "linear_regression_slope_50",
        "regression_r2_50":      "linear_regression_r2_50",
        "return_skew_20":        "rolling_skewness_return_20",
        "return_kurt_20":        "rolling_kurtosis_return_20",
        "upside_vol_10":         "upside_volatility_10",
        "upside_vol_20":         "upside_volatility_20",
        "dollar_volume_ratio_20":"dollar_volume_20",
        "hurst_proxy_50":        "hurst_exponent_50",
        "hurst_proxy_100":       "hurst_exponent_100",
        "current_runup_50":      "current_runup",
        "dist_ema_50":           "distance_ema_50",
        "dist_ema_200":          "distance_ema_200",
        "dist_ema_20":           "distance_ema_20",
        "mom_logret_72":         "log_return_72",
        "rsi_13":                "rsi_14",
    }
    for target, source in direct.items():
        if target not in df.columns and source in df.columns:
            df[target] = df[source]

    if "mom_logret_72" not in df.columns:
        for cand in ("log_return_70", "log_return_50"):
            if cand in df.columns:
                df["mom_logret_72"] = df[cand]
                break

    if "ema_spread_50_200" not in df.columns:
        if "distance_ema_50" in df.columns and "distance_ema_200" in df.columns:
            df["ema_spread_50_200"] = df["distance_ema_50"] - df["distance_ema_200"]

    if "ema_50_200_spread" not in df.columns and "ema_spread_50_200" in df.columns:
        df["ema_50_200_spread"] = df["ema_spread_50_200"]

    if "ema_21_50_spread" not in df.columns:
        if "distance_ema_21" in df.columns and "distance_ema_50" in df.columns:
            df["ema_21_50_spread"] = df["distance_ema_21"] - df["distance_ema_50"]

    if "high_low_range_pct" not in df.columns:
        if all(c in df.columns for c in ("high", "low", "close")):
            df["high_low_range_pct"] = (df["high"] - df["low"]) / df["close"].clip(lower=1e-9)

    if "macd_hist_slope" not in df.columns:
        for cand in ("macd_histogram_20", "macd_histogram_14", "macd_histogram_1"):
            if cand in df.columns:
                df["macd_hist_slope"] = df[cand].diff().fillna(0.0)
                break

    if "mom_logret_168" not in df.columns:
        if "log_return_200" in df.columns:
            df["mom_logret_168"] = df["log_return_200"]
        elif "close" in df.columns:
            df["mom_logret_168"] = np.log(
                df["close"] / df["close"].shift(168).replace(0, np.nan)
            )

    if "Close" not in df.columns and "close" in df.columns:
        df["Close"] = df["close"]

    return df


# ─── MTF features (4h et 1d) — identique à l'assemble ───────────────────────

def _add_mtf_features(df: pd.DataFrame) -> pd.DataFrame:
    """Resampling 1h → 4h et 1h → 1d pour les features multi-timeframe."""
    if "datetime" not in df.columns:
        return df

    df_t = df.set_index("datetime")
    mtf_specs = {
        "4h": ("4h", ["rsi_14", "mom_logret_72", "dist_ema_50", "dist_ema_200",
                       "ema_spread_50_200", "rv_24", "rv_72"]),
        "1d": ("1d", ["rsi_14", "mom_logret_72", "dist_ema_50", "dist_ema_200",
                       "ema_spread_50_200", "rv_24"]),
    }
    for tf, (rule, cols_to_resample) in mtf_specs.items():
        available = [c for c in cols_to_resample if c in df_t.columns]
        if not available:
            continue
        try:
            mtf = df_t[available].resample(rule).last()
            mtf_1h = mtf.reindex(df_t.index, method="ffill")
            for col in available:
                alias = f"{col}_{tf}"
                if alias not in df.columns:
                    df[alias] = mtf_1h[col].values
        except Exception:
            pass
    return df


# ─── Core update ─────────────────────────────────────────────────────────────

def update_enriched(symbol: str, dry_run: bool = False) -> int:
    """
    Met à jour le parquet enrichi pour symbol.
    Retourne le nombre de nouvelles barres ajoutées.
    """
    path = ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"
    if not path.exists():
        print(f"  [SKIP] {path.name} n'existe pas — lancer assemble_enriched_from_dataout.py d'abord")
        return 0

    # ── 1. Charger la queue du parquet (contexte pour les features fenêtrées) ─
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    total_rows = pf.metadata.num_rows

    ohlcv_cols = ["datetime", "open", "high", "low", "close", "Close",
                  "volume", "number_of_trades", "taker_buy_base_asset_volume"]
    avail_cols = [c for c in ohlcv_cols if c in pf.schema_arrow.names]

    offset = max(0, total_rows - N_CONTEXT_BARS)
    df_tail = pd.read_parquet(path, columns=avail_cols).iloc[offset:]
    df_tail["datetime"] = pd.to_datetime(df_tail["datetime"], utc=True)
    df_tail = df_tail.sort_values("datetime").reset_index(drop=True)

    last_dt = df_tail["datetime"].iloc[-1]
    last_ms = int(last_dt.timestamp() * 1000) + 3_600_000   # +1h

    print(f"  {symbol}: dernier bar = {last_dt.strftime('%Y-%m-%d %H:%M')} UTC")

    # ── 2. Fetch nouvelles barres ─────────────────────────────────────────────
    df_new = fetch_binance_1h(symbol, last_ms)
    if df_new.empty:
        print(f"  {symbol}: aucune nouvelle barre")
        return 0

    n_new = len(df_new)
    print(f"  {symbol}: {n_new} nouvelles barres "
          f"({df_new['datetime'].iloc[0].strftime('%Y-%m-%d %H:%M')} → "
          f"{df_new['datetime'].iloc[-1].strftime('%Y-%m-%d %H:%M')})")

    if dry_run:
        print(f"  [DRY-RUN] {n_new} barres prêtes à être ajoutées — skip écriture")
        return n_new

    # ── 3. Combiner contexte + nouvelles barres ───────────────────────────────
    df_combined = pd.concat([df_tail, df_new], ignore_index=True)
    df_combined = df_combined.drop_duplicates("datetime").sort_values("datetime")
    df_combined = df_combined.reset_index(drop=True)

    # ── 4. Calculer les features (identique à l'entraînement) ────────────────
    df_ohlcv = df_combined.set_index("datetime")[
        [c for c in ("open", "high", "low", "close", "volume",
                     "number_of_trades", "taker_buy_base_asset_volume")
         if c in df_combined.columns]
    ].copy()
    df_ohlcv.index.name = "datetime"

    df_enriched = compute_enriched_ohlcv_features(
        df_ohlcv,
        interval="1h",
        include_labels=False,
        include_multi_timeframe=False,
        include_sequence_features=False,
    )
    df_enriched = df_enriched.reset_index()
    df_enriched["datetime"] = pd.to_datetime(df_enriched["datetime"], utc=True)

    df_enriched = _apply_feature_aliases(df_enriched)
    df_enriched = _add_mtf_features(df_enriched)

    try:
        df_enriched = compute_label_columns(df_enriched)
    except Exception:
        pass

    # ── 5. Garder uniquement les nouvelles barres ─────────────────────────────
    df_only_new = df_enriched[df_enriched["datetime"] > last_dt].copy()
    if df_only_new.empty:
        print(f"  {symbol}: 0 nouvelle barre après feature computation")
        return 0

    # ── 6. Aligner les colonnes avec le parquet existant ──────────────────────
    df_existing_schema = pd.read_parquet(path, columns=None).iloc[:1]
    existing_cols = df_existing_schema.columns.tolist()

    # Ajouter les colonnes manquantes (NaN pour macro non disponible en live)
    for col in existing_cols:
        if col not in df_only_new.columns:
            df_only_new[col] = np.nan

    df_only_new = df_only_new[[c for c in existing_cols if c in df_only_new.columns]]

    # ── 7. Append au parquet ──────────────────────────────────────────────────
    df_full = pd.read_parquet(path)
    df_full["datetime"] = pd.to_datetime(df_full["datetime"], utc=True)
    df_full = pd.concat([df_full, df_only_new], ignore_index=True)
    df_full = df_full.drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)
    df_full.to_parquet(path, index=False)

    print(f"  {symbol}: +{len(df_only_new)} barres → {path.name} ({len(df_full):,} total)")
    return len(df_only_new)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live data update — Binance → enriched parquet")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"],
                        help="Symboles à mettre à jour")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simuler sans écrire")
    args = parser.parse_args()

    print("=== live_data_update ===")
    total = 0
    for sym in args.symbols:
        n = update_enriched(sym, dry_run=args.dry_run)
        total += n
    print(f"\nTotal : {total} nouvelles barres ajoutées")


if __name__ == "__main__":
    main()
