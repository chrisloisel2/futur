"""
ai/level_0/live_features.py — CALCUL DES FEATURES DEPUIS OHLCV BINANCE
=======================================================================

Prend un DataFrame Binance brut (open/high/low/close/volume +
taker_buy_base_asset_volume + number_of_trades) et produit toutes
les features de base attendues par FEATURES_COMMON + les pré-requis
de FEATURES_LONG_EXTRA / FEATURES_SHORT_EXTRA.

Après cet appel, enchaîner compute_long_features() et
compute_short_features() pour compléter les features asymétriques.

Colonnes Binance attendues :
  open, high, low, close, volume
  taker_buy_base_asset_volume
  number_of_trades
  (index = DatetimeIndex UTC)
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd


# Colonnes macro présentes dans features_merged.parquet avec couverture historique réelle.
#
# Couverture vérifiée (après ffill) :
#   funding_rate_z_*    : depuis oct 2019  (~24% NaN) → train 2020-2022 ✓ val 2023 ✓ test 2024 ✓
#   fear_greed_value_z_*: depuis fév 2018  (~5%  NaN) → couverture complète ✓
#   news_count_roll_*   : depuis août 2017 (0%   NaN) → couverture complète ✓
#
# Exclues (données démarrent seulement en mars 2026, inutilisables pour le training) :
#   oihist_sumOpenInterest_z_*, global_ls_longShortRatio_z_*,
#   taker_ls_buySellRatio_z_*, taker_ls_imbalance, funding_x_global_ls, oi_x_fng
MACRO_BUNDLE_COLS: List[str] = [
    "funding_rate_z_24",      # sentiment positionnement court terme (8h update)
    "funding_rate_z_72",      # structure moyen terme
    "funding_rate_z_288",     # tendance structurelle 12j
    "fear_greed_value_z_24",  # sentiment F&G court terme (daily update)
    "fear_greed_value_z_72",  # sentiment F&G moyen terme
    "news_count_roll_240",    # volume news 4h rolling
    "news_count_roll_1440",   # volume news 24h rolling
]


def compute_macro_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Finalise les features macro issues du bundle (funding, OI, L/S, F&G, news).
    Forward-fill uniquement — les valeurs sont déjà calculées par le bundle.
    Compatible live (sans bundle) : no-op si les colonnes sont absentes.
    """
    present = [c for c in MACRO_BUNDLE_COLS if c in df.columns]
    if not present:
        return df
    df = df.copy()
    df[present] = df[present].ffill().fillna(0.0)
    return df


