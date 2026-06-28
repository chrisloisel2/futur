"""
src/institutional/features/technical.py
─────────────────────────────────────────────────────────────────────────────
Features techniques — momentum, trend, breakout.

RSI et MACD sont implémentés comme FEATURES uniquement (jamais comme stratégie).
La stratégie centrale repose sur des modèles ML entraînés sur ces features.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd


# ─── EMA / SMA ────────────────────────────────────────────────────────────────

def ema(close: pd.Series, span: int) -> pd.Series:
    """EMA causale (pandas ewm adjust=False)."""
    return close.ewm(span=span, adjust=False).mean()


def ema_distance(close: pd.Series, span: int) -> pd.Series:
    """Distance (fraction) entre le prix et son EMA : (close - EMA) / EMA."""
    e = ema(close, span)
    return (close - e) / (e + 1e-9)


def ema_slope(close: pd.Series, span: int, lag: int = 1) -> pd.Series:
    """Pente normalisée de l'EMA sur `lag` barres."""
    e = ema(close, span)
    return (e - e.shift(lag)) / (e.shift(lag) + 1e-9)


def ma_cross_score(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """Score de croisement MA : (fast - slow) / slow. > 0 = bullish."""
    e_fast = ema(close, fast)
    e_slow = ema(close, slow)
    return (e_fast - e_slow) / (e_slow + 1e-9)


# ─── Momentum ─────────────────────────────────────────────────────────────────

def time_series_momentum(close: pd.Series, lookback: int) -> pd.Series:
    """
    Time-series momentum : log-return sur `lookback` barres.
    Pas de normalisation globale — z-score causal à la demande.
    """
    return np.log(close / close.shift(lookback))


def compute_multi_horizon_momentum(
    close: pd.Series,
    horizons: Optional[List[int]] = None,
) -> pd.DataFrame:
    """Momentum multi-horizons (en barres 1h)."""
    if horizons is None:
        horizons = [4, 8, 12, 24, 48, 72, 168]  # 4h, 8h, 12h, 1d, 2d, 3d, 7d
    out = pd.DataFrame(index=close.index)
    for h in horizons:
        out[f"mom_{h}h"] = time_series_momentum(close, h)
    return out


# ─── Donchian Channel ─────────────────────────────────────────────────────────

def donchian_position(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    window: int = 20,
) -> pd.Series:
    """
    Position dans le canal Donchian [-1, +1].
    +1 = nouveau high sur `window` barres (breakout haussier)
    -1 = nouveau low sur `window` barres
    0 = milieu du canal
    """
    upper = high.rolling(window, min_periods=window // 2).max()
    lower = low.rolling(window, min_periods=window // 2).min()
    band = upper - lower
    pos = 2 * (close - lower) / (band + 1e-9) - 1
    return pos.clip(-1, 1)


def breakout_distance(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    window: int = 20,
) -> pd.Series:
    """
    Distance signée au plus haut/bas récent.
    Positive si au-dessus du high de la fenêtre (breakout haussier).
    """
    upper = high.shift(1).rolling(window, min_periods=window // 2).max()
    lower = low.shift(1).rolling(window, min_periods=window // 2).min()
    # Fraction de dépassement au-dessus du high ou en dessous du low
    above = (close - upper) / (upper + 1e-9)
    below = (lower - close) / (lower + 1e-9)
    # Valeur positive = breakout haussier, négative = breakdown
    return above.where(above > 0, -below.where(below > 0, 0))


# ─── RSI (feature uniquement) ─────────────────────────────────────────────────

def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """
    RSI causal sur `window` barres.
    Utilisé UNIQUEMENT comme feature secondaire — jamais comme signal primaire.
    """
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=window - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=window - 1, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - 100 / (1 + rs)


def rsi_zscore(close: pd.Series, rsi_window: int = 14, zscore_window: int = 120) -> pd.Series:
    """Z-score rolling du RSI (normalise les régimes de marché)."""
    r = rsi(close, rsi_window)
    mu = r.rolling(zscore_window, min_periods=zscore_window // 2).mean()
    sigma = r.rolling(zscore_window, min_periods=zscore_window // 2).std()
    return (r - mu) / (sigma + 1e-9)


# ─── Trend Consistency ────────────────────────────────────────────────────────

def trend_consistency_score(close: pd.Series, window: int = 20) -> pd.Series:
    """
    Fraction des barres dans le sens dominant sur `window` barres.
    [0, 1] : 1 = toutes les barres dans le même sens.
    """
    ret = close.pct_change()
    frac_pos = ret.rolling(window, min_periods=window // 2).apply(
        lambda x: (x > 0).mean(), raw=True
    )
    return (frac_pos - 0.5).abs() * 2


def compute_trend_features(
    df: pd.DataFrame,
    ema_spans: Optional[List[int]] = None,
) -> pd.DataFrame:
    """
    Calcule toutes les features de trend/momentum.

    Spans EMA par défaut : 8, 21, 55, 144 (Fibonacci)
    """
    if ema_spans is None:
        ema_spans = [8, 21, 55, 144]

    close = df["close"]
    out = pd.DataFrame(index=df.index)

    # EMA distances
    for span in ema_spans:
        out[f"ema_dist_{span}h"] = ema_distance(close, span)
        out[f"ema_slope_{span}h"] = ema_slope(close, span)

    # MA cross scores (rapide vs lent)
    for fast, slow in [(8, 21), (21, 55), (55, 144)]:
        out[f"ma_cross_{fast}_{slow}"] = ma_cross_score(close, fast, slow)

    # Momentum multi-horizons
    mom = compute_multi_horizon_momentum(close)
    out = pd.concat([out, mom], axis=1)

    # Donchian et breakout
    if all(c in df.columns for c in ["high", "low"]):
        for w in [20, 55]:
            out[f"donchian_pos_{w}h"] = donchian_position(close, df["high"], df["low"], w)
            out[f"breakout_dist_{w}h"] = breakout_distance(close, df["high"], df["low"], w)

    # RSI (feature secondaire)
    out["rsi_14h"] = rsi(close, 14)
    out["rsi_zscore_14h"] = rsi_zscore(close, 14, 120)

    # Trend consistency
    for w in [12, 24, 72]:
        out[f"trend_cons_{w}h"] = trend_consistency_score(close, w)

    return out
