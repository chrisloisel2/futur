"""
src/institutional/features/volatility.py
─────────────────────────────────────────────────────────────────────────────
Features de volatilité — toutes causales.

Implémente :
  - EWMA
  - Réalisée (parkinson, garman-klass)
  - HAR-RV (Hétérogène AutoRégressif)
  - Vol-of-vol
  - ATR et percentiles
  - Régime de volatilité
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd


ANNUALIZATION_FACTOR = np.sqrt(24 * 365)  # pour variance horaire → annualisée


def _log_ret(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1))


# ─── EWMA ─────────────────────────────────────────────────────────────────────

def ewma_vol(
    close: pd.Series,
    span: int = 60,
    annualize: bool = True,
) -> pd.Series:
    """Volatilité EWMA (RiskMetrics). Causal via .ewm(span)."""
    log_r = _log_ret(close)
    var = log_r.pow(2).ewm(span=span, adjust=False).mean()
    vol = np.sqrt(var)
    return vol * ANNUALIZATION_FACTOR if annualize else vol


# ─── Volatilité réalisée rolling ──────────────────────────────────────────────

def realized_vol(
    close: pd.Series,
    window: int = 24,
    annualize: bool = True,
) -> pd.Series:
    """Volatilité réalisée simple (std des log-returns rolling)."""
    log_r = _log_ret(close)
    rv = log_r.rolling(window, min_periods=window // 2).std()
    return rv * ANNUALIZATION_FACTOR if annualize else rv


def parkinson_vol(
    high: pd.Series,
    low: pd.Series,
    window: int = 24,
    annualize: bool = True,
) -> pd.Series:
    """
    Estimateur Parkinson (range-based).
    Biais faible sur les marchés continus.
    Causal : rolling sur window barres passées.
    """
    hl_log = np.log(high / low)
    pv = np.sqrt(hl_log.pow(2).rolling(window, min_periods=window // 2).mean() / (4 * np.log(2)))
    return pv * ANNUALIZATION_FACTOR if annualize else pv


def garman_klass_vol(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 24,
    annualize: bool = True,
) -> pd.Series:
    """
    Estimateur Garman-Klass (OHLC).
    Efficace sous diffusion log-normale standard.
    """
    hl2 = 0.5 * np.log(high / low).pow(2)
    co2 = (2 * np.log(2) - 1) * np.log(close / open_).pow(2)
    gk_sq = (hl2 - co2).rolling(window, min_periods=window // 2).mean()
    gk_sq = gk_sq.clip(lower=0)
    vol = np.sqrt(gk_sq)
    return vol * ANNUALIZATION_FACTOR if annualize else vol


# ─── HAR-RV ───────────────────────────────────────────────────────────────────

def har_rv_components(
    close: pd.Series,
) -> pd.DataFrame:
    """
    Composantes HAR-RV :
      RV_1h   : variance réalisée sur 1 barre
      RV_1d   : moyenne des RV 24 barres
      RV_1w   : moyenne des RV 168 barres (7j)

    Ces composantes servent de features pour prédire la volatilité future.
    Le modèle HAR est entraîné séparément (models/volatility/har_rv.py).
    """
    log_r = _log_ret(close)
    rv_1bar = log_r.pow(2)

    out = pd.DataFrame(index=close.index)
    out["rv_1h"] = rv_1bar
    out["rv_1d"] = rv_1bar.rolling(24, min_periods=12).mean()
    out["rv_1w"] = rv_1bar.rolling(168, min_periods=84).mean()

    # En annualisé
    for col in ["rv_1h", "rv_1d", "rv_1w"]:
        out[f"{col}_ann"] = np.sqrt(out[col]) * ANNUALIZATION_FACTOR

    return out


# ─── ATR ─────────────────────────────────────────────────────────────────────

def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """Average True Range sur `window` barres (causal)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=window // 2).mean()


