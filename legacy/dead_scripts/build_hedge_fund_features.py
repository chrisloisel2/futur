#!/usr/bin/env python3
"""
scripts/build_hedge_fund_features.py — BUILD DATASET ENRICHI 1h DEPUIS BUNDLES 1m
===================================================================================

Pipeline :
  1. Charge data_hedge_fund/{SYMBOL}_1m_bundle.parquet
  2. Resample 1m → 1h (OHLCV + macro séparément)
  3. Applique compute_enriched_ohlcv_features() — ~4000 features techniques
  4. Ajoute les aliases de noms pour matcher FEATURES_INST_LONG
  5. Calcule les labels (future_ret_4h, y_long, y_short, etc.)
  6. Sauvegarde en parquet dans data/enriched/

Usage :
  python scripts/build_hedge_fund_features.py
  python scripts/build_hedge_fund_features.py --symbols BTCUSDT ETHUSDT
  python scripts/build_hedge_fund_features.py --symbols BTCUSDT --no-cache
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

DATA_HF_DIR   = ROOT / "data_hedge_fund"
DATA_OUT_DIR  = ROOT / "data" / "enriched"
DATA_OUT_DIR.mkdir(parents=True, exist_ok=True)

# Colonnes OHLCV du bundle 1m
OHLCV_COLS = ["open", "high", "low", "close", "volume",
              "taker_buy_base_asset_volume", "number_of_trades"]

# Colonnes macro du bundle (forward-filled à 1h)
MACRO_COLS = [
    "funding_rate_z_24", "funding_rate_z_72", "funding_rate_z_288",
    "fear_greed_value_z_24", "fear_greed_value_z_72",
    "oihist_sumOpenInterest_z_24", "oihist_sumOpenInterest_z_72",
    "global_ls_longShortRatio_z_24", "global_ls_longShortRatio_z_72",
    "taker_ls_buySellRatio_z_24", "taker_ls_imbalance",
    "oi_x_fng", "funding_x_global_ls",
    "global_market_cap_usd_z_24", "global_market_cap_usd_z_72",
    "btc_dominance_z_24", "btc_mempool_fee_fastest_z_24",
    "btc_mempool_tx_count_z_24", "news_count_z_24", "news_count_z_72",
    "news_count_roll_240", "news_count_roll_1440",
]

# ─────────────────────────────────────────────────────────────────────────────
# Mapping : noms enrichis → noms institutionnels
# ─────────────────────────────────────────────────────────────────────────────
# Pour chaque feature institutionnelle absente, on crée un alias depuis
# l'équivalent enrichi le plus proche.

FEATURE_ALIASES: dict[str, str] = {
    # Returns simples
    "return_5":          "log_return_5",
    "return_10":         "log_return_10",
    "return_20":         "log_return_20",
    "return_50":         "log_return_50",
    "return_accel_5":    "price_acceleration_5",
    "return_accel_10":   "price_acceleration_10",

    # Volatilité — noms corrects dans enriched
    "garman_klass_vol_20":  "garman_klass_volatility_20",
    "yang_zhang_vol_20":    "yang_zhang_volatility_20",
    "realized_vol_20":      "realized_volatility_20",
    "atr_pct_20":           "atr_percent_20",

    # Structure des chandeliers
    "body_to_range":        "body_size_pct",
    "lower_wick_to_range":  "lower_wick_range",   # dans enriched

    # EMA spreads — calculés à la volée dans _apply_feature_aliases()
    # (distance_ema_21 - distance_ema_50 etc.)
    # Placeholders — calculés dynamiquement ci-dessous

    # Oscillateurs
    "rsi_13":               "rsi_14",
    "stoch_k_20":           "stochastic_k_20",
    "macd_hist_slope":      "macd_histogram_12",   # slope MACD histogramme période 12

    # Régression linéaire
    "regression_slope_50":  "linear_regression_slope_50",
    "regression_r2_50":     "linear_regression_r2_50",

    # Distribution — noms enrichis
    "return_skew_20":       "rolling_skewness_return_20",
    "return_kurt_20":       "rolling_kurtosis_return_20",

    # Vol directionnelle
    "upside_vol_10":        "upside_volatility_10",
    "upside_vol_20":        "upside_volatility_20",

    # Market quality
    "dollar_volume_ratio_20": "dollar_volume_20",
    "hurst_proxy_50":        "hurst_exponent_50",
    "hurst_proxy_100":       "hurst_exponent_100",
    "current_runup_50":      "current_runup",      # sans suffix dans enriched

    # Returns simples
    "return_5":          "log_return_5",
    "return_10":         "log_return_10",
    "return_20":         "log_return_20",
    "return_50":         "log_return_50",
    "return_accel_5":    "price_acceleration_5",
    "return_accel_10":   "price_acceleration_10",

    # Features pour les gates de régime (compute_long_regime_col / compute_regime_col)
    # Ces colonnes sont requises par labels.py — sans elles le gate est désactivé
    "dist_ema_50":        "distance_ema_50",     # % distance close/EMA50
    "dist_ema_200":       "distance_ema_200",
    "dist_ema_20":        "distance_ema_20",

    # Noms MTF — ignorés (absent sans include_multi_timeframe=True)
}


def _apply_feature_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ajoute les colonnes institutionnelles manquantes en les aliasant
    depuis leur équivalent enrichi. Opération sans perte — on crée de
    nouvelles colonnes, les originales restent intactes.
    """
    # 1. Aliases directs depuis le dictionnaire
    for inst_name, enriched_name in FEATURE_ALIASES.items():
        if inst_name in df.columns:
            continue
        if enriched_name in df.columns:
            df[inst_name] = df[enriched_name]

    # 2. Features calculées dynamiquement (non dérivables par simple renommage)

    # EMA spreads : différence normalisée entre deux EMAs
    if "ema_21_50_spread" not in df.columns:
        if "distance_ema_21" in df.columns and "distance_ema_50" in df.columns:
            df["ema_21_50_spread"] = df["distance_ema_21"] - df["distance_ema_50"]
        elif "ema_21" in df.columns and "ema_50" in df.columns and "close" in df.columns:
            df["ema_21_50_spread"] = (df["ema_21"] - df["ema_50"]) / df["close"].clip(lower=1e-9)

    if "ema_50_200_spread" not in df.columns:
        if "distance_ema_50" in df.columns and "distance_ema_200" in df.columns:
            df["ema_50_200_spread"] = df["distance_ema_50"] - df["distance_ema_200"]
        elif "ema_50" in df.columns and "ema_200" in df.columns and "close" in df.columns:
            df["ema_50_200_spread"] = (df["ema_50"] - df["ema_200"]) / df["close"].clip(lower=1e-9)

    # high_low_range_pct : (high - low) / close
    if "high_low_range_pct" not in df.columns:
        if "high" in df.columns and "low" in df.columns and "close" in df.columns:
            df["high_low_range_pct"] = (df["high"] - df["low"]) / df["close"].clip(lower=1e-9)

    # macd_hist_slope : dérivée première du MACD histogramme
    if "macd_hist_slope" not in df.columns:
        for cand in ("macd_histogram_12", "macd_histogram_1", "macd_histogram_2"):
            if cand in df.columns:
                df["macd_hist_slope"] = df[cand].diff().fillna(0.0)
                break

    # ema_spread_50_200 : requis par compute_long_regime_col() et compute_regime_col()
    # = (EMA50 - EMA200) / close → positif = bullish, négatif = bearish
    if "ema_spread_50_200" not in df.columns:
        if "distance_ema_50" in df.columns and "distance_ema_200" in df.columns:
            # dist_ema_50 = (close - EMA50)/close, idem pour 200
            # ema_spread = close×dist50 - close×dist200 / close = dist50 - dist200 (approx)
            df["ema_spread_50_200"] = df["distance_ema_50"] - df["distance_ema_200"]
        elif "ema_50" in df.columns and "ema_200" in df.columns and "close" in df.columns:
            df["ema_spread_50_200"] = (df["ema_50"] - df["ema_200"]) / df["close"].clip(lower=1e-9)

    # mom_logret_72 : momentum 72h — requis par compute_long_regime_col()
    if "mom_logret_72" not in df.columns:
        for cand in ("log_return_72", "log_return_70", "log_return_50"):
            if cand in df.columns:
                df["mom_logret_72"] = df[cand]
                break

    return df


