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
      mom_logret_4               — momentum log-return 4 barres (4h) — contexte court terme
      mom_logret_8               — momentum log-return 8 barres (8h) — aligné sur l'horizon
      mom_logret_168             — momentum 7 jours (168h) — régime macro
      vol_ratio_4h               — ratio volume 4h / moyenne 4j — spike d'activité
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

    # ── Momentum multi-horizon ────────────────────────────────────────────────
    if close is not None:
        log_c = np.log(close.astype(np.float64))
        df["mom_logret_4"]   = log_c - log_c.shift(4)     # 4h — contexte court terme
        df["mom_logret_8"]   = log_c - log_c.shift(8)     # 8h — aligné sur l'horizon de prédiction
        df["mom_logret_168"] = log_c - log_c.shift(168)   # 7j — régime macro
    else:
        df["mom_logret_4"]   = np.nan
        df["mom_logret_8"]   = np.nan
        df["mom_logret_168"] = np.nan

    # ── vol_ratio_4h : activité volume 4h vs baseline 4-jours ────────────────
    vol_col = "Volume" if "Volume" in df.columns else ("volume" if "volume" in df.columns else None)
    if vol_col is not None:
        vol = pd.to_numeric(df[vol_col], errors="coerce")
        vol_4h   = vol.rolling(4, min_periods=1).sum()
        vol_base = vol.rolling(96, min_periods=24).mean().clip(lower=1e-9)
        df["vol_ratio_4h"] = vol_4h / (4 * vol_base)
    else:
        df["vol_ratio_4h"] = np.nan

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

    # Proxy de return 1h passé — uniquement des données historiques (pas de leakage)
    if "mom_logret_6" in df.columns:
        ret = df["mom_logret_6"] / 6.0
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
        "mom_logret_4", "mom_logret_8", "mom_logret_168", "vol_ratio_4h",
        "dist_from_local_low_24", "dist_from_local_low_168", "breakout_strength_24",
        "trend_persistence_12", "ret_pos_autocorr_12", "upside_vol_ratio_24",
        "taker_buy_cumul_12", "buy_vol_ratio_6", "momentum_accel_6", "boll_expansion_6",
    ]
    for col in _new_cols_long:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    # Features cross-macro (hedge_fund bundle) — ajoutees si macro disponible
    df = compute_macro_cross_features(df)

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

    # Features cross-macro (hedge_fund bundle) — ajoutees si macro disponible
    df = compute_macro_cross_features(df)

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


# ─────────────────────────────────────────────────────────────────────────────
# LEVIER 1 — Features event-driven (golden cross + distance EMA200 normée ATR)
# ─────────────────────────────────────────────────────────────────────────────

