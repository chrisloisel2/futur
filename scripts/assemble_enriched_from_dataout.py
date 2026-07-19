#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/assemble_enriched_from_dataout.py
=========================================
Construit data/enriched/{SYM}_1h_enriched.parquet depuis les parquets 1m
de data_out/result/{year}_{SYM}_features.parquet.

Pipeline par symbole :
  1. Concat les fichiers annuels
  2. Renommage colonnes (n_trades → number_of_trades, etc.)
  3. Resample OHLCV → 1h (agrégation standard)
  4. Resample colonnes macro → 1h (last + ffill)
  5. compute_enriched_ohlcv_features(1h, include_multi_timeframe=False)
  6. Merge colonnes macro + apply_feature_aliases
  7. compute_label_columns
  8. Sauvegarde parquet

Usage :
  python scripts/assemble_enriched_from_dataout.py
  python scripts/assemble_enriched_from_dataout.py --symbols BTCUSDT ETHUSDT SOLUSDT
  python scripts/assemble_enriched_from_dataout.py --no-cache
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from data_pipeline.enriched_ohlcv_features import compute_enriched_ohlcv_features
from ai.level_0.labels import compute_label_columns

DATA_IN_DIR  = ROOT / "data_out" / "result"
DATA_OUT_DIR = ROOT / "data" / "enriched"
DATA_OUT_DIR.mkdir(parents=True, exist_ok=True)

# Symboles à traiter en priorité (walk-forward core)
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT"]

# Règles d'agrégation OHLCV 1m → 1h
OHLCV_AGG = {
    "open":  "first",
    "high":  "max",
    "low":   "min",
    "close": "last",
    "volume": "sum",
    "number_of_trades": "sum",
    "taker_buy_base_asset_volume": "sum",
    "quote_volume": "sum",
}

# Colonnes macro à forward-fill (last par heure)
MACRO_COLS = [
    "funding_rate",
    "fear_greed",
    "oi_sum",
    "oi_value_sum",
    "global_long_short_ratio",
    "taker_buy_sell_ratio",
    "top_trader_lsr",
    "top_trader_lsr_sum",
    "eth_close",
    "bnb_close",
    "spot_close",
    "fred_vixcls",
    "fred_dtwexbgs",
    "fred_fedfunds",
    "tvl_bitcoin",
    # Z-scores pré-calculés dans les parquets 1m
    "funding_z_7d",
    "funding_z_30d",
    "funding_accel",
    "funding_sign",
    "funding_extreme",
    "oi_z_1d",
    "lsr_z_1d",
    "fear_greed_z_30d",
    "extreme_fear",
    "risk_off_proxy",
    "dxy_proxy_chg_1d",
    "dxy_proxy_ret_z_30d",
    "vix_chg_1d",
    "vix_ret_z_30d",
    "eth_btc_ratio",
    "eth_btc_z_7d",
    "cross_basket_ret_1d",
    "top_trader_z_7d",
    "smart_retail_divergence",
]

# Renommage colonnes parquet → standard Binance
COL_RENAME = {
    "n_trades":       "number_of_trades",
    "taker_buy_base": "taker_buy_base_asset_volume",
}


