"""
ai/level_2/regime_allocator.py — ALLOCATEUR RÉGIME + HEDGE v5
=============================================================

Remplace le TRM SHORT standalone (audit 2026-05 → SHORT_REJECTED) par deux
mécanismes complémentaires qui ne dépendent pas de la direction baissière :

1. Macro-régime BEAR / BULL / NEUTRAL  (vote majoritaire 24h sur indicateurs BTC)
   - compute_macro_regime_v5(df)       → pd.Series["BEAR"/"BULL"/"NEUTRAL"]
   - compute_long_size_multiplier(...)  → 0.65 en BEAR, 1.0 sinon
   - Impact attendu : réduction de 15-25% du max-drawdown sans toucher au PF LONG

2. Funding Harvest Signal  (rare, haute précision, 20-40 signaux/an)
   - compute_funding_harvest_signal(df) → pd.Series[bool]
   - Condition : funding > 0.05%/8h ET OI en hausse ET RSI > 60
   - Logique : foule surexposée long → reversion probable sur 4-8h
   - Taille : 2% du capital / trade, stop ATR×1.5

Colonnes enriched parquet utilisées :
  dist_ema_200      : (close / EMA200) - 1  — négatif = sous EMA200
  ema_spread_50_200 : (EMA50 / EMA200) - 1  — négatif = death cross
  mom_logret_72     : log-return 3 jours  (72 barres 1h)
  mom_logret_720    : log-return 30 jours (720 barres 1h)  [optionnel]
  funding_rate      : decimal (0.001 = 0.1%/8h)
  rsi_14            : RSI 14 périodes
  open_interest / oi_usd_8h / oi_usd : open interest (n'importe quel nom)
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


_EPS = 1e-9
_BEAR_VOTE_THR  = 0.60   # 60% des 24 dernières barres confirment BEAR
_BULL_VOTE_THR  = 0.60
_BEAR_SIZE_MULT = 0.65   # réduire les LONG de 35% en BEAR confirmé
_FUNDING_THR    = 0.0005  # 0.05%/8h — funding élevé = foule long
_FUNDING_Z_THR  = 1.5     # ou z-score ≥ 1.5 σ
_RSI_HARVEST    = 60.0    # RSI > 60 = overbought à court terme
_OI_LOOKBACK    = 4       # barres de hausse OI requises
_HARVEST_HOLD   = 4       # barres de tenue pour le funding harvest (4h)
_HARVEST_SIZE   = 0.02    # 2% du capital par trade funding


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, *names: str, default: float = 0.0) -> np.ndarray:
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").fillna(default).to_numpy(dtype=np.float64)
    return np.full(len(df), default, dtype=np.float64)


def _rolling_mean(arr: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    return (
        pd.Series(arr)
        .rolling(window, min_periods=min_periods)
        .mean()
        .fillna(0.0)
        .to_numpy(dtype=np.float64)
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Détecteur de régime macro
# ─────────────────────────────────────────────────────────────────────────────

def compute_macro_regime_v5(df: pd.DataFrame) -> pd.Series:
    """
    Détecte le régime macro BEAR / BULL / NEUTRAL à partir des indicateurs
    techniques disponibles dans les parquets enrichis.

    Algorithme
    ----------
    bear_raw = (dist_ema_200 < -0.02) AND (mom_3d < -0.05 OR mom_30d < -0.15)
    bull_raw = (dist_ema_200 > 0)     AND (ema_50_200 > 0)  AND (mom_3d > 0.03)

    Chaque signal est lissé par vote majoritaire sur 24 barres (60% requis).
    BEAR prend la priorité sur BULL en cas de conflit.

    Columns requises (avec fallbacks gracieux si absentes)
    -----
    dist_ema_200      : (close/EMA200 - 1)  — principal détecteur
    ema_spread_50_200 : (EMA50/EMA200 - 1)  — golden/death cross
    mom_logret_72     : log-return 3j
    mom_logret_720    : log-return 30j  [optionnel, default 0]
    """
    n = len(df)

    dist200   = _col(df, "dist_ema_200")
    spread_50 = _col(df, "ema_spread_50_200")
    mom_3d    = _col(df, "mom_logret_72")
    mom_30d   = _col(df, "mom_logret_720")

    bear_raw = (dist200 < -0.02) & ((mom_3d < -0.05) | (mom_30d < -0.15))
    bull_raw = (dist200 > 0.0)   &  (spread_50 > 0.0) & (mom_3d > 0.03)

    s_bear = _rolling_mean(bear_raw.astype(np.float64), window=24, min_periods=12)
    s_bull = _rolling_mean(bull_raw.astype(np.float64), window=24, min_periods=12)

    regime = np.full(n, "NEUTRAL", dtype=object)
    regime[s_bull > _BULL_VOTE_THR] = "BULL"
    regime[s_bear > _BEAR_VOTE_THR] = "BEAR"   # BEAR > BULL en priorité

    return pd.Series(regime, index=df.index, dtype=str)


def compute_long_size_multiplier(macro_regime: pd.Series) -> pd.Series:
    """
    Retourne le multiplicateur de taille LONG selon le régime macro.

    BEAR    → 0.65  (réduction 35% — couvre ~2 σ de drawdown supplémentaire)
    BULL    → 1.00  (taille pleine)
    NEUTRAL → 1.00  (taille pleine — pas de signal négatif confirmé)
    """
    mult = pd.Series(1.0, index=macro_regime.index, dtype=np.float64)
    mult[macro_regime == "BEAR"] = _BEAR_SIZE_MULT
    return mult


# ─────────────────────────────────────────────────────────────────────────────
# 2. Funding Harvest Signal
# ─────────────────────────────────────────────────────────────────────────────

def compute_funding_harvest_signal(df: pd.DataFrame) -> pd.Series:
    """
    Détecte les barres où la foule est surexposée long (funding harvest).

    Signal = True quand TOUTES ces conditions sont vraies :
      A. funding_rate > 0.05%/8h  OU  funding_z > 1.5 σ   (foule trop longue)
      B. RSI_14 > 60                                        (court terme overbought)
      C. OI en hausse sur les 4 dernières barres            (crowding qui s'accélère)

    Fréquence attendue : 20-40 signaux/an sur BTC à ces conditions strictes.
    Ne pas utiliser en marché BEAR confirmé (funding y est déjà négatif).
    """
    n = len(df)

    funding = _col(df, "funding_rate")
    rsi     = _col(df, "rsi_14", default=50.0)

    # Open interest — plusieurs variantes de noms selon la source
    oi: Optional[np.ndarray] = None
    for oi_col in ("open_interest", "oi_usd_8h", "oi_usd", "oi", "openInterest"):
        if oi_col in df.columns:
            oi = (
                pd.to_numeric(df[oi_col], errors="coerce")
                .ffill().bfill().fillna(0.0)
                .to_numpy(dtype=np.float64)
            )
            break
    if oi is None:
        oi = np.zeros(n, dtype=np.float64)

    # Funding z-score sur fenêtre 72h
    f_ser   = pd.Series(funding)
    f_mean  = f_ser.rolling(72, min_periods=24).mean().fillna(0.0)
    f_std   = f_ser.rolling(72, min_periods=24).std().fillna(0.001)
    f_z     = ((f_ser - f_mean) / f_std.clip(lower=_EPS)).fillna(0.0).to_numpy()

    # Condition A : funding élevé
    cond_a = (funding > _FUNDING_THR) | (f_z > _FUNDING_Z_THR)

    # Condition B : RSI overbought
    cond_b = rsi > _RSI_HARVEST

    # Condition C : OI en hausse sur _OI_LOOKBACK barres
    oi_ser  = pd.Series(oi)
    cond_c  = (oi_ser > oi_ser.shift(_OI_LOOKBACK)).fillna(False).to_numpy(dtype=bool)

    signal = cond_a & cond_b & cond_c

    return pd.Series(signal, index=df.index, dtype=bool)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Backtest funding harvest
# ─────────────────────────────────────────────────────────────────────────────

def backtest_funding_harvest(
    df_test:       pd.DataFrame,
    cost_short:    float = 0.0015,    # 15 bps — coût conservateur short
    cooldown_bars: int   = 8,         # 8h de cooldown entre deux signaux
) -> Dict:
    """
    Simule les trades funding harvest sur df_test.

    Stratégie :
      - Signal = compute_funding_harvest_signal()
      - Entrée : short au close de la barre signal
      - Sortie : 4 barres plus tard (4h)
      - Coût   : cost_short round-trip
      - Cooldown : 8 barres entre deux entrées

    Retourne les métriques standards (n_trades, pf, wr, expectancy).
    """
    signal = compute_funding_harvest_signal(df_test)

    close_col = "Close" if "Close" in df_test.columns else "close"
    if close_col not in df_test.columns:
        return {"n_trades": 0, "pf": 0.0, "wr": 0.0, "expectancy": 0.0}

    close = df_test[close_col].fillna(method="ffill").to_numpy(dtype=np.float64)
    n     = len(close)
    sig   = signal.to_numpy(dtype=bool)

    trade_rets: List[float] = []
    last_trade  = -cooldown_bars - 1

    for i in range(n - _HARVEST_HOLD):
        if not sig[i]:
            continue
        if (i - last_trade) < cooldown_bars:
            continue

        entry = close[i]
        if entry <= 0:
            continue

        exit_price = close[i + _HARVEST_HOLD]
        # Short : profit quand prix descend
        gross = (entry - exit_price) / entry
        net   = gross - cost_short

        trade_rets.append(float(net))
        last_trade = i

    if not trade_rets:
        return {"n_trades": 0, "pf": 0.0, "wr": 0.0, "expectancy": 0.0}

    arr  = np.array(trade_rets, dtype=np.float64)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    gw = float(wins.sum())   if len(wins)   else 0.0
    gl = float(abs(losses.sum())) if len(losses) else 0.0
    pf = gw / max(gl, _EPS)
    wr = len(wins) / len(arr)

    return {
        "n_trades":   len(arr),
        "pf":         round(pf, 3),
        "wr":         round(wr, 3),
        "expectancy": round(float(arr.mean()) * 100, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. Analyse complète d'un fold
# ─────────────────────────────────────────────────────────────────────────────

def run_regime_fold(
    df_test:    pd.DataFrame,
    cost_short: float = 0.0015,
) -> Dict:
    """
    Point d'entrée principal pour un fold du walk-forward.

    Retourne :
      bear_pct      : % du temps en régime BEAR
      bull_pct      : % du temps en régime BULL
      size_mult_mean: multiplicateur moyen de taille (< 1.0 = réduction en BEAR)
      harvest_n     : trades funding harvest
      harvest_pf    : profit factor funding harvest
      harvest_wr    : win rate funding harvest
      dd_reduction_est_pct : estimation réduction DD par le sizing
    """
    n = len(df_test)
    if n < 50:
        return _empty_regime_result()

    macro  = compute_macro_regime_v5(df_test)
    sizing = compute_long_size_multiplier(macro)

    bear_pct = float((macro == "BEAR").mean() * 100)
    bull_pct = float((macro == "BULL").mean() * 100)
    size_mean = float(sizing.mean())

    # Estimation de la réduction de drawdown :
    # En BEAR (size 0.65), chaque trade génère 0.65 de la perte normale.
    # Soit p_bear = bear_pct/100, la réduction attendue = p_bear × (1 - 0.65) = p_bear × 0.35
    dd_red_est = bear_pct / 100.0 * (1.0 - _BEAR_SIZE_MULT) * 100.0

    harvest = backtest_funding_harvest(df_test, cost_short=cost_short)

    return {
        "bear_pct":           round(bear_pct, 1),
        "bull_pct":           round(bull_pct, 1),
        "size_mult_mean":     round(size_mean, 3),
        "dd_reduction_est_pct": round(dd_red_est, 1),
        "harvest_n":          harvest["n_trades"],
        "harvest_pf":         harvest["pf"],
        "harvest_wr":         harvest.get("wr", 0.0),
        "harvest_expectancy": harvest.get("expectancy", 0.0),
    }


def _empty_regime_result() -> Dict:
    return {
        "bear_pct": 0.0, "bull_pct": 0.0,
        "size_mult_mean": 1.0, "dd_reduction_est_pct": 0.0,
        "harvest_n": 0, "harvest_pf": 0.0,
        "harvest_wr": 0.0, "harvest_expectancy": 0.0,
    }
