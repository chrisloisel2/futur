#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_hedge_fund_data.py
==========================
Prépare les parquets 1m de /home/qbee/hedge_fund/data_out/result/
en parquets compatibles avec train_pipeline.py :

  - Renommage colonnes OHLCV (timestamp→datetime, quote_volume→quote_asset_volume, …)
  - Ajout taker_buy_quote_asset_volume ≈ taker_buy_base × close
  - Calcul z-scores macro (funding_rate_z_24/72/288, fear_greed_value_z_24/72)
  - Concaténation de toutes les années par symbole
  - Sauvegarde dans data_hedge_fund/<SYM>_1m_bundle.parquet

Usage:
    python scripts/prepare_hedge_fund_data.py
    python scripts/prepare_hedge_fund_data.py --symbols BTCUSDT ETHUSDT
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

FUTUR = Path(__file__).resolve().parents[1]
DATA_OUT = Path("/home/qbee/hedge_fund/data_out/result")
OUT_DIR  = FUTUR / "data_hedge_fund"

SYMBOLS_AVAILABLE = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "ADAUSDT", "AVAXUSDT", "XRPUSDT", "LINKUSDT",
    "DOGEUSDT",
]

# Colonnes OHLCV attendues par train_pipeline.py (raw 1m branch)
OHLCV_COLS_SRC = [
    "timestamp", "open", "high", "low", "close", "volume",
    "quote_volume", "n_trades", "taker_buy_base",
]
# Colonnes macro brutes disponibles dans les parquets hedge_fund
MACRO_RAW_COLS = [
    "funding_rate",
    "fear_greed", "fear_greed_value",
    "oi_sum", "oi_value_sum",
    "global_long_short_ratio",
    "taker_buy_sell_ratio",
]