def atr_pct(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """ATR normalisé par le prix (en fraction)."""
    return atr(high, low, close, window) / close


# ─── Percentiles et régimes ───────────────────────────────────────────────────

def vol_percentile(vol_series: pd.Series, window: int = 252 * 24) -> pd.Series:
    """
    Percentile de volatilité rolling [0, 100].
    Causal : rang de la valeur courante dans la fenêtre passée.
    """
    return vol_series.rolling(window, min_periods=window // 4).rank(pct=True) * 100


def vol_zscore(vol_series: pd.Series, window: int = 240) -> pd.Series:
    """Z-score rolling de la volatilité (causal)."""
    mu = vol_series.rolling(window, min_periods=window // 2).mean()
    sigma = vol_series.rolling(window, min_periods=window // 2).std()
    return (vol_series - mu) / (sigma + 1e-9)


def vol_of_vol(
    close: pd.Series,
    vol_window: int = 24,
    vov_window: int = 168,
) -> pd.Series:
    """Volatilité de la volatilité (vol-of-vol) — mesure de régime."""
    rv = realized_vol(close, vol_window, annualize=False)
    return rv.rolling(vov_window, min_periods=vov_window // 2).std()


def vol_regime(
    close: pd.Series,
    vol_window: int = 24,
    percentile_window: int = 252 * 24,
) -> pd.Series:
    """
    Régime de volatilité discret :
        0 = LOW (percentile < 33)
        1 = MEDIUM (percentile 33-66)
        2 = HIGH (percentile > 66)
    """
    rv = realized_vol(close, vol_window, annualize=True)
    pct = vol_percentile(rv, percentile_window)
    regime = pd.cut(pct, bins=[0, 33, 66, 100], labels=[0, 1, 2]).astype(float)
    return regime


# ─── Feature set complet ─────────────────────────────────────────────────────

def compute_volatility_features(
    df: pd.DataFrame,
    windows: Optional[List[int]] = None,
) -> pd.DataFrame:
    """
    Calcule toutes les features de volatilité sur un DataFrame OHLCV.

    Retourne un DataFrame avec toutes les features vol (sans modifier df).
    """
    if windows is None:
        windows = [15, 24, 60, 120, 240, 720]  # en barres 1h

    close = df["close"]
    out = pd.DataFrame(index=df.index)

    # EWMA
    for w in [24, 60, 168]:
        out[f"ewma_vol_{w}h"] = ewma_vol(close, span=w)

    # Realized vol multi-fenêtres
    for w in windows:
        out[f"rv_{w}h"] = realized_vol(close, w)
        out[f"rv_zscore_{w}h"] = vol_zscore(out[f"rv_{w}h"], window=max(240, w * 4))
        out[f"rv_pct_{w}h"] = vol_percentile(out[f"rv_{w}h"])

    # Parkinson et Garman-Klass (si OHLC disponibles)
    if all(c in df.columns for c in ["high", "low"]):
        out["parkinson_vol_24h"] = parkinson_vol(df["high"], df["low"], window=24)
        out["parkinson_vol_72h"] = parkinson_vol(df["high"], df["low"], window=72)

    if all(c in df.columns for c in ["open", "high", "low"]):
        out["gk_vol_24h"] = garman_klass_vol(df["open"], df["high"], df["low"], close, 24)

    # HAR components
    har = har_rv_components(close)
    out = pd.concat([out, har], axis=1)

    # ATR
    if all(c in df.columns for c in ["high", "low"]):
        out["atr_14h"] = atr(df["high"], df["low"], close, 14)
        out["atr_pct_14h"] = atr_pct(df["high"], df["low"], close, 14)
        out["atr_pct_rank"] = vol_percentile(out["atr_pct_14h"])

    # Vol-of-vol
    out["vol_of_vol_7d"] = vol_of_vol(close, 24, 168)

    # Regime
    out["vol_regime"] = vol_regime(close)

    # Ratio vol courts/longs (mesure d'expansion)
    if "rv_24h" in out.columns and "rv_168h" in out.columns:
        out["vol_ratio_short_long"] = out["rv_24h"] / (out["rv_168h"] + 1e-9)

    return out