def compute_event_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Capture les événements structurels EMA qui prédisent mieux les moves 4h.

    Colonnes ajoutées
    -----------------
    days_since_golden_cross : barres depuis le dernier croisement EMA50 > EMA200
                              (NaN si jamais croisé ou si en death cross)
    gc_fresh                : 1 si golden cross dans les 168 dernières barres (7 jours)
    dist_ema200_atr         : (Close - EMA200) / ATR14 — distance normée par volatilité
                              Plus robuste que la distance % car adapte au régime de vol.

    Pourquoi ces features améliorent l'AUC :
    - L'EMA cross fraction brute (ema_spread_50_200) ne distingue pas un cross récent
      d'un cross vieux de 6 mois. Le modèle ne peut pas utiliser cette nuance.
    - Un golden cross FRAIS (< 7j) est un signal structurellement différent d'un marché
      en bull établi depuis des mois. days_since_golden_cross capture cette différence.
    - dist_ema200_atr normalise par la volatilité actuelle → comparable entre régimes.
    """
    df = df.copy()
    n  = len(df)

    if "ema_spread_50_200" not in df.columns:
        df["days_since_golden_cross"] = np.nan
        df["gc_fresh"]                = np.nan
        df["dist_ema200_atr"]         = np.nan
        return df

    spread = df["ema_spread_50_200"].values.astype(np.float64)

    # ── Détection des golden crosses (transition ≤0 → >0) ────────────────────
    # Vectorisé via searchsorted : O(n log k) au lieu de O(n)
    prev_spread    = np.empty_like(spread)
    prev_spread[0] = spread[0]
    prev_spread[1:] = spread[:-1]
    gc_mask   = (prev_spread <= 0) & (spread > 0)
    gc_indices = np.where(gc_mask)[0]

    if len(gc_indices) == 0:
        days_since_gc = np.full(n, np.nan)
    else:
        all_idx   = np.arange(n)
        # Pour chaque barre i, position du dernier gc_index ≤ i
        insert_pos = np.searchsorted(gc_indices, all_idx, side="right") - 1
        has_prior  = insert_pos >= 0
        last_gc    = np.where(has_prior, gc_indices[np.clip(insert_pos, 0, len(gc_indices) - 1)], 0)
        days_since_gc = np.where(
            has_prior & (spread > 0),
            (all_idx - last_gc).astype(np.float64),
            np.nan,
        )

    df["days_since_golden_cross"] = days_since_gc

    # gc_fresh : golden cross récent (≤ 168 barres = 7 jours)
    df["gc_fresh"] = np.where(
        np.isfinite(days_since_gc) & (days_since_gc <= 168),
        1.0, 0.0,
    ).astype(np.float32)

    # ── Distance EMA200 normée par ATR ────────────────────────────────────────
    close_col = "Close" if "Close" in df.columns else ("close" if "close" in df.columns else None)
    atr_col   = "atr_14" if "atr_14" in df.columns else ("atr_pct_14" if "atr_pct_14" in df.columns else None)

    if "dist_ema_200" in df.columns and close_col and atr_col:
        close    = df[close_col].values.astype(np.float64)
        atr      = df[atr_col].values.astype(np.float64)
        dist_pct = df["dist_ema_200"].values.astype(np.float64)
        # dist_ema_200 = (Close - EMA200) / EMA200 → Close * dist_pct ≈ ecart absolu
        dist_abs = dist_pct * close
        atr_safe = np.where(atr > 0, atr, close * 0.01)
        dist_atr = dist_abs / atr_safe
        df["dist_ema200_atr"] = np.clip(dist_atr, -15.0, 15.0).astype(np.float32)
    else:
        df["dist_ema200_atr"] = np.nan

    for col in ["days_since_golden_cross", "gc_fresh", "dist_ema200_atr"]:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# LEVIER 2 — Features VWAP journalier
# ─────────────────────────────────────────────────────────────────────────────

def compute_vwap_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    VWAP (Volume-Weighted Average Price) journalier et dérivés.

    Colonnes ajoutées
    -----------------
    vwap_daily      : VWAP cumulatif du jour (reset à minuit UTC)
    dist_vwap_pct   : (Close - VWAP) / VWAP — position relative au VWAP
    above_vwap_4h   : fraction des 4 dernières barres au-dessus du VWAP [0,1]

    Pourquoi ces features améliorent l'AUC :
    - Le VWAP est le prix de référence des institutionnels intraday.
      Un long LONG au-dessus du VWAP signale que les acheteurs contrôlent la journée.
    - dist_vwap_pct capture si le prix est "étiré" ou "ancré" par rapport aux volumes.
    - above_vwap_4h mesure la persistance de la pression acheteuse sur 4h —
      directement aligné avec l'horizon de prédiction.
    """
    df   = df.copy()
    n    = len(df)

    # Colonnes sources
    c_map = {}
    for field in ["Close", "High", "Low", "Volume"]:
        if field in df.columns:
            c_map[field.lower()] = field
        elif field.lower() in df.columns:
            c_map[field.lower()] = field.lower()

    if not all(k in c_map for k in ["close", "high", "low", "volume"]):
        df["vwap_daily"]   = np.nan
        df["dist_vwap_pct"] = np.nan
        df["above_vwap_4h"] = np.nan
        return df

    close  = pd.to_numeric(df[c_map["close"]],  errors="coerce")
    high   = pd.to_numeric(df[c_map["high"]],   errors="coerce")
    low    = pd.to_numeric(df[c_map["low"]],    errors="coerce")
    volume = pd.to_numeric(df[c_map["volume"]], errors="coerce").clip(lower=1e-9)

    typical_price = (high + low + close) / 3.0

    # ── VWAP journalier (reset UTC minuit) ───────────────────────────────────
    if isinstance(df.index, pd.DatetimeIndex):
        dates = df.index.normalize()       # minuit UTC de chaque bar
    else:
        # Fallback : fenêtre glissante 24h
        vwap = (typical_price * volume).rolling(24, min_periods=1).sum() \
               / volume.rolling(24, min_periods=1).sum()
        df["vwap_daily"]    = vwap
        df["dist_vwap_pct"] = ((close - vwap) / vwap.clip(lower=1e-9)).clip(-0.1, 0.1)
        above = (close > vwap).astype(float)
        df["above_vwap_4h"] = above.rolling(4, min_periods=1).mean()
        for col in ["vwap_daily", "dist_vwap_pct", "above_vwap_4h"]:
            df[col] = df[col].fillna(0.0)
        return df

    # Groupby jour : cumsum vectorisé via pandas (très rapide)
    df["_date"]  = dates
    cum_tp_vol   = (typical_price * volume).groupby(df["_date"]).cumsum()
    cum_vol      = volume.groupby(df["_date"]).cumsum().clip(lower=1e-9)
    vwap         = cum_tp_vol / cum_vol
    df.drop(columns=["_date"], inplace=True)

    df["vwap_daily"]    = vwap
    df["dist_vwap_pct"] = ((close - vwap) / vwap.clip(lower=1e-9)).clip(-0.15, 0.15)

    above = (close > vwap).astype(float)
    df["above_vwap_4h"] = above.rolling(4, min_periods=1).mean()

    for col in ["vwap_daily", "dist_vwap_pct", "above_vwap_4h"]:
        df[col] = df[col].fillna(0.0)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# LEVIER HEDGE-FUND — Features cross-macro (OI × sentiment × crowd)
