"""
core/features_1m.py — FEATURE ENGINEERING NATIF 1 MINUTE
=========================================================

Table d'entrée  : DataFrame 1m, colonnes Binance lowercase :
    open, high, low, close, volume,
    taker_buy_base_asset_volume, number_of_trades
    index = DatetimeIndex UTC

Table de sortie : même index + toutes les features (float32).
Zéro leakage    : tout est backward-looking sur [0..t].

Architecture
------------
    compute_features_1m(df)          → ~70 features instantanées sur 1m
    compute_multitf_context(df_1m)   → ~30 features de contexte 5m/15m/1h
    compute_all_features(df_1m)      → combine les deux → ~100 features

Convention anti-leakage multi-TF
---------------------------------
    La barre 5m labellisée à T contient les 1m [T, T+4min].
    Pour la barre 1m à T+5min, la barre 5m COMPLÈTE disponible est celle
    labellisée T. On utilise donc shift(1) après resample avant le reindex.

    resample("5min", closed="left", label="left")
        → barre "10:00" = barres 1m [10:00, 10:04]
    shift(1) sur l'index 5m
        → la barre "10:05" voit le contexte de la barre "10:00" complète
    reindex(df_1m.index, method="ffill")
        → chaque 1m hérite du dernier 5m complété
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List

from core.settings import configure_project_imports

configure_project_imports()


# ─────────────────────────────────────────────────────────────────────────────
# Listes de features exportées
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_1M_BASE: List[str] = [
    # Retours multi-horizons
    "ret_1m", "ret_2m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m", "ret_60m",
    # Volatilité réalisée
    "rv_5m", "rv_15m", "rv_30m", "rv_60m", "rv_120m",
    # Structure de barre
    "body_ratio", "upper_wick_ratio", "lower_wick_ratio", "wick_net", "range_pct",
    # ATR
    "atr_14m_pct",
    # EMA distances (sur 1m)
    "dist_ema_20m", "dist_ema_50m", "dist_ema_200m",
    "ema_spread_20_50m", "ema_spread_50_200m",
    # EMA slope
    "ema_slope_20m", "ema_slope_50m",
    # Momentum
    "mom_5m", "mom_10m", "mom_15m", "mom_30m", "mom_60m",
    # Momentum Sharpe
    "sharpe_15m", "sharpe_30m", "sharpe_60m",
    # RSI
    "rsi_14m",
    # Distance au high/low rolling
    "dist_high_15m", "dist_high_30m", "dist_high_60m", "dist_high_240m",
    "dist_low_15m",  "dist_low_30m",  "dist_low_60m",  "dist_low_240m",
    # Breakout / breakdown
    "breakout_up_15m", "breakout_up_30m", "breakout_up_60m",
    "breakout_dn_15m", "breakout_dn_30m", "breakout_dn_60m",
    # Taker flow
    "taker_buy_ratio", "taker_delta",
    "taker_cumul_5m", "taker_cumul_15m", "taker_cumul_30m",
    # Volume
    "vol_z_60m", "vol_z_240m",
    # Persistance directionnelle
    "bull_frac_5m", "bull_frac_10m", "bull_frac_15m",
    # Vitesse
    "speed_3m", "speed_5m", "speed_10m",
    # Compression
    "compression_ratio",
    # Efficience
    "eff_ratio_10m", "eff_ratio_30m",
    # VWAP approx
    "dist_vwap_60m", "dist_vwap_30m",
    # Temporel
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    # Reversal density
    "reversal_density_10m", "reversal_density_30m",
    # Ratio vol court/long
    "rv_ratio_5_30m", "rv_ratio_15_60m",
]

FEATURES_CONTEXT_5M: List[str] = [
    "ctx5_ret", "ctx5_rv", "ctx5_body_ratio", "ctx5_wick_net",
    "ctx5_vol_ratio", "ctx5_taker_delta", "ctx5_eff_ratio",
]

FEATURES_CONTEXT_15M: List[str] = [
    "ctx15_ret", "ctx15_rv", "ctx15_body_ratio", "ctx15_wick_net",
    "ctx15_vol_ratio", "ctx15_taker_delta", "ctx15_mom",
    "ctx15_rsi", "ctx15_dist_ema_20",
]

FEATURES_CONTEXT_1H: List[str] = [
    "ctx1h_ret", "ctx1h_rv", "ctx1h_body_ratio", "ctx1h_wick_net",
    "ctx1h_vol_ratio", "ctx1h_taker_delta", "ctx1h_mom",
    "ctx1h_rsi", "ctx1h_dist_ema_20", "ctx1h_dist_ema_50",
    "ctx1h_dist_ema_200", "ctx1h_boll_pos", "ctx1h_atr_pct",
]

FEATURES_ALL_1M: List[str] = list(dict.fromkeys(
    FEATURES_1M_BASE + FEATURES_CONTEXT_5M + FEATURES_CONTEXT_15M + FEATURES_CONTEXT_1H
))

# Features utilisées par le modèle LONG 1m
FEATURES_LONG_1M: List[str] = [
    # Momentum directionnel
    "ret_5m", "ret_15m", "ret_30m", "mom_5m", "mom_15m", "mom_30m",
    "sharpe_15m", "sharpe_30m",
    # Persistance
    "bull_frac_5m", "bull_frac_10m", "bull_frac_15m",
    # Vitesse
    "speed_3m", "speed_5m",
    # Structure
    "body_ratio", "lower_wick_ratio", "wick_net", "range_pct",
    "eff_ratio_10m", "eff_ratio_30m",
    # Breakout
    "breakout_up_15m", "breakout_up_30m",
    "dist_high_15m", "dist_high_60m",
    "dist_low_15m",  "dist_low_60m",
    # Flow
    "taker_buy_ratio", "taker_delta", "taker_cumul_5m", "taker_cumul_15m",
    "vol_z_60m",
    # Volatilité / compression
    "rv_5m", "rv_15m", "rv_60m", "compression_ratio",
    "rv_ratio_5_30m", "rv_ratio_15_60m",
    # Oscillateurs
    "rsi_14m", "dist_ema_20m", "dist_ema_50m", "dist_vwap_60m",
    "atr_14m_pct",
    # Contexte
    "ctx5_ret", "ctx5_taker_delta", "ctx5_eff_ratio",
    "ctx15_ret", "ctx15_rv", "ctx15_taker_delta", "ctx15_rsi",
    "ctx1h_ret", "ctx1h_rv", "ctx1h_dist_ema_50", "ctx1h_boll_pos",
    # Macro / sentiment (bundle) — couverture historique confirmée (oct 2019+)
    "funding_rate_z_24",      # funding positif = momentum bullish
    "fear_greed_value_z_24",  # greed = contexte favorable au long
    "news_count_roll_240",    # activité news (proxy d'attention du marché)
    # Temporel
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
]

# Features utilisées par le modèle SHORT 1m
FEATURES_SHORT_1M: List[str] = [
    # Momentum baissier
    "ret_5m", "ret_15m", "ret_30m", "mom_5m", "mom_15m", "mom_30m",
    "sharpe_15m", "sharpe_30m",
    # Persistance baissière
    "bull_frac_5m", "bull_frac_10m", "bull_frac_15m",
    "reversal_density_10m", "reversal_density_30m",
    # Structure (surachat / extension)
    "body_ratio", "upper_wick_ratio", "wick_net",
    "dist_high_15m", "dist_high_60m",
    # Breakdown
    "breakout_dn_15m", "breakout_dn_30m",
    # Flow vendeur
    "taker_delta", "taker_cumul_15m", "taker_cumul_30m",
    "vol_z_60m",
    # Volatilité stress
    "rv_5m", "rv_60m", "rv_ratio_5_30m",
    # Oscillateurs
    "rsi_14m", "dist_ema_20m", "dist_ema_50m", "dist_vwap_60m",
    # Contexte
    "ctx5_ret", "ctx5_wick_net",
    "ctx15_ret", "ctx15_rv", "ctx15_rsi",
    "ctx1h_ret", "ctx1h_dist_ema_50", "ctx1h_boll_pos",
    # Macro / sentiment (bundle) — couverture historique confirmée (oct 2019+)
    "funding_rate_z_24",      # funding trop positif = foule longée → fade
    "fear_greed_value_z_24",  # greed extrême = retournement imminent
    "news_count_roll_240",    # activité news (proxy d'attention du marché)
    # Temporel
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
]


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires internes
# ─────────────────────────────────────────────────────────────────────────────

def _safe_div(a: pd.Series, b: pd.Series, fill: float = 0.0) -> pd.Series:
    return a.div(b.clip(lower=1e-9)).fillna(fill)


def _reversal_density(signs: pd.Series, window: int) -> pd.Series:
    """Nombre de changements de signe dans une fenêtre, normalisé."""
    changes = (signs.diff().abs() > 0).astype(float)
    return changes.rolling(window, min_periods=window // 2).mean()


def _rolling_vwap(close: pd.Series, volume: pd.Series, window: int) -> pd.Series:
    """VWAP approximé sur une fenêtre glissante."""
    pv = (close * volume).rolling(window, min_periods=window // 4).sum()
    v  = volume.rolling(window, min_periods=window // 4).sum().clip(lower=1e-9)
    return pv / v


# ─────────────────────────────────────────────────────────────────────────────
# Calcul des features 1m de base
# ─────────────────────────────────────────────────────────────────────────────

def compute_features_1m(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule toutes les features natives 1m.

    Colonnes requises : open, high, low, close, volume,
                        taker_buy_base_asset_volume, number_of_trades
    """
    df = df.copy()

    open_  = df["open"].astype(np.float64)
    high   = df["high"].astype(np.float64)
    low    = df["low"].astype(np.float64)
    close  = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    taker  = df["taker_buy_base_asset_volume"].astype(np.float64)
    trades = df["number_of_trades"].astype(np.float64)

    log_close = np.log(close.clip(lower=1e-9))
    log_ret   = log_close.diff()

    # ── Retours multi-horizons ────────────────────────────────────────────────
    for h in [1, 2, 3, 5, 10, 15, 30, 60]:
        df[f"ret_{h}m"] = log_close - log_close.shift(h)

    # ── Volatilité réalisée ───────────────────────────────────────────────────
    for w in [5, 15, 30, 60, 120]:
        df[f"rv_{w}m"] = log_ret.rolling(w, min_periods=w // 3).std()

    df["rv_ratio_5_30m"]  = _safe_div(df["rv_5m"],  df["rv_30m"])
    df["rv_ratio_15_60m"] = _safe_div(df["rv_15m"], df["rv_60m"])

    # ── Structure de barre ────────────────────────────────────────────────────
    bar_range  = (high - low).clip(lower=1e-9)
    body       = (close - open_).abs()
    upper_wick = high - np.maximum(open_, close)
    lower_wick = np.minimum(open_, close) - low

    df["body_ratio"]       = _safe_div(body,       bar_range)
    df["upper_wick_ratio"] = _safe_div(upper_wick, bar_range)
    df["lower_wick_ratio"] = _safe_div(lower_wick, bar_range)
    df["wick_net"]         = _safe_div(lower_wick - upper_wick, bar_range)  # >0 = bullish
    df["range_pct"]        = _safe_div(bar_range, close)

    # ── ATR 14 ───────────────────────────────────────────────────────────────
    hl  = high - low
    hpc = (high - close.shift(1)).abs()
    lpc = (low  - close.shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    df["atr_14m_pct"] = _safe_div(atr, close)

    # ── EMA distances ─────────────────────────────────────────────────────────
    ema20  = close.ewm(span=20,  adjust=False).mean()
    ema50  = close.ewm(span=50,  adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    df["dist_ema_20m"]       = _safe_div(close - ema20,  ema20)
    df["dist_ema_50m"]       = _safe_div(close - ema50,  ema50)
    df["dist_ema_200m"]      = _safe_div(close - ema200, ema200)
    df["ema_spread_20_50m"]  = _safe_div(ema20 - ema50,  ema50)
    df["ema_spread_50_200m"] = _safe_div(ema50 - ema200, ema200)

    # ── EMA slope (variation sur 5 barres) ────────────────────────────────────
    df["ema_slope_20m"] = _safe_div(ema20 - ema20.shift(5), ema20.shift(5).clip(lower=1e-9) * 5)
    df["ema_slope_50m"] = _safe_div(ema50 - ema50.shift(5), ema50.shift(5).clip(lower=1e-9) * 5)

    # ── Momentum cumulatif ────────────────────────────────────────────────────
    for w in [5, 10, 15, 30, 60]:
        df[f"mom_{w}m"] = log_close - log_close.shift(w)

    # ── Momentum Sharpe ───────────────────────────────────────────────────────
    for w in [15, 30, 60]:
        rm = log_ret.rolling(w, min_periods=w // 3).mean()
        rs = log_ret.rolling(w, min_periods=w // 3).std().clip(lower=1e-9)
        df[f"sharpe_{w}m"] = rm / rs

    # ── RSI 14 ───────────────────────────────────────────────────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    df["rsi_14m"] = 100.0 - 100.0 / (1.0 + _safe_div(gain, loss))

    # ── Distance au high/low rolling ──────────────────────────────────────────
    for w in [15, 30, 60, 240]:
        roll_high = high.rolling(w, min_periods=w // 3).max()
        roll_low  = low.rolling(w,  min_periods=w // 3).min()
        df[f"dist_high_{w}m"] = _safe_div(close - roll_high, roll_high)   # ≤ 0
        df[f"dist_low_{w}m"]  = _safe_div(close - roll_low,  roll_low)    # ≥ 0

    # ── Breakout / Breakdown ──────────────────────────────────────────────────
    for w in [15, 30, 60]:
        prev_high = high.shift(1).rolling(w, min_periods=w // 2).max()
        prev_low  = low.shift(1).rolling(w,  min_periods=w // 2).min()
        df[f"breakout_up_{w}m"] = (close > prev_high).astype(np.float32)
        df[f"breakout_dn_{w}m"] = (close < prev_low).astype(np.float32)

    # ── Taker flow ────────────────────────────────────────────────────────────
    taker_ratio             = _safe_div(taker, volume)
    df["taker_buy_ratio"]   = taker_ratio
    df["taker_delta"]       = taker_ratio - 0.5
    for w in [5, 15, 30]:
        df[f"taker_cumul_{w}m"] = df["taker_delta"].rolling(w, min_periods=w // 3).sum()

    # ── Volume z-score ────────────────────────────────────────────────────────
    for w in [60, 240]:
        vm = volume.rolling(w, min_periods=w // 3).mean()
        vs = volume.rolling(w, min_periods=w // 3).std().clip(lower=1e-9)
        df[f"vol_z_{w}m"] = (volume - vm) / vs

    # ── Persistance directionnelle ────────────────────────────────────────────
    up_bar = (log_ret > 0).astype(float)
    for w in [5, 10, 15]:
        df[f"bull_frac_{w}m"] = up_bar.rolling(w, min_periods=w // 2).mean()

    # ── Vitesse (abs return moyen) ────────────────────────────────────────────
    abs_ret = log_ret.abs()
    for w in [3, 5, 10]:
        df[f"speed_{w}m"] = abs_ret.rolling(w, min_periods=2).mean()

    # ── Compression : vol court vs long ──────────────────────────────────────
    rv5  = log_ret.rolling(5,  min_periods=3).std().clip(lower=1e-9)
    rv30 = log_ret.rolling(30, min_periods=10).std().clip(lower=1e-9)
    df["compression_ratio"] = rv5 / rv30  # < 1 = comprimé, > 1 = expansion

    # ── Efficiency ratio ──────────────────────────────────────────────────────
    for w in [10, 30]:
        net  = (close - close.shift(w)).abs()
        path = close.diff().abs().rolling(w, min_periods=w // 3).sum().clip(lower=1e-9)
        df[f"eff_ratio_{w}m"] = net / path

    # ── VWAP approximé ────────────────────────────────────────────────────────
    vwap_30 = _rolling_vwap(close, volume, 30)
    vwap_60 = _rolling_vwap(close, volume, 60)
    df["dist_vwap_30m"] = _safe_div(close - vwap_30, vwap_30)
    df["dist_vwap_60m"] = _safe_div(close - vwap_60, vwap_60)

    # ── Densité de retournement ───────────────────────────────────────────────
    sign_ret = np.sign(log_ret)
    df["reversal_density_10m"] = _reversal_density(sign_ret, 10)
    df["reversal_density_30m"] = _reversal_density(sign_ret, 30)

    # ── Encodage temporel ─────────────────────────────────────────────────────
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

    # ── Cast float32 pour économiser la RAM ───────────────────────────────────
    feat_cols = [c for c in FEATURES_1M_BASE if c in df.columns]
    df[feat_cols] = df[feat_cols].astype(np.float32)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Contexte multi-timeframe — injecté sur l'index 1m
# ─────────────────────────────────────────────────────────────────────────────

def _agg_to_tf(df_1m: pd.DataFrame, freq: str) -> pd.DataFrame:
    """
    Rééchantillonne df_1m vers la fréquence freq.
    closed="left", label="left" : la barre labellisée T contient [T, T+freq).
    shift(1) : à l'index 1m T+freq, on voit la barre [T, T+freq-1] complète.
    """
    agg = df_1m.resample(freq, closed="left", label="left").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
        "taker_buy_base_asset_volume": "sum",
        "number_of_trades": "sum",
    }).dropna(subset=["close"])
    return agg


def _context_features(df_agg: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """
    Calcule un sous-ensemble de features sur un DataFrame agrégé (5m/15m/1h).
    Retourne un DataFrame avec colonnes nommées {prefix}_{feat}.
    """
    out = pd.DataFrame(index=df_agg.index)

    close  = df_agg["close"].astype(np.float64)
    high   = df_agg["high"].astype(np.float64)
    low    = df_agg["low"].astype(np.float64)
    open_  = df_agg["open"].astype(np.float64)
    volume = df_agg["volume"].astype(np.float64)
    taker  = df_agg["taker_buy_base_asset_volume"].astype(np.float64)

    log_close = np.log(close.clip(lower=1e-9))
    log_ret   = log_close.diff()

    bar_range  = (high - low).clip(lower=1e-9)
    body       = (close - open_).abs()
    upper_wick = high - np.maximum(open_, close)
    lower_wick = np.minimum(open_, close) - low

    out[f"{prefix}_ret"]        = log_ret
    out[f"{prefix}_rv"]         = log_ret.rolling(12, min_periods=4).std()
    out[f"{prefix}_body_ratio"] = _safe_div(body, bar_range)
    out[f"{prefix}_wick_net"]   = _safe_div(lower_wick - upper_wick, bar_range)
    out[f"{prefix}_vol_ratio"]  = _safe_div(volume, volume.rolling(24, min_periods=6).mean())
    out[f"{prefix}_taker_delta"] = _safe_div(taker, volume) - 0.5

    # Efficiency ratio
    net  = (close - close.shift(10)).abs()
    path = close.diff().abs().rolling(10, min_periods=3).sum().clip(lower=1e-9)
    out[f"{prefix}_eff_ratio"] = net / path

    if prefix in ("ctx15", "ctx1h"):
        # Momentum cumulatif
        out[f"{prefix}_mom"] = log_close - log_close.shift(12)
        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        out[f"{prefix}_rsi"] = 100.0 - 100.0 / (1.0 + _safe_div(gain, loss))
        # EMA
        ema20 = close.ewm(span=20, adjust=False).mean()
        out[f"{prefix}_dist_ema_20"] = _safe_div(close - ema20, ema20)

    if prefix == "ctx1h":
        ema50  = close.ewm(span=50,  adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()
        out[f"{prefix}_dist_ema_50"]  = _safe_div(close - ema50,  ema50)
        out[f"{prefix}_dist_ema_200"] = _safe_div(close - ema200, ema200)
        # Bollinger position
        ma20  = close.rolling(20, min_periods=8).mean()
        std20 = close.rolling(20, min_periods=8).std().clip(lower=1e-9)
        bw    = (4 * std20).clip(lower=1e-9)
        out[f"{prefix}_boll_pos"] = ((close - (ma20 - 2 * std20)) / bw) * 2 - 1
        # ATR
        hl  = high - low
        hpc = (high - close.shift(1)).abs()
        lpc = (low  - close.shift(1)).abs()
        tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
        atr = tr.ewm(span=14, adjust=False).mean()
        out[f"{prefix}_atr_pct"] = _safe_div(atr, close)

    out = out.astype(np.float32)
    return out


def compute_multitf_context(df_1m: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule le contexte 5m, 15m et 1h et le réindexe sur l'index 1m.

    Anti-leakage :
        shift(1) sur chaque timeframe agrégé avant le reindex ffill.
        → à chaque barre 1m, on voit uniquement les barres agrégées COMPLÈTES.
    """
    results = []

    for freq, prefix in [("5min", "ctx5"), ("15min", "ctx15"), ("1h", "ctx1h")]:
        agg   = _agg_to_tf(df_1m, freq)
        feats = _context_features(agg, prefix)
        # shift(1) : la barre T n'est visible qu'à partir de T+1
        feats_shifted = feats.shift(1)
        # Reindex sur l'index 1m avec ffill — chaque 1m hérite du dernier agrégat disponible
        feats_on_1m = feats_shifted.reindex(df_1m.index, method="ffill")
        results.append(feats_on_1m)

    return pd.concat(results, axis=1).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

def compute_all_features(df_1m: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule toutes les features (~100+macro) et les joint au DataFrame 1m.

    Entrée  : DataFrame 1m brut Binance + éventuelles colonnes macro du bundle
    Sortie  : DataFrame 1m enrichi, même index

    Ordre d'appel :
        1. compute_features_1m      → features instantanées price-action
        2. compute_multitf_context  → contexte agrégé 5m/15m/1h
        3. pass-through colonnes macro (si bundle)
        4. ffill + fillna(0)
    """
    from ai.level_0.live_features import MACRO_BUNDLE_COLS

    # Mémoriser les colonnes macro avant le calcul (elles peuvent être écrasées)
    _macro_present = [c for c in MACRO_BUNDLE_COLS if c in df_1m.columns]

    print("   compute_features_1m …")
    df = compute_features_1m(df_1m)

    print("   compute_multitf_context (5m / 15m / 1h) …")
    ctx = compute_multitf_context(df_1m)
    df  = df.join(ctx, how="left")

    # Pass-through colonnes macro du bundle (déjà ffill à la lecture)
    if _macro_present:
        df[_macro_present] = df_1m[_macro_present]
        df[_macro_present] = df[_macro_present].ffill().fillna(0.0)

    # Remplissage résiduel (début de série)
    feat_cols = [c for c in FEATURES_ALL_1M if c in df.columns]
    df[feat_cols] = df[feat_cols].ffill().fillna(0.0).astype(np.float32)

    missing = [c for c in FEATURES_ALL_1M if c not in df.columns]
    if missing:
        print(f"   ⚠  Features manquantes (non-bundle) : {missing}")

    macro_loaded = len(_macro_present)
    print(f"   {len(feat_cols)} features price-action + {macro_loaded} macro sur {len(df):,} barres 1m")
    return df