# Toutes les colonnes macro produites (= MACRO_BUNDLE_COLS etendu)
MACRO_Z_COLS = [
    "funding_rate_z_24",
    "funding_rate_z_72",
    "funding_rate_z_288",
    "fear_greed_value_z_24",
    "fear_greed_value_z_72",
    "news_count_roll_240",
    "news_count_roll_1440",
    # OI / L/S / taker (calcules depuis les donnees disponibles)
    "oihist_sumOpenInterest_z_24",
    "oihist_sumOpenInterest_z_72",
    "global_ls_longShortRatio_z_24",
    "global_ls_longShortRatio_z_72",
    "taker_ls_buySellRatio_z_24",
    "taker_ls_imbalance",
    "oi_x_fng",
    "funding_x_global_ls",
    # Non disponibles -> 0
    "global_market_cap_usd_z_24",
    "global_market_cap_usd_z_72",
    "btc_dominance_z_24",
    "btc_mempool_fee_fastest_z_24",
    "btc_mempool_tx_count_z_24",
    "news_count_z_24",
    "news_count_z_72",
]


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mu  = series.rolling(window, min_periods=max(2, window // 10)).mean()
    std = series.rolling(window, min_periods=max(2, window // 10)).std()
    return (series - mu) / std.clip(lower=1e-9)


def _get_cols_to_load(files: list) -> list:
    """Retourne uniquement les colonnes utiles presentes dans l'union de tous les parquets."""
    import pyarrow.parquet as pq
    available: set = set()
    for f in files:
        available |= set(pq.read_schema(f).names)
    wanted = set(OHLCV_COLS_SRC + MACRO_RAW_COLS)
    return sorted(wanted & available)


def prepare_symbol(symbol: str) -> Path:
    """Charge toutes les annees (colonnes minimales), renomme, calcule z-scores, sauvegarde."""
    files = sorted(DATA_OUT.glob(f"*_{symbol}_features.parquet"))
    if not files:
        raise RuntimeError(f"Aucun parquet pour {symbol} dans {DATA_OUT}")

    print(f"\n{'='*60}")
    print(f"  {symbol}  --  {len(files)} annees")

    # Detecter les colonnes a charger (union de tous les fichiers)
    cols_to_load = _get_cols_to_load(files)
    print(f"  Colonnes chargees : {cols_to_load}")

    import pyarrow.parquet as pq
    frames = []
    for f in files:
        year = f.name.split("_")[0]
        print(f"    chargement {year}...", end=" ", flush=True)
        # Charger seulement les colonnes disponibles dans ce fichier
        avail_this = set(pq.read_schema(f).names)
        cols_this = [c for c in cols_to_load if c in avail_this]
        df_yr = pd.read_parquet(f, columns=cols_this)
        # Ajouter les colonnes manquantes comme NaN (seront ffill plus tard)
        for c in cols_to_load:
            if c not in df_yr.columns:
                df_yr[c] = np.nan
        print(f"{len(df_yr):,} lignes")
        frames.append(df_yr)

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"  Total : {len(df):,} barres 1m  [{pd.to_datetime(df['timestamp'].iloc[0]).date()} -> {pd.to_datetime(df['timestamp'].iloc[-1]).date()}]")

    # ── 1. Renommage colonnes OHLCV ──────────────────────────────────────────
    rename_map = {
        "timestamp":      "datetime",
        "quote_volume":   "quote_asset_volume",
        "n_trades":       "number_of_trades",
        "taker_buy_base": "taker_buy_base_asset_volume",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # taker_buy_quote_asset_volume ≈ taker_buy_base × close
    if "taker_buy_base_asset_volume" in df.columns and "close" in df.columns:
        df["taker_buy_quote_asset_volume"] = (
            pd.to_numeric(df["taker_buy_base_asset_volume"], errors="coerce")
            * pd.to_numeric(df["close"], errors="coerce")
        ).fillna(0.0)
    else:
        df["taker_buy_quote_asset_volume"] = 0.0

    # Colonnes manquantes → 0
    for col in ["quote_asset_volume", "number_of_trades",
                "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]:
        if col not in df.columns:
            df[col] = 0.0

    # ── 2. Z-scores macro (fenetres en barres 1m) ────────────────────────────
    # 24h=1440, 72h=4320, 288h=17280 barres a 1m
    print("  Calcul z-scores macro...", end=" ", flush=True)

    def _get_series(col_candidates):
        for c in col_candidates:
            if c in df.columns:
                return pd.to_numeric(df[c], errors="coerce").ffill().fillna(0.0)
        return None

    # Funding rate
    fr = _get_series(["funding_rate"])
    if fr is not None:
        df["funding_rate_z_24"]  = _rolling_zscore(fr, 1440)
        df["funding_rate_z_72"]  = _rolling_zscore(fr, 4320)
        df["funding_rate_z_288"] = _rolling_zscore(fr, 17280)
    else:
        df["funding_rate_z_24"] = df["funding_rate_z_72"] = df["funding_rate_z_288"] = 0.0

    # Fear & Greed
    fg = _get_series(["fear_greed_value", "fear_greed"])
    if fg is not None:
        fg = fg.where(fg > 0, 50.0)  # 0 = missing
        df["fear_greed_value_z_24"] = _rolling_zscore(fg, 1440)
        df["fear_greed_value_z_72"] = _rolling_zscore(fg, 4320)
    else:
        df["fear_greed_value_z_24"] = df["fear_greed_value_z_72"] = 0.0

    # OI sum
    oi = _get_series(["oi_sum", "oi_value_sum"])
    if oi is not None and (oi != 0).any():
        df["oihist_sumOpenInterest_z_24"] = _rolling_zscore(oi, 1440)
        df["oihist_sumOpenInterest_z_72"] = _rolling_zscore(oi, 4320)
    else:
        df["oihist_sumOpenInterest_z_24"] = df["oihist_sumOpenInterest_z_72"] = 0.0

    # Global Long/Short ratio
    gls = _get_series(["global_long_short_ratio"])
    if gls is not None and (gls != 0).any():
        df["global_ls_longShortRatio_z_24"] = _rolling_zscore(gls, 1440)
        df["global_ls_longShortRatio_z_72"] = _rolling_zscore(gls, 4320)
    else:
        df["global_ls_longShortRatio_z_24"] = df["global_ls_longShortRatio_z_72"] = 0.0

    # Taker buy/sell ratio
    tbsr = _get_series(["taker_buy_sell_ratio"])
    if tbsr is not None and (tbsr != 0).any():
        df["taker_ls_buySellRatio_z_24"] = _rolling_zscore(tbsr, 1440)
        df["taker_ls_imbalance"]         = _rolling_zscore(tbsr - 1.0, 1440)
    else:
        df["taker_ls_buySellRatio_z_24"] = df["taker_ls_imbalance"] = 0.0

    # Derived products
    df["oi_x_fng"]          = (df["oihist_sumOpenInterest_z_24"]
                               * df["fear_greed_value_z_24"])
    df["funding_x_global_ls"] = (df["funding_rate_z_24"]
                                 * df["global_ls_longShortRatio_z_24"])

    # Colonnes non disponibles -> 0 (signal neutre)
    for col in ["global_market_cap_usd_z_24", "global_market_cap_usd_z_72",
                "btc_dominance_z_24",
                "btc_mempool_fee_fastest_z_24", "btc_mempool_tx_count_z_24",
                "news_count_z_24", "news_count_z_72",
                "news_count_roll_240", "news_count_roll_1440"]:
        df[col] = 0.0

    for col in MACRO_Z_COLS:
        df[col] = df[col].fillna(0.0)
    print("OK")

    # Supprimer les colonnes macro brutes (non attendues par le pipeline)
    for col in MACRO_RAW_COLS:
        if col in df.columns:
            df = df.drop(columns=[col])

    # ── 3. Sauvegarde ────────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{symbol}_1m_bundle.parquet"
    df.to_parquet(out_path, index=False, engine="pyarrow", compression="zstd")
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"  -> Sauvegarde : {out_path.name}  ({size_mb:.0f} MB)")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=SYMBOLS_AVAILABLE)
    args = ap.parse_args()

    prepared = []
    errors   = []
    for sym in args.symbols:
        try:
            p = prepare_symbol(sym)
            prepared.append((sym, p))
        except Exception as e:
            print(f"  ERROR {sym}: {e}")
            errors.append(sym)

    print(f"\n{'='*60}")
    print(f"Préparation terminée : {len(prepared)} OK, {len(errors)} erreurs")
    if errors:
        print(f"  Erreurs : {errors}")
    for sym, p in prepared:
        print(f"  {sym:12} {p}")


if __name__ == "__main__":
    main()