def _apply_feature_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """
    Alias enriched_ohlcv_features names -> FEATURES_INST_LONG + FEATURES_LONG names.
    Tous les aliases sont additifs (colonnes originales preservees).
    """
    # Aliases directs
    direct = {
        # FEATURES_INST_LONG
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
        # Regime gate cols
        "dist_ema_50":           "distance_ema_50",
        "dist_ema_200":          "distance_ema_200",
        "dist_ema_20":           "distance_ema_20",
        # FEATURES_LONG
        "mom_logret_72":         "log_return_72",
        "rsi_13":                "rsi_14",
    }
    for target, source in direct.items():
        if target not in df.columns and source in df.columns:
            df[target] = df[source]

    # mom_logret_72 fallback chain
    if "mom_logret_72" not in df.columns:
        for cand in ("log_return_70", "log_return_50"):
            if cand in df.columns:
                df["mom_logret_72"] = df[cand]
                break

    # ema_spread_50_200 requis par compute_long_regime_col
    if "ema_spread_50_200" not in df.columns:
        if "distance_ema_50" in df.columns and "distance_ema_200" in df.columns:
            df["ema_spread_50_200"] = df["distance_ema_50"] - df["distance_ema_200"]

    # ema_50_200_spread (nom FEATURES_INST_LONG)
    if "ema_50_200_spread" not in df.columns and "ema_spread_50_200" in df.columns:
        df["ema_50_200_spread"] = df["ema_spread_50_200"]

    # ema_21_50_spread
    if "ema_21_50_spread" not in df.columns:
        if "distance_ema_21" in df.columns and "distance_ema_50" in df.columns:
            df["ema_21_50_spread"] = df["distance_ema_21"] - df["distance_ema_50"]

    # high_low_range_pct = (high - low) / close
    if "high_low_range_pct" not in df.columns:
        if all(c in df.columns for c in ("high", "low", "close")):
            df["high_low_range_pct"] = (df["high"] - df["low"]) / df["close"].clip(lower=1e-9)

    # macd_hist_slope
    if "macd_hist_slope" not in df.columns:
        for cand in ("macd_histogram_20", "macd_histogram_14", "macd_histogram_1"):
            if cand in df.columns:
                df["macd_hist_slope"] = df[cand].diff().fillna(0.0)
                break

    # mom_logret_168 = log return 7j (requis par compute_regime_col)
    if "mom_logret_168" not in df.columns:
        if "log_return_200" in df.columns:
            df["mom_logret_168"] = df["log_return_200"]
        elif "close" in df.columns:
            df["mom_logret_168"] = np.log(df["close"] / df["close"].shift(168).replace(0, np.nan))

    # Close majuscule (requis par labels.py)
    if "Close" not in df.columns and "close" in df.columns:
        df["Close"] = df["close"]

    # rv_N aliases for DynamicSizer / MetaSuppressor
    _rv_map = {
        "rv_12": "realized_volatility_14",
        "rv_24": "realized_volatility_20",
        "rv_48": "realized_volatility_50",
        "rv_72": "realized_volatility_50",
        "rv_168": "realized_volatility_100",
    }
    for target, source in _rv_map.items():
        if target not in df.columns and source in df.columns:
            df[target] = df[source]
    if "rv_ratio_24_72" not in df.columns and "rv_24" in df.columns and "rv_72" in df.columns:
        df["rv_ratio_24_72"] = df["rv_24"] / df["rv_72"].replace(0.0, np.nan)
    if "rv_ratio_12_48" not in df.columns and "rv_12" in df.columns and "rv_48" in df.columns:
        df["rv_ratio_12_48"] = df["rv_12"] / df["rv_48"].replace(0.0, np.nan)

    return df