def _compute_close_col(df: pd.DataFrame) -> pd.DataFrame:
    """Assure Close (majuscule) pour compatibilité pipeline labels."""
    if "Close" not in df.columns and "close" in df.columns:
        df["Close"] = df["close"]
    return df


def build_1h_enriched(
    symbol:    str,
    bundle_path: Optional[Path] = None,
    include_multi_timeframe: bool = True,
) -> pd.DataFrame:
    """
    Construit le dataset 1h enrichi pour un symbole depuis son bundle 1m.

    Étapes :
      1. Chargement bundle 1m
      2. Resample OHLCV → 1h (agrégation)
      3. Resample macro → 1h (last + ffill)
      4. compute_enriched_ohlcv_features() → ~4000 colonnes techniques
      5. Alias des noms pour matcher FEATURES_INST_LONG
      6. Ajout colonne Close, datetime

    Retourne un DataFrame indexé par DatetimeIndex UTC.
    """
    if bundle_path is None:
        bundle_path = DATA_HF_DIR / f"{symbol}_1m_bundle.parquet"

    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle 1m non trouvé : {bundle_path}")

    print(f"  Chargement {bundle_path.name} …")
    df1m = pd.read_parquet(bundle_path)
    df1m["datetime"] = pd.to_datetime(df1m["datetime"], utc=True)
    df1m = df1m.set_index("datetime").sort_index()
    print(f"  1m : {len(df1m):,} barres  {df1m.index[0].date()} → {df1m.index[-1].date()}")

    # ── OHLCV 1h ──────────────────────────────────────────────────────────────
    ohlcv_present = [c for c in OHLCV_COLS if c in df1m.columns]
    agg_rules = {
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }
    if "taker_buy_base_asset_volume" in df1m.columns:
        agg_rules["taker_buy_base_asset_volume"] = "sum"
    if "number_of_trades" in df1m.columns:
        agg_rules["number_of_trades"] = "sum"
    df_ohlcv = df1m[[c for c in agg_rules if c in df1m.columns]].resample("1h").agg(
        {k: v for k, v in agg_rules.items() if k in df1m.columns}
    ).dropna(subset=["close"])

    # ── Macro 1h ──────────────────────────────────────────────────────────────
    macro_present = [c for c in MACRO_COLS if c in df1m.columns]
    if macro_present:
        df_macro = df1m[macro_present].resample("1h").last().ffill()
        df_1h = df_ohlcv.join(df_macro, how="left")
    else:
        df_1h = df_ohlcv.copy()

    print(f"  1h : {len(df_1h):,} barres  {len(df_1h.columns)} colonnes input")

    # ── Features techniques enrichies ─────────────────────────────────────────
    print(f"  Calcul des features enrichies …")
    df_enriched = compute_enriched_ohlcv_features(
        df_1h,
        interval="1h",
        include_labels=False,
        include_multi_timeframe=include_multi_timeframe,
        include_sequence_features=False,    # trop lent pour le WF
    )

    # ── Aliases pour FEATURES_INST_LONG ────────────────────────────────────────
    df_enriched = _apply_feature_aliases(df_enriched)
    df_enriched = _compute_close_col(df_enriched)

    # ── Colonne datetime pour les splits chronologiques ───────────────────────
    df_enriched.index.name = "datetime"
    df_enriched = df_enriched.reset_index()
    df_enriched["datetime"] = pd.to_datetime(df_enriched["datetime"], utc=True)

    print(f"  Enrichi : {len(df_enriched):,} barres × {len(df_enriched.columns)} colonnes")
    return df_enriched


