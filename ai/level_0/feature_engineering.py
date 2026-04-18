"""
level_0/feature_engineering.py — CALCUL DES FEATURES ASYMÉTRIQUES
===================================================================

Ce module calcule les features qui ne sont PAS dans le CSV original
mais qui sont nécessaires pour les modèles long et short.

Organisation :
  compute_long_features(df)   → ajoute toutes les colonnes long dans df
  compute_short_features(df)  → ajoute toutes les colonnes short dans df
  Les fonctions individuelles peuvent être appelées séparément pour le debug.

Conventions :
  - Toutes les features sont calculées sur la fenêtre [0..t] uniquement (pas de leakage).
  - Les NaN en début de série sont normaux et seront droppés dans preprocessing.
  - Aucune valeur forward (t+1, t+2...) ne doit apparaître ici.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def compute_flow_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Order flow features: volume delta, trade intensity, liquidation proxies.

    Colonnes ajoutées :
      volume_delta        — buy_vol - sell_vol (unités absolues si dispo, sinon proxy)
      vol_imbalance       — volume_delta / total_volume ∈ [-1, 1]
      trade_intensity     — trades/volume (retail=élevé, institutionnel=faible)
      liq_long_spike_12   — proxy liquidations longs (sell_spike × vol_spike, rolling 12)
      liq_short_spike_12  — proxy liquidations shorts (buy_spike × vol_spike, rolling 12)
      liq_imbalance       — liq_long - liq_short (positif = plus de longs liquidés = bearish)
    """
    df = df.copy()

    # ── Volume delta et imbalance normalisée ─────────────────────────────────
    if "taker_buy_base_asset_volume" in df.columns and "volume" in df.columns:
        buy_vol = pd.to_numeric(df["taker_buy_base_asset_volume"], errors="coerce")
        total   = pd.to_numeric(df["volume"], errors="coerce").clip(lower=1e-9)
        df["volume_delta"]  = buy_vol - (total - buy_vol)
        df["vol_imbalance"] = df["volume_delta"] / total
    elif "delta_taker_pressure" in df.columns:
        df["volume_delta"]  = df["delta_taker_pressure"]
        df["vol_imbalance"] = df["delta_taker_pressure"]
    else:
        df["volume_delta"]  = np.nan
        df["vol_imbalance"] = np.nan

    # ── Trade intensity : trades/volume (haut = retail fractionné) ───────────
    if "number_of_trades" in df.columns and "volume" in df.columns:
        df["trade_intensity"] = (
            pd.to_numeric(df["number_of_trades"], errors="coerce")
            / pd.to_numeric(df["volume"], errors="coerce").clip(lower=1e-9)
        )
    elif "trades_ratio_24" in df.columns and "vol_ratio_24" in df.columns:
        df["trade_intensity"] = df["trades_ratio_24"] / df["vol_ratio_24"].clip(lower=0.01)
    else:
        df["trade_intensity"] = np.nan

    # ── Liquidation proxies ───────────────────────────────────────────────────
    # Long liq  : sell pressure spike + volume spike (forced longs closing = bearish)
    # Short liq : buy pressure spike + volume spike  (forced shorts closing = bullish)
    if "delta_taker_pressure" in df.columns and "vol_ratio_24" in df.columns:
        vol_spike  = df["vol_ratio_24"].clip(lower=0.0)
        sell_spike = (-df["delta_taker_pressure"]).clip(lower=0.0) * vol_spike
        buy_spike  = df["delta_taker_pressure"].clip(lower=0.0)   * vol_spike
        df["liq_long_spike_12"]  = sell_spike.rolling(12, min_periods=1).sum()
        df["liq_short_spike_12"] = buy_spike.rolling(12, min_periods=1).sum()
        df["liq_imbalance"]      = df["liq_long_spike_12"] - df["liq_short_spike_12"]
    else:
        df["liq_long_spike_12"]  = np.nan
        df["liq_short_spike_12"] = np.nan
        df["liq_imbalance"]      = np.nan

    _new_cols = [
        "volume_delta", "vol_imbalance", "trade_intensity",
        "liq_long_spike_12", "liq_short_spike_12", "liq_imbalance",
    ]
    for col in _new_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    return df