def load_symbol_1h(symbol: str) -> Optional[pd.DataFrame]:
    """
    Charge les fichiers annuels un par un, resample chacun à 1h, concat.
    Évite de charger plusieurs années de 1m en même temps.
    """
    files = sorted(DATA_IN_DIR.glob(f"*_{symbol}_features.parquet"))
    files = [f for f in files if not f.stem.split("_")[1].islower()]

    if not files:
        print(f"  [SKIP] Aucun fichier pour {symbol}")
        return None

    parts_1h = []
    ohlcv_cols_ref = None
    macro_cols_ref = None

    for f in files:
        try:
            df = pd.read_parquet(f)
            df = df.rename(columns=COL_RENAME)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()

            # Détecter les colonnes disponibles sur le premier fichier
            if ohlcv_cols_ref is None:
                ohlcv_cols_ref = {k: v for k, v in OHLCV_AGG.items() if k in df.columns}
                macro_cols_ref = [c for c in MACRO_COLS if c in df.columns]

            ohlcv_avail = {k: v for k, v in ohlcv_cols_ref.items() if k in df.columns}
            macro_avail = [c for c in macro_cols_ref if c in df.columns]

            # Resample immédiatement pour libérer la RAM
            df_ohlcv = df[[c for c in ohlcv_avail]].resample("1h").agg(ohlcv_avail)
            df_ohlcv = df_ohlcv.dropna(subset=["close"])

            if macro_avail:
                df_macro = df[macro_avail].resample("1h").last()
                df_1h_year = df_ohlcv.join(df_macro, how="left")
            else:
                df_1h_year = df_ohlcv.copy()

            parts_1h.append(df_1h_year)
            del df, df_ohlcv
            if macro_avail:
                del df_macro

        except Exception as e:
            print(f"  [WARN] {f.name}: {e}")

    if not parts_1h:
        return None

    df_1h = pd.concat(parts_1h).sort_index()
    df_1h = df_1h[~df_1h.index.duplicated(keep="last")]
    df_1h = df_1h.ffill()  # combler les trous macro inter-années

    print(f"  1h : {len(df_1h):,} barres  "
          f"{df_1h.index[0].date()} -> {df_1h.index[-1].date()}  "
          f"({len(files)} fichiers, {len(df_1h.columns)} colonnes input)")
    return df_1h


def build_1h_enriched(symbol: str, use_cache: bool = True) -> Optional[pd.DataFrame]:
    out_path = DATA_OUT_DIR / f"{symbol}_1h_enriched.parquet"

    if use_cache and out_path.exists():
        print(f"  [CACHE] {out_path.name} — skip (--no-cache pour forcer)")
        return None

    df_1h = load_symbol_1h(symbol)
    if df_1h is None:
        return None

    # Colonnes macro à conserver séparément
    macro_cols_present = [c for c in MACRO_COLS if c in df_1h.columns]
    df_macro_1h = df_1h[macro_cols_present].copy() if macro_cols_present else None

    # Features techniques depuis OHLCV uniquement
    ohlcv_input_cols = [c for c in OHLCV_AGG if c in df_1h.columns]
    df_ohlcv_input = df_1h[ohlcv_input_cols].copy()
    df_ohlcv_input.index.name = "datetime"  # éviter conflit 'timestamp' index + col
    print(f"  Calcul features enrichies (interval=1h, no MTF) ...")
    df_enriched = compute_enriched_ohlcv_features(
        df_ohlcv_input,
        interval="1h",
        include_labels=False,
        include_multi_timeframe=False,
        include_sequence_features=False,
    )

    # Merge colonnes macro
    if df_macro_1h is not None:
        for col in macro_cols_present:
            if col not in df_enriched.columns:
                df_enriched[col] = df_macro_1h[col]

    # Aliases + Close
    df_enriched = _apply_feature_aliases(df_enriched)

    # Labels (requis par le walk-forward)
    df_enriched.index.name = "datetime"
    df_enriched = df_enriched.reset_index()
    df_enriched["datetime"] = pd.to_datetime(df_enriched["datetime"], utc=True)

    try:
        df_enriched = compute_label_columns(df_enriched)
    except Exception as e:
        print(f"  [WARN] compute_label_columns: {e}")

    print(f"  Enrichi : {len(df_enriched):,} barres × {len(df_enriched.columns)} colonnes")
    df_enriched.to_parquet(out_path, index=False)
    print(f"  Sauvegardé → {out_path}")
    return df_enriched


def main(symbols: List[str], use_cache: bool = True) -> None:
    print(f"\n=== assemble_enriched_from_dataout ===")
    print(f"Symboles : {symbols}")
    print(f"Cache    : {'activé' if use_cache else 'désactivé'}\n")

    for sym in symbols:
        print(f"\n── {sym} ──────────────────────────────────────────")
        build_1h_enriched(sym, use_cache=use_cache)

    print(f"\n=== Terminé. Fichiers dans {DATA_OUT_DIR} ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    main(args.symbols, use_cache=not args.no_cache)