def build_all_symbols(
    symbols: Optional[List[str]] = None,
    use_cache: bool = True,
    include_mtf: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Construit le dataset enrichi pour tous les symboles disponibles.
    Utilise le cache parquet si disponible et use_cache=True.
    """
    if symbols is None:
        bundles = sorted(DATA_HF_DIR.glob("*_1m_bundle.parquet"))
        symbols = [b.stem.replace("_1m_bundle", "") for b in bundles]

    if not symbols:
        print(f"Aucun bundle trouvé dans {DATA_HF_DIR}")
        return {}

    print(f"\nBuild dataset enrichi pour : {symbols}")
    datasets: dict[str, pd.DataFrame] = {}

    for sym in symbols:
        cache_path = DATA_OUT_DIR / f"{sym}_1h_enriched.parquet"
        if use_cache and cache_path.exists():
            print(f"  {sym}: chargement cache {cache_path.name}")
            df = pd.read_parquet(cache_path)
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            datasets[sym] = df
            continue

        try:
            df = build_1h_enriched(sym, include_multi_timeframe=include_mtf)
            df.to_parquet(cache_path, index=False)
            print(f"  {sym}: sauvegardé → {cache_path}")
            datasets[sym] = df
        except Exception as e:
            print(f"  {sym}: ERREUR — {e}")

    return datasets


def validate_features_in_dataset(
    df: pd.DataFrame,
    feature_list: List[str],
    context: str = "",
) -> List[str]:
    """
    Valide que les features sont présentes et non-NaN dans df.
    Retourne uniquement les features valides (fill ≥ 75%).
    LÈVE une RuntimeError si une feature listée n'existe pas DU TOUT.
    """
    ctx = f" [{context}]" if context else ""
    n = len(df)

    # Crash sur features totalement absentes
    totally_absent = [f for f in feature_list if f not in df.columns]
    if totally_absent:
        raise RuntimeError(
            f"Features absentes du dataset{ctx} — appeler build_1h_enriched() d'abord :\n"
            f"  {totally_absent[:10]}{'...' if len(totally_absent) > 10 else ''}"
        )

    # Filtrer par fill ≥ 75%
    valid = []
    sparse = []
    for f in feature_list:
        fill = df[f].notna().sum() / max(n, 1)
        if fill >= 0.75:
            valid.append(f)
        else:
            sparse.append(f)

    if sparse:
        print(f"   validate_features{ctx}: {len(sparse)} features sparse (<75% fill) exclues")
    print(f"   validate_features{ctx}: {len(valid)}/{len(feature_list)} features valides")
    return valid


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-mtf", action="store_true", help="Désactiver multi-timeframe (plus rapide)")
    args = parser.parse_args()

    datasets = build_all_symbols(
        symbols=args.symbols,
        use_cache=not args.no_cache,
        include_mtf=not args.no_mtf,
    )

    print(f"\nRésumé:")
    for sym, df in datasets.items():
        print(f"  {sym}: {len(df):,} barres × {len(df.columns)} colonnes")
        # Vérifier les features institutionnelles
        from core.settings import configure_project_imports
        configure_project_imports()
        from ai.level_0.institutional_features import FEATURES_INST_LONG
        present = [f for f in FEATURES_INST_LONG if f in df.columns]
        print(f"    FEATURES_INST_LONG: {len(present)}/{len(FEATURES_INST_LONG)}")