def compute_long_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les features asymétriques spécifiques au LONG.

    Colonnes ajoutées (voir FEATURES_LONG_EXTRA dans level_0/features.py) :
      dist_from_local_low_24     — distance du creux 24h (breakout indicator)
      dist_from_local_low_168    — distance du creux hebdo
      breakout_strength_24       — position dans le range 24h [0=bas, 1=haut]
      trend_persistence_12       — % de barres à rendement positif sur 12h
      ret_pos_autocorr_12        — autocorrélation lag-1 des returns (trend signal)
      upside_vol_ratio_24        — std(pos)/std(neg) sur 24 barres (>1 = bull pressure)
      taker_buy_cumul_12         — accumulation acheteuse nette sur 12 barres
      buy_vol_ratio_6            — ratio pression acheteuse sur 6 barres
      momentum_accel_6           — accélération du momentum (différence)
      boll_expansion_6           — expansion bandes Bollinger (compression→expansion)
    """
    df = df.copy()

    if "Close" in df.columns:
        close = df["Close"]
    elif "close" in df.columns:
        close = df["close"]
    else:
        close = None

    if close is not None:
        rolling_low_24  = close.rolling(24,  min_periods=1).min()
        rolling_low_168 = close.rolling(168, min_periods=1).min()
        df["dist_from_local_low_24"]  = (close - rolling_low_24)  / rolling_low_24.clip(lower=1e-9)
        df["dist_from_local_low_168"] = (close - rolling_low_168) / rolling_low_168.clip(lower=1e-9)

        rolling_high_24 = close.rolling(24, min_periods=1).max()
        bar_range       = (rolling_high_24 - rolling_low_24).clip(lower=1e-9)
        df["breakout_strength_24"] = (close - rolling_low_24) / bar_range
    else:
        df["dist_from_local_low_24"]  = np.nan
        df["dist_from_local_low_168"] = np.nan
        df["breakout_strength_24"]    = np.nan

    # Returns proxy — utiliser UNIQUEMENT les returns passés (pas future_ret_h = leakage)
    if "mom_logret_6" in df.columns:
        ret = df["mom_logret_6"] / 6.0
    elif "future_ret_h" in df.columns:
        ret = df["future_ret_h"].shift(1)
    else:
        ret = None

    if ret is not None:
        df["trend_persistence_12"] = (ret > 0).astype(float).rolling(12, min_periods=6).mean()
    else:
        df["trend_persistence_12"] = np.nan

    if ret is not None:
        df["ret_pos_autocorr_12"] = _rolling_lag1_autocorr(ret.values, window=12)
    else:
        df["ret_pos_autocorr_12"] = np.nan

    if ret is not None:
        df["upside_vol_ratio_24"] = _rolling_upside_vol_ratio(ret.values, window=24)
    else:
        df["upside_vol_ratio_24"] = np.nan

    if "delta_taker_pressure" in df.columns:
        df["taker_buy_cumul_12"] = (
            df["delta_taker_pressure"].rolling(12, min_periods=6).sum()
        )
    else:
        df["taker_buy_cumul_12"] = np.nan

    if "taker_buy_ratio_base" in df.columns:
        df["buy_vol_ratio_6"] = df["taker_buy_ratio_base"].rolling(6, min_periods=3).mean()
    else:
        df["buy_vol_ratio_6"] = np.nan

    if "mom_logret_6" in df.columns:
        df["momentum_accel_6"] = df["mom_logret_6"] - df["mom_logret_6"].shift(3)
    else:
        df["momentum_accel_6"] = np.nan

    if "boll_width_20" in df.columns:
        bw = df["boll_width_20"]
        df["boll_expansion_6"] = bw / bw.rolling(6, min_periods=3).mean().clip(lower=1e-9)
    else:
        df["boll_expansion_6"] = np.nan

    _new_cols_long = [
        "dist_from_local_low_24", "dist_from_local_low_168", "breakout_strength_24",
        "trend_persistence_12", "ret_pos_autocorr_12", "upside_vol_ratio_24",
        "taker_buy_cumul_12", "buy_vol_ratio_6", "momentum_accel_6", "boll_expansion_6",
    ]
    for col in _new_cols_long:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    return df


def compute_short_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule et ajoute toutes les features short asymétriques dans df.

    Appeler après le chargement du CSV, avant le feature validation.
    Idempotent : si une colonne existe déjà, elle est recalculée.

    Colonnes ajoutées (voir FEATURES_SHORT_EXTRA dans level_0/features.py) :
      delta_taker_cumul_12    — accumulation vendeuse sur 12 barres
      sell_vol_ratio_6        — ratio volume vendeur sur 6 barres
      sell_vol_ratio_24       — ratio volume vendeur sur 24 barres
      price_vol_divergence_12 — corrélation prix/volume inversée
      rsi_14_above_70_bars    — barres consécutives RSI > 70
      dist_from_local_high_24 — distance du sommet 24h
      dist_from_local_high_168 — distance du sommet 168h (semaine)
      ret_neg_autocorr_12     — autocorrélation négative des returns
      skew_ret_12             — skewness des returns 12 barres
      downside_vol_ratio_24   — ratio vol baissière/haussière
      max_drawdown_12         — MDD sur 12 barres
    """
    df = df.copy()

    # ── Groupe A : mécanique de la vente ─────────────────────────────────────

    if "delta_taker_pressure" in df.columns:
        df["delta_taker_cumul_12"] = (
            df["delta_taker_pressure"].rolling(12, min_periods=6).sum()
        )
    else:
        df["delta_taker_cumul_12"] = np.nan

    if "taker_buy_ratio_base" in df.columns and "vol_ratio_24" in df.columns:
        sell_pressure = 1.0 - df["taker_buy_ratio_base"]
        df["sell_vol_ratio_6"]  = sell_pressure.rolling(6,  min_periods=3).mean()
        df["sell_vol_ratio_24"] = sell_pressure.rolling(24, min_periods=12).mean()
    else:
        df["sell_vol_ratio_6"]  = np.nan
        df["sell_vol_ratio_24"] = np.nan

    if "vol_ratio_24" in df.columns and "mom_logret_12" in df.columns:
        ret_roll  = df["mom_logret_12"]
        vol_roll  = df["vol_ratio_24"]
        df["price_vol_divergence_12"] = _rolling_correlation(ret_roll, vol_roll, window=12)
    else:
        df["price_vol_divergence_12"] = np.nan

    # ── Groupe B : structure de retournement ─────────────────────────────────

    if "rsi_14" in df.columns:
        rsi_overbought = (df["rsi_14"] > 70).astype(int)
        df["rsi_14_above_70_bars"] = _consecutive_count(rsi_overbought.values)
    else:
        df["rsi_14_above_70_bars"] = np.nan

    if "Close" in df.columns:
        close = df["Close"]
    elif "close" in df.columns:
        close = df["close"]
    else:
        close = None

    if close is not None:
        rolling_high_24  = close.rolling(24,  min_periods=1).max()
        rolling_high_168 = close.rolling(168, min_periods=1).max()
        df["dist_from_local_high_24"]  = (close - rolling_high_24)  / rolling_high_24.clip(lower=1e-9)
        df["dist_from_local_high_168"] = (close - rolling_high_168) / rolling_high_168.clip(lower=1e-9)
    else:
        df["dist_from_local_high_24"]  = np.nan
        df["dist_from_local_high_168"] = np.nan

    # IMPORTANT : utiliser close.pct_change() = return[t-1→t] = connu à t, aucun leakage.
    if close is not None:
        ret = close.pct_change()
    elif "mom_logret_6" in df.columns:
        ret = df["mom_logret_6"] / 6.0
    else:
        ret = None

    if ret is not None:
        df["ret_neg_autocorr_12"] = _rolling_lag1_autocorr(ret.values, window=12)
    else:
        df["ret_neg_autocorr_12"] = np.nan

    # ── Groupe C : skew et volatilité asymétrique ─────────────────────────────

    if ret is not None:
        df["skew_ret_12"] = ret.rolling(12, min_periods=6).skew()
    else:
        df["skew_ret_12"] = np.nan

    if ret is not None:
        df["downside_vol_ratio_24"] = _rolling_downside_vol_ratio(ret.values, window=24)
    else:
        df["downside_vol_ratio_24"] = np.nan

    if close is not None:
        df["max_drawdown_12"] = _rolling_max_drawdown(close.values, window=12)
    else:
        df["max_drawdown_12"] = np.nan

    _new_cols = [
        "delta_taker_cumul_12", "sell_vol_ratio_6", "sell_vol_ratio_24",
        "price_vol_divergence_12", "rsi_14_above_70_bars",
        "dist_from_local_high_24", "dist_from_local_high_168",
        "ret_neg_autocorr_12", "skew_ret_12", "downside_vol_ratio_24",
        "max_drawdown_12",
    ]
    for col in _new_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers numériques (fonctions internes, pas de dépendances externes)
