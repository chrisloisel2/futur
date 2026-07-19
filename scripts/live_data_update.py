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

ENRICHED_DIR    = ROOT / "data" / "enriched"
BINANCE_URL     = "https://api.binance.com/api/v3/klines"
BINANCE_FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"

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


# ─── Fetch funding rate (Binance Futures) ────────────────────────────────────

def fetch_funding_rate(symbol: str, since_ms: int) -> pd.DataFrame:
    """
    Charge les funding rates depuis Binance Futures depuis since_ms.
    Retourne un DataFrame [datetime, funding_rate] à fréquence 8h.
    Si l'asset n'est pas un perp Binance (erreur API) → retourne DataFrame vide.
    """
    all_rows: list = []
    start = since_ms
    while True:
        try:
            r = requests.get(
                BINANCE_FUNDING,
                params={"symbol": symbol, "startTime": int(start), "limit": 1000},
                timeout=10,
            )
            if r.status_code != 200:
                break
            rows = r.json()
            if not rows or isinstance(rows, dict):
                break
            all_rows.extend(rows)
            if len(rows) < 1000:
                break
            start = int(rows[-1]["fundingTime"]) + 1
        except Exception:
            break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["datetime"]     = pd.to_datetime(df["fundingTime"].astype(int), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    return df[["datetime", "funding_rate"]].sort_values("datetime").reset_index(drop=True)


def _add_funding_features(df: pd.DataFrame, df_funding: pd.DataFrame) -> pd.DataFrame:
    """
    Fusionne le funding rate (8h) dans le parquet 1h et calcule les z-scores.
    df doit avoir un index DatetimeTZAware ou une colonne 'datetime'.
    """
    if df_funding.empty:
        for col in ("funding_rate", "funding_rate_z_24", "funding_rate_z_72"):
            if col not in df.columns:
                df[col] = 0.0
        return df

    # Reindex sur l'index 1h par ffill
    if "datetime" in df.columns:
        df_tmp = df.set_index("datetime")
    else:
        df_tmp = df.copy()

    fr = df_funding.set_index("datetime")["funding_rate"]
    fr_aligned = fr.reindex(df_tmp.index, method="ffill").fillna(0.0)

    df_tmp["funding_rate"]    = fr_aligned.values
    df_tmp["funding_rate_z_24"] = (
        (fr_aligned - fr_aligned.rolling(24, min_periods=4).mean())
        / fr_aligned.rolling(24, min_periods=4).std().replace(0, np.nan)
    ).fillna(0.0)
    df_tmp["funding_rate_z_72"] = (
        (fr_aligned - fr_aligned.rolling(72, min_periods=12).mean())
        / fr_aligned.rolling(72, min_periods=12).std().replace(0, np.nan)
    ).fillna(0.0)

    if "datetime" in df.columns:
        df_tmp = df_tmp.reset_index()
    return df_tmp


def _add_taker_flow_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les features order-flow taker absentes du compute_enriched_ohlcv_features.
    Nécessite 'taker_buy_base_asset_volume' et 'volume' dans df.
    """
    if "taker_buy_base_asset_volume" not in df.columns or "volume" not in df.columns:
        for col in ("taker_buy_ratio_base", "taker_flow_imbalance_20", "taker_flow_momentum_5"):
            if col not in df.columns:
                df[col] = 0.0
        return df

    taker = pd.to_numeric(df["taker_buy_base_asset_volume"], errors="coerce").fillna(0.0)
    vol   = pd.to_numeric(df["volume"], errors="coerce").fillna(1.0).clip(lower=1e-9)

    ratio = (taker / vol).clip(0.0, 1.0)
    if "taker_buy_ratio_base" not in df.columns:
        df["taker_buy_ratio_base"] = ratio.values

    # z-score 20h du taker ratio — excès directionnel
    if "taker_flow_imbalance_20" not in df.columns:
        mu  = ratio.rolling(20, min_periods=4).mean()
        sig = ratio.rolling(20, min_periods=4).std().replace(0, np.nan)
        df["taker_flow_imbalance_20"] = ((ratio - mu) / sig).fillna(0.0).clip(-4, 4).values

    # Momentum du taker ratio sur 5 barres
    if "taker_flow_momentum_5" not in df.columns:
        df["taker_flow_momentum_5"] = ratio.diff(5).fillna(0.0).values

    return df


# ─── CVD, OI delta, Basis (microstructure alpha — phase 1 plan) ──────────────

def _z_score(series: pd.Series, window: int) -> pd.Series:
    """Z-score rolling — helper interne."""
    mu  = series.rolling(window, min_periods=max(4, window//8)).mean()
    sig = series.rolling(window, min_periods=max(4, window//8)).std().replace(0, np.nan)
    return ((series - mu) / sig).fillna(0.0).clip(-4, 4)


def _add_cvd_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cumulative Volume Delta depuis taker_buy_base_asset_volume.
    CVD mesure la pression directionnelle nette des takers.
    """
    if "taker_buy_base_asset_volume" not in df.columns or "volume" not in df.columns:
        for col in ("cvd_4h", "cvd_24h", "cvd_72h", "cvd_4h_z", "cvd_24h_z", "cvd_momentum"):
            if col not in df.columns:
                df[col] = 0.0
        return df

    taker = pd.to_numeric(df["taker_buy_base_asset_volume"], errors="coerce").fillna(0)
    vol   = pd.to_numeric(df["volume"], errors="coerce").fillna(1e-9).clip(lower=1e-9)

    # Ratio [0,1] plutôt que delta absolu — indépendant de la taille des barres
    taker_ratio = (taker / vol).clip(0.0, 1.0)
    # Centrer sur 0.5 → delta en [-0.5, 0.5]
    delta_norm = taker_ratio - 0.5

    df["cvd_4h"]       = delta_norm.rolling(4,  min_periods=1).sum()
    df["cvd_24h"]      = delta_norm.rolling(24, min_periods=4).sum()
    df["cvd_72h"]      = delta_norm.rolling(72, min_periods=12).sum()
    df["cvd_4h_z"]     = _z_score(df["cvd_4h"],  96)
    df["cvd_24h_z"]    = _z_score(df["cvd_24h"], 96)
    df["cvd_momentum"] = df["cvd_24h"].diff(6).fillna(0.0).clip(-2, 2)
    return df


def _add_oi_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Open Interest delta depuis oi_sum (colonne macro déjà présente).
    Δ OI × direction prix → régime (accumulation / squeeze / liquidation).
    """
    if "oi_sum" not in df.columns:
        for col in ("oi_delta_1h", "oi_delta_8h", "oi_delta_24h", "oi_price_regime"):
            if col not in df.columns:
                df[col] = 0.0
        return df

    oi = pd.to_numeric(df["oi_sum"], errors="coerce").ffill().fillna(0)
    df["oi_delta_1h"]  = oi.pct_change(1).fillna(0).clip(-0.20, 0.20)
    df["oi_delta_8h"]  = oi.pct_change(8).fillna(0).clip(-0.30, 0.30)
    df["oi_delta_24h"] = oi.pct_change(24).fillna(0).clip(-0.50, 0.50)

    # 4 régimes : 0=short_build, 1=short_squeeze, 2=long_cap, 3=long_build
    close = pd.to_numeric(df["close"], errors="coerce").fillna(method="ffill")
    ret_8h = close.pct_change(8).fillna(0)
    df["oi_price_regime"] = (
        (df["oi_delta_8h"] > 0).astype(int) * 2
        + (ret_8h > 0).astype(int)
    )
    return df


def _add_basis_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Basis spot/perp proxy via funding_rate.
    funding_rate élevé → longs crowded → risque de squeeze baissier.
    """
    if "funding_rate" not in df.columns:
        for col in ("basis_annualized", "basis_momentum_8h", "basis_extreme_long", "basis_extreme_short"):
            if col not in df.columns:
                df[col] = 0.0
        return df

    fr = pd.to_numeric(df["funding_rate"], errors="coerce").fillna(0)
    df["basis_annualized"]   = (fr * 3 * 365 * 100).clip(-200, 200)  # % annualisé
    df["basis_momentum_8h"]  = fr.diff(1).fillna(0)                   # Δ entre périodes
    df["basis_extreme_long"] = (fr >  0.001).astype(float)            # > 0.1% → crowded long
    df["basis_extreme_short"]= (fr < -0.0005).astype(float)           # < -0.05% → crowded short
    return df


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

    # rv_N aliases for DynamicSizer / MetaSuppressor (absent des parquets enrichis)
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


# ─── MTF features (4h et 1d) — noms identiques aux données d'entraînement ───

def _rsi_ewm(series: pd.Series, n: int) -> pd.Series:
    delta = series.diff()
    avg_g = delta.clip(lower=0).ewm(alpha=1.0 / max(n, 1), adjust=False).mean()
    avg_l = (-delta.clip(upper=0)).ewm(alpha=1.0 / max(n, 1), adjust=False).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _adx_ewm(h: pd.Series, l: pd.Series, c: pd.Series, n: int) -> pd.Series:
    h_diff = h.diff()
    l_diff = -l.diff()
    plus_dm  = np.where((h_diff > l_diff) & (h_diff > 0), h_diff, 0.0)
    minus_dm = np.where((l_diff > h_diff) & (l_diff > 0), l_diff, 0.0)
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    a = 1.0 / max(n, 1)
    atr   = tr.ewm(alpha=a, adjust=False).mean()
    pdi   = 100.0 * pd.Series(plus_dm,  index=h.index).ewm(alpha=a, adjust=False).mean() / atr.replace(0, np.nan)
    mdi   = 100.0 * pd.Series(minus_dm, index=h.index).ewm(alpha=a, adjust=False).mean() / atr.replace(0, np.nan)
    dx    = (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan) * 100.0
    return dx.ewm(alpha=a, adjust=False).mean()


def _add_mtf_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les features MTF 4h et 1d depuis les bars 1h.
    Noms de colonnes identiques aux données d'entraînement :
      mtf_4h_adx_20, mtf_4h_adx_10, mtf_4h_ema_distance_20, mtf_4h_rsi_10,
      mtf_4h_return_5, mtf_4h_donchian_position_20, mtf_4h_realized_vol_10,
      mtf_1d_return_5, mtf_1d_adx_5, mtf_1d_ema_distance_5, mtf_1d_rsi_5,
      mtf_1d_donchian_position_5, mtf_1d_realized_vol_5
    """
    if "datetime" not in df.columns or "close" not in df.columns:
        return df

    df_t = df.set_index("datetime")
    orig_idx = df_t.index

    timeframe_specs = [
        ("4h", "4h", [
            ("adx_20",               lambda h, l, c: _adx_ewm(h, l, c, 20)),
            ("adx_10",               lambda h, l, c: _adx_ewm(h, l, c, 10)),
            ("ema_distance_20",      lambda h, l, c: (c - c.ewm(span=20, adjust=False).mean())
                                                     / c.ewm(span=20, adjust=False).mean().replace(0, np.nan)),
            ("rsi_10",               lambda h, l, c: _rsi_ewm(c, 10)),
            ("return_5",             lambda h, l, c: np.log(c / c.shift(5).replace(0, np.nan))),
            ("donchian_position_20", lambda h, l, c: (c - l.rolling(20, min_periods=1).min())
                                                     / (h.rolling(20, min_periods=1).max()
                                                        - l.rolling(20, min_periods=1).min()).replace(0, np.nan)),
            ("realized_vol_10",      lambda h, l, c: np.log(c / c.shift(1).replace(0, np.nan)).rolling(10, min_periods=2).std()),
        ]),
        ("1d", "1D", [
            ("return_5",             lambda h, l, c: np.log(c / c.shift(5).replace(0, np.nan))),
            ("adx_5",                lambda h, l, c: _adx_ewm(h, l, c, 5)),
            ("ema_distance_5",       lambda h, l, c: (c - c.ewm(span=5, adjust=False).mean())
                                                     / c.ewm(span=5, adjust=False).mean().replace(0, np.nan)),
            ("rsi_5",                lambda h, l, c: _rsi_ewm(c, 5)),
            ("donchian_position_5",  lambda h, l, c: (c - l.rolling(5, min_periods=1).min())
                                                     / (h.rolling(5, min_periods=1).max()
                                                        - l.rolling(5, min_periods=1).min()).replace(0, np.nan)),
            ("realized_vol_5",       lambda h, l, c: np.log(c / c.shift(1).replace(0, np.nan)).rolling(5, min_periods=2).std()),
        ]),
    ]

    for tf_name, rule, specs in timeframe_specs:
        try:
            ohlcv = df_t[["open", "high", "low", "close"]].copy()
            htf = ohlcv.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            htf = htf.dropna(subset=["close"])
            if htf.empty:
                continue
            hh = htf["high"].astype(float)
            hl = htf["low"].astype(float)
            hc = htf["close"].astype(float)

            for feat_name, compute_fn in specs:
                col = f"mtf_{tf_name}_{feat_name}"
                try:
                    vals = compute_fn(hh, hl, hc)
                    # Shift 1: use only completed candles (no lookahead)
                    vals = vals.shift(1)
                    # Forward-fill to 1h resolution
                    aligned = vals.reindex(orig_idx, method="ffill")
                    df[col] = aligned.values
                except Exception:
                    pass
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
        include_multi_timeframe=True,   # v2: activé (était False = bug)
        include_sequence_features=False,
    )
    df_enriched = df_enriched.reset_index()
    # compute_enriched_ohlcv_features nomme l'index "timestamp" — normaliser en "datetime"
    if "datetime" not in df_enriched.columns:
        for cand in ("timestamp", "index", "level_0"):
            if cand in df_enriched.columns:
                df_enriched = df_enriched.rename(columns={cand: "datetime"})
                break
    df_enriched["datetime"] = pd.to_datetime(df_enriched["datetime"], utc=True)

    df_enriched = _apply_feature_aliases(df_enriched)
    df_enriched = _add_mtf_features(df_enriched)

    # ── v2 : Taker flow z-scores ─────────────────────────────────────────────
    # Récupérer taker_buy_base_asset_volume depuis df_combined si absent
    if "taker_buy_base_asset_volume" not in df_enriched.columns and \
       "taker_buy_base_asset_volume" in df_combined.columns:
        tb = df_combined.set_index("datetime")["taker_buy_base_asset_volume"]
        df_enriched = df_enriched.set_index("datetime")
        df_enriched["taker_buy_base_asset_volume"] = tb.reindex(
            df_enriched.index, method="nearest"
        ).values
        df_enriched = df_enriched.reset_index()
    df_enriched = _add_taker_flow_features(df_enriched)

    # ── v2 : Funding rate depuis Binance Futures ──────────────────────────────
    try:
        df_funding = fetch_funding_rate(symbol, last_ms - 3 * 86_400_000)  # 3j back
        df_enriched = _add_funding_features(df_enriched, df_funding)
    except Exception:
        for col in ("funding_rate", "funding_rate_z_24", "funding_rate_z_72"):
            if col not in df_enriched.columns:
                df_enriched[col] = 0.0

    # ── v3 : CVD + OI delta + basis (microstructure alpha) ───────────────────
    # Passer df_combined pour accès à oi_sum et taker_buy_base si absent
    if "oi_sum" not in df_enriched.columns and "oi_sum" in df_combined.columns:
        oi = df_combined.set_index("datetime")["oi_sum"]
        df_enriched = df_enriched.set_index("datetime")
        df_enriched["oi_sum"] = oi.reindex(df_enriched.index, method="ffill").values
        df_enriched = df_enriched.reset_index()

    df_enriched = _add_cvd_features(df_enriched)
    df_enriched = _add_oi_features(df_enriched)
    df_enriched = _add_basis_features(df_enriched)

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

    # ── 7. Append ATOMIQUE au parquet (lock + temp + os.replace + fsync) ───────
    # Remplace l'ancien `to_parquet(path)` direct, cause de corruption en
    # concurrence avec le scheduler. cf. src/institutional/data/atomic_parquet.py
    from src.institutional.data.atomic_parquet import append_enriched_atomic
    df_only_new["datetime"] = pd.to_datetime(df_only_new["datetime"], utc=True)
    n_total = append_enriched_atomic(
        path, df_only_new, timestamp_col="datetime", dedupe_cols=("datetime",))

    print(f"  {symbol}: +{len(df_only_new)} barres → {path.name} ({n_total:,} total)")
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