# ─────────────────────────────────────────────────────────────────────────────
# Ces features exploitent le bundle hedge_fund (OI, L/S, funding, fear_greed)
# pour construire des signaux de second ordre orthogonaux au price-action pur.
# Toutes calculées sans lookahead, tolérent l'absence de colonnes (→ 0.0).

def compute_macro_cross_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Features cross-macro disponibles uniquement avec le bundle hedge_fund.

    Nouvelles colonnes :
      oi_acceleration_z        — acceleration de l'OI (2e derivee z-scored)
                                  OI qui monte de plus en plus vite = conviction croissante
      crowd_leverage_index     — |funding_z| × |global_ls_z| : levier crowd composite
                                  valeur haute = foule extrêmement positionnee des deux cotes
      macro_confluence_long    — score 0-4 de signaux bullish alignes
                                  (funding+, OI+, FnG greed, L/S haut)
      macro_confluence_short   — score 0-4 de signaux bearish alignes
                                  (funding extreme, OI+price flat, FnG extreme, L/S extreme)
      oi_funding_divergence    — divergence OI vs funding
                                  OI monte + funding negatif = accumulation vs crowd short
                                  Signal contra-trend tres fiable
      macro_regime_score       — score composite [-2,+2] : positif=bullish, negatif=bearish
    """
    df = df.copy()

    def _get(col):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        return pd.Series(0.0, index=df.index)

    oi_z24   = _get("oihist_sumOpenInterest_z_24")
    oi_z72   = _get("oihist_sumOpenInterest_z_72")
    fund_z24 = _get("funding_rate_z_24")
    fund_z72 = _get("funding_rate_z_72")
    fg_z24   = _get("fear_greed_value_z_24")
    fg_z72   = _get("fear_greed_value_z_72")
    gls_z24  = _get("global_ls_longShortRatio_z_24")
    gls_z72  = _get("global_ls_longShortRatio_z_72")
    tbsr_z24 = _get("taker_ls_buySellRatio_z_24")
    tls_imb  = _get("taker_ls_imbalance")

    # ── OI acceleration : d/dt de l'OI z-score (momentum du momentum) ───────
    oi_delta = oi_z24 - oi_z24.shift(1).fillna(0.0)
    oi_delta_mean = oi_delta.rolling(12, min_periods=3).mean()
    oi_delta_std  = oi_delta.rolling(12, min_periods=3).std().clip(lower=1e-9)
    df["oi_acceleration_z"] = ((oi_delta - oi_delta_mean) / oi_delta_std).clip(-3, 3).fillna(0.0)

    # ── Crowd leverage index : les deux cotes extremes en meme temps ────────
    df["crowd_leverage_index"] = (
        oi_z24.abs() * gls_z24.abs() * (1 + fund_z24.abs() * 0.5)
    ).clip(0, 10).fillna(0.0)

    # ── Macro confluence LONG : combien de signaux bullish alignes ───────────
    bull_funding  = (fund_z24 > 0.5).astype(float)
    bull_oi       = (oi_z24 > 0.5).astype(float)
    bull_fng      = (fg_z24  > 0.5).astype(float)
    bull_gls      = (gls_z24 > 0.5).astype(float)
    bull_taker    = (tls_imb > 0.3).astype(float)
    df["macro_confluence_long"] = (bull_funding + bull_oi + bull_fng + bull_gls + bull_taker).fillna(0.0)

    # ── Macro confluence SHORT : combien de signaux bearish alignes ──────────
    bear_funding_extreme = (fund_z24 > 2.0).astype(float)   # funding excessif = fade
    bear_oi_dist         = (oi_z24 > 1.0).astype(float) * ((oi_z72 - oi_z24) > 0.5).astype(float)
    bear_fng_extreme     = (fg_z24 > 2.0).astype(float)     # greed extreme = retournement
    bear_gls_crowded     = (gls_z24 > 2.0).astype(float)    # tout le monde long = short squeeze risk
    bear_taker_exhaust   = (tbsr_z24 < -0.5).astype(float)  # takers vendeurs dominent
    df["macro_confluence_short"] = (
        bear_funding_extreme + bear_oi_dist + bear_fng_extreme + bear_gls_crowded + bear_taker_exhaust
    ).fillna(0.0)

    # ── OI vs funding divergence (smart money signal) ───────────────────────
    # OI monte (smart money accumule) mais funding negatif (crowd short) → bullish
    # OI monte + funding tres positif (crowd long) → distribution → bearish
    oi_sign   = np.sign(oi_z24)
    fund_sign = np.sign(fund_z24)
    df["oi_funding_divergence"] = (oi_sign * (-fund_sign) * oi_z24.abs()).clip(-3, 3).fillna(0.0)

    # ── Macro regime score composite [-2, +2] ───────────────────────────────
    # Positif = macro bullish (long confirmé), Negatif = macro bearish (short confirmé)
    score = (
        fund_z24 * 0.25           # funding direction
        + oi_z24 * 0.25           # OI direction
        + fg_z24 * 0.20           # sentiment direction
        + tls_imb * 0.15          # taker imbalance
        + gls_z24 * 0.10          # crowd positioning
        + tbsr_z24 * 0.05         # taker buy/sell
    )
    df["macro_regime_score"] = score.rolling(4, min_periods=1).mean().clip(-2, 2).fillna(0.0)

    return df