# ─────────────────────────────────────────────────────────────────────────────

def _rolling_correlation(s1: pd.Series, s2: pd.Series, window: int) -> pd.Series:
    """Corrélation rolling entre deux séries. NaN si variance nulle."""
    return s1.rolling(window, min_periods=window // 2).corr(s2)


def _consecutive_count(arr: np.ndarray) -> np.ndarray:
    """
    Pour chaque position i, retourne le nombre de 1 consécutifs se terminant en i.
    Exemple : [0,0,1,1,1,0,1,1] → [0,0,1,2,3,0,1,2]
    """
    out = np.zeros(len(arr), dtype=np.float64)
    count = 0
    for i in range(len(arr)):
        if arr[i] == 1:
            count += 1
        else:
            count = 0
        out[i] = count
    return out


def _rolling_lag1_autocorr(arr: np.ndarray, window: int) -> np.ndarray:
    """
    Autocorrélation lag-1 sur fenêtre glissante.
    Négatif = mean-reversion probable.
    """
    out = np.full(len(arr), np.nan)
    for i in range(window, len(arr)):
        sub = arr[i - window : i]
        if sub.std() < 1e-12:
            continue
        try:
            out[i] = np.corrcoef(sub[:-1], sub[1:])[0, 1]
        except Exception:
            pass
    return out


def _rolling_downside_vol_ratio(arr: np.ndarray, window: int) -> np.ndarray:
    """
    std(négatifs) / std(positifs) sur fenêtre glissante.
    > 1.0 : pression vendeuse plus volatile → favorable au short.
    Retourne 1.0 (neutre) si un côté est vide — jamais NaN.
    """
    out = np.ones(len(arr), dtype=np.float64)
    for i in range(window, len(arr)):
        sub = arr[i - window : i]
        neg = sub[sub < 0]
        pos = sub[sub > 0]
        if len(neg) < 1 or len(pos) < 1:
            continue
        std_neg = float(neg.std()) if len(neg) > 1 else float(abs(neg[0]))
        std_pos = float(pos.std()) if len(pos) > 1 else float(abs(pos[0]))
        if std_pos > 1e-12:
            out[i] = std_neg / std_pos
    return out


def _rolling_upside_vol_ratio(arr: np.ndarray, window: int) -> np.ndarray:
    """
    std(positifs) / std(négatifs) sur fenêtre glissante.
    > 1.0 : hausse plus volatile → favorable au long (bull pressure).
    Retourne 1.0 (neutre) si un côté est vide — jamais NaN.
    """
    out = np.ones(len(arr), dtype=np.float64)
    for i in range(window, len(arr)):
        sub = arr[i - window : i]
        pos = sub[sub > 0]
        neg = sub[sub < 0]
        if len(pos) < 1 or len(neg) < 1:
            continue
        std_pos = float(pos.std()) if len(pos) > 1 else float(abs(pos[0]))
        std_neg = float(neg.std()) if len(neg) > 1 else float(abs(neg[0]))
        if std_neg > 1e-12:
            out[i] = std_pos / std_neg
    return out


def _rolling_max_drawdown(prices: np.ndarray, window: int) -> np.ndarray:
    """
    MDD rolling sur `window` barres.
    MDD = min((price - rolling_peak) / rolling_peak) sur la fenêtre.
    """
    out = np.full(len(prices), np.nan)
    for i in range(window, len(prices)):
        sub   = prices[i - window : i]
        valid = sub[sub > 0]
        if len(valid) < 3:
            continue
        peak   = np.maximum.accumulate(valid)
        drawdn = (valid - peak) / peak
        out[i] = float(drawdn.min())
    return out