def compute_live_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule toutes les features de base depuis les colonnes OHLCV Binance.

    Retourne un nouveau DataFrame avec les colonnes originales + les features.
    Les NaN en début de série sont normaux (fenêtres d'initialisation).
    Ils sont remplis par ffill puis 0.0 à la fin.
    """
    df = df.copy()
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    # ── Log-returns ──────────────────────────────────────────────────────────
    log_ret = np.log(close / close.shift(1))

    # ── Realized volatility (rolling std of log-returns) ─────────────────────
    for w in [12, 24, 48, 72, 168]:
        df[f"rv_{w}"] = log_ret.rolling(w, min_periods=max(2, w // 4)).std()

    df["rv_ratio_24_72"] = df["rv_24"] / df["rv_72"].clip(lower=1e-9)
    df["rv_ratio_12_48"] = df["rv_12"] / df["rv_48"].clip(lower=1e-9)

    # ── ATR-14 (EWM) ──────────────────────────────────────────────────────────
    hl  = high - low
    hpc = (high - close.shift(1)).abs()
    lpc = (low  - close.shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    df["atr_pct_14"] = atr / close.clip(lower=1e-9)

    # ── Bollinger bands (20, 2σ) ──────────────────────────────────────────────
    ma20  = close.rolling(20, min_periods=10).mean()
    std20 = close.rolling(20, min_periods=10).std()
    upper = ma20 + 2 * std20
    lower_b = ma20 - 2 * std20
    bw    = (upper - lower_b).clip(lower=1e-9)
    df["boll_width_20"] = bw / ma20.clip(lower=1e-9)
    df["boll_pos_20"]   = ((close - lower_b) / bw) * 2 - 1   # [-1, +1]

    # ── Bar structure ─────────────────────────────────────────────────────────
    bar_range = (high - low).clip(lower=1e-9)
    df["close_in_bar"]       = (close - low) / bar_range
    df["intrabar_range_pct"] = bar_range / close.clip(lower=1e-9)

    # ── Efficiency ratio (Kaufman) ────────────────────────────────────────────
    for w in [12, 24]:
        net   = (close - close.shift(w)).abs()
        path  = close.diff().abs().rolling(w, min_periods=1).sum().clip(lower=1e-9)
        df[f"eff_ratio_{w}"] = net / path

    # ── Taker flow (Binance) ──────────────────────────────────────────────────
    taker_buy = df["taker_buy_base_asset_volume"]
    df["taker_buy_ratio_base"]  = taker_buy / vol.clip(lower=1e-9)
    df["delta_taker_pressure"]  = df["taker_buy_ratio_base"] - 0.5

    # ── Volume / trades ratios ─────────────────────────────────────────────────
    vol_ma24 = vol.rolling(24, min_periods=6).mean().clip(lower=1e-9)
    df["vol_ratio_24"] = vol / vol_ma24

    trades = df["number_of_trades"].astype(float)
    tr_ma24 = trades.rolling(24, min_periods=6).mean().clip(lower=1e-9)
    df["trades_ratio_24"] = trades / tr_ma24

    # ── Z-scores ──────────────────────────────────────────────────────────────
    c_mean24 = close.rolling(24, min_periods=6).mean()
    c_std24  = close.rolling(24, min_periods=6).std().clip(lower=1e-9)
    df["zscore_close_24"] = (close - c_mean24) / c_std24

    r_mean24 = log_ret.rolling(24, min_periods=6).mean()
    r_std24  = log_ret.rolling(24, min_periods=6).std().clip(lower=1e-9)
    df["zscore_ret_24"] = (log_ret - r_mean24) / r_std24

    # ── Time encoding (cyclique) ──────────────────────────────────────────────
    if hasattr(df.index, "hour"):
        hour = df.index.hour.astype(float)
        dow  = df.index.dayofweek.astype(float)
    else:
        ts   = pd.to_datetime(df.index)
        hour = ts.hour.astype(float)
        dow  = ts.dayofweek.astype(float)

    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["dow_sin"]  = np.sin(2 * np.pi * dow  / 7)
    df["dow_cos"]  = np.cos(2 * np.pi * dow  / 7)

    # ── Momentum log-returns ──────────────────────────────────────────────────
    for w in [6, 12, 24, 72]:
        df[f"mom_logret_{w}"] = log_ret.rolling(w, min_periods=max(2, w // 4)).sum()

    # ── Momentum Sharpe (mean/std de log-returns sur fenêtre) ─────────────────
    for w in [6, 12, 24]:
        r_m = log_ret.rolling(w, min_periods=max(2, w // 4)).mean()
        r_s = log_ret.rolling(w, min_periods=max(2, w // 4)).std().clip(lower=1e-9)
        df[f"mom_sharpe_{w}"] = r_m / r_s

    # ── EMA distances ─────────────────────────────────────────────────────────
    ema20  = close.ewm(span=20,  adjust=False).mean()
    ema50  = close.ewm(span=50,  adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    df["dist_ema_20"]  = (close - ema20)  / ema20.clip(lower=1e-9)
    df["dist_ema_50"]  = (close - ema50)  / ema50.clip(lower=1e-9)
    df["dist_ema_200"] = (close - ema200) / ema200.clip(lower=1e-9)
    df["ema_spread_20_50"]  = (ema20  - ema50)  / ema50.clip(lower=1e-9)
    df["ema_spread_50_200"] = (ema50  - ema200) / ema200.clip(lower=1e-9)

    # ── RSI-14 (Wilder EWM) ───────────────────────────────────────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs    = gain / loss.clip(lower=1e-9)
    df["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

    # ── CCI-20 ────────────────────────────────────────────────────────────────
    tp    = (high + low + close) / 3.0
    tp_ma = tp.rolling(20, min_periods=10).mean()
    mad   = tp.rolling(20, min_periods=10).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    ).clip(lower=1e-9)
    df["cci_20"] = (tp - tp_ma) / (0.015 * mad)

    # ── Skewness rolling ──────────────────────────────────────────────────────
    df["skew_ret_24"] = log_ret.rolling(24, min_periods=8).skew()

    # ── Remplissage NaN ──────────────────────────────────────────────────────
    feat_cols = [c for c in df.columns if c not in
                 ("open", "high", "low", "close", "volume",
                  "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume",
                  "number_of_trades", "quote_asset_volume",
                  "open_time", "close_time", "ignore")]
    df[feat_cols] = df[feat_cols].ffill().fillna(0.0)

    return df
