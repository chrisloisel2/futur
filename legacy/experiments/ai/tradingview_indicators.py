"""
ai/level_0/tradingview_indicators.py — INDICATEURS TRADINGVIEW OPEN-SOURCE
===========================================================================

8 indicateurs communautaires parmi les plus populaires de TradingView,
portés en Python vectorisé depuis leurs formules Pine Script open-source.

Chaque indicateur apporte une lecture orthogonale du marché :

  Squeeze Momentum  (LazyBear) — détection consolidation puis explosion
  Supertrend                   — tendance adaptée à la volatilité ATR
  WaveTrend         (LazyBear) — oscillateur de retournement normé
  ADX + DMI         (Wilder)   — force de tendance + direction DI+/DI-
  Hull MA           (Hull)     — MA sans lag, pente rapide et propre
  Zero Lag MACD                — MACD sans décalage, plus réactif
  LSMA + R²                    — régression linéaire + qualité du trend
  Chandelier Exit   (Volman)   — stops ATR → position vs niveau de sortie

Sources Pine Script (open-source CC) :
  LazyBear Squeeze  : tradingview.com/script/nqQ1DT5a
  LazyBear WaveTrend: tradingview.com/script/2KE8wTuF
  Supertrend        : pine built-in ta.supertrend()
  ADX               : pine built-in ta.dmi()
  Chandelier Exit   : tradingview.com/support/solutions/43000773013

Convention anti-leakage : toutes les fenêtres rolling utilisent [0..t] uniquement.
NaN de début de série gérés par fillna(0.0) en fin de fonction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes (non exportés)
# ─────────────────────────────────────────────────────────────────────────────

def _get_ohlcv(df: pd.DataFrame):
    c = "Close"  if "Close"  in df.columns else ("close"  if "close"  in df.columns else None)
    h = "High"   if "High"   in df.columns else ("high"   if "high"   in df.columns else None)
    l = "Low"    if "Low"    in df.columns else ("low"    if "low"    in df.columns else None)
    v = "Volume" if "Volume" in df.columns else ("volume" if "volume" in df.columns else None)
    return c, h, l, v


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    return pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)


def _wma(series: pd.Series, period: int) -> pd.Series:
    """WMA linéaire : poids i+1 pour la i-ème barre (1=plus vieille, n=plus récente)."""
    weights = np.arange(1, period + 1, dtype=float)
    norm    = weights.sum()
    return series.rolling(period).apply(lambda x: np.dot(x, weights) / norm, raw=True)


def _hma(series: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average — WMA(2·WMA(n/2) - WMA(n), √n). Très peu de lag."""
    half   = max(2, period // 2)
    sq     = max(2, round(period ** 0.5))
    raw    = 2 * _wma(series, half) - _wma(series, period)
    return _wma(raw, sq)


def _zlema(series: pd.Series, period: int) -> pd.Series:
    """Zero Lag EMA — EMA(src + (src - src.shift(lag)), period)."""
    lag = (period - 1) // 2
    return (series + (series - series.shift(lag))).ewm(span=period, adjust=False).mean()


def _linreg_value(series: pd.Series, period: int) -> pd.Series:
    """
    LSMA — valeur de fin de régression linéaire sur `period` barres.
    Formule analytique vectorisée (poids précalculés, O(n·period)).
    """
    x    = np.arange(period, dtype=float)
    mx   = x.mean()
    sx2  = ((x - mx) ** 2).sum()
    # Poids pour l'endpoint : lsma = Σ w_k · y_k
    # = slope·(n-1) + intercept = slope·(n-1-mx) + mean_y
    w = (x - mx) * (period - 1 - mx) / (period * sx2) + 1.0 / period
    return series.rolling(period).apply(lambda y: np.dot(y, w), raw=True)


def _linreg_slope(series: pd.Series, period: int) -> pd.Series:
    """Pente de la régression linéaire sur `period` barres (en unités de prix/barre)."""
    x   = np.arange(period, dtype=float)
    mx  = x.mean()
    sx2 = ((x - mx) ** 2).sum()
    w_s = (x - mx) / (period * sx2)  # poids pour la pente
    return series.rolling(period).apply(lambda y: np.dot(y, w_s), raw=True)


def _linreg_r2(series: pd.Series, period: int, slope: pd.Series) -> pd.Series:
    """
    R² rolling à partir de la pente déjà calculée.
    R² = slope² · Var(x) / Var(y)  [analytiquement exact pour la régression linéaire]
    """
    var_x = (period ** 2 - 1) / 12.0  # Var([0, 1, ..., n-1])
    var_y = series.rolling(period).var().clip(lower=1e-12)
    return (slope ** 2 * var_x / var_y).clip(0.0, 1.0)


def _supertrend(
    close: np.ndarray, high: np.ndarray, low: np.ndarray,
    atr: np.ndarray, factor: float = 3.0
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Supertrend (itératif — nécessite une boucle path-dependent).
    Retourne (supertrend_line, direction) où direction ∈ {+1, -1}.
    """
    n   = len(close)
    hl2 = (high + low) / 2.0

    basic_up = hl2 + factor * atr
    basic_dn = hl2 - factor * atr

    upper     = basic_up.copy()
    lower     = basic_dn.copy()
    direction = np.ones(n, dtype=float)
    st_line   = np.zeros(n)
    st_line[0]= lower[0]

    for i in range(1, n):
        if np.isnan(basic_up[i]) or np.isnan(basic_dn[i]):
            upper[i]     = upper[i - 1]
            lower[i]     = lower[i - 1]
            direction[i] = direction[i - 1]
            st_line[i]   = st_line[i - 1]
            continue

        upper[i] = basic_up[i] if (basic_up[i] < upper[i-1] or close[i-1] > upper[i-1]) else upper[i-1]
        lower[i] = basic_dn[i] if (basic_dn[i] > lower[i-1] or close[i-1] < lower[i-1]) else lower[i-1]

        if   direction[i-1] == -1 and close[i] > upper[i]:
            direction[i] = 1
        elif direction[i-1] ==  1 and close[i] < lower[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]

        st_line[i] = lower[i] if direction[i] == 1 else upper[i]

    return st_line, direction


# ─────────────────────────────────────────────────────────────────────────────
# Colonnes produites
# ─────────────────────────────────────────────────────────────────────────────

TRADINGVIEW_COLS = [
    # ── Squeeze Momentum (LazyBear) ───────────────────────────────────────────
    "sqz_in_squeeze",       # 1 = BB dans KC (consolidation avant explosion)
    "sqz_momentum",         # linreg momentum normé : positif=bull, négatif=bear
    "sqz_momentum_accel",   # accélération du momentum (confirmation du mouvement)
    "sqz_on_release",       # 1 = vient de quitter la squeeze (explosion imminente)
    # ── Supertrend (ATR factor 3, period 10) ─────────────────────────────────
    "supertrend_dir",       # +1 = uptrend, -1 = downtrend
    "supertrend_dist",      # (close - ST_line) / close — position relative
    "supertrend_flip",      # +1 = passage bearish→bull, -1 = bull→bearish
    # ── WaveTrend Oscillator (LazyBear) ──────────────────────────────────────
    "wt1",                  # oscillateur principal normé [-100, +100]
    "wt_diff",              # wt1 - wt2 : mesure du crossover signal
    "wt_overbought",        # 1 si wt1 > 53 (retournement short probable)
    "wt_oversold",          # 1 si wt1 < -53 (rebond long probable)
    # ── ADX + Directional Movement (Wilder 14) ────────────────────────────────
    "adx_14",               # force de tendance [0, 100] (>25 = trending)
    "di_diff",              # (+DI - -DI) / 100 : biais directionnel net
    "adx_trending",         # 1 si ADX > 25 (marché directionnel)
    # ── Hull Moving Average (period 20) ──────────────────────────────────────
    "hma_dist",             # (close - HMA) / close : position vs HMA
    "hma_slope",            # pente normée HMA (tendance sur 3 barres)
    # ── Zero Lag MACD (12/26/9) ──────────────────────────────────────────────
    "zlmacd_hist",          # histogramme normé (momentum directionnel)
    "zlmacd_slope",         # accélération histogramme (confirmation)
    # ── LSMA + Linear Regression (period 20) ─────────────────────────────────
    "lsma_dist",            # (close - LSMA) / close : déviation du trend
    "lsma_slope",           # pente de régression normée (direction trend)
    "lr_r2",                # R² [0,1] : qualité du trend linéaire
    # ── Chandelier Exit (22 barres, factor 3) ────────────────────────────────
    "chandelier_long_dist",  # (close - CE_long) / close : > 0 = zone bull
    "chandelier_short_dist", # (close - CE_short) / close : < 0 = zone bear
]


# ─────────────────────────────────────────────────────────────────────────────
# Fonction principale
# ─────────────────────────────────────────────────────────────────────────────

def compute_tradingview_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule 23 features issues des 8 scripts TradingView open-source les plus populaires.

    Dépendances : colonnes OHLCV (close/high/low/volume) — appeler après
    compute_live_features() pour que rsi_14 soit déjà disponible.

    Tous les indicateurs respectent l'anti-leakage : fenêtres rolling [0..t] uniquement.
    """
    df = df.copy()
    c_col, h_col, l_col, _ = _get_ohlcv(df)

    if any(x is None for x in [c_col, h_col, l_col]):
        for col in TRADINGVIEW_COLS:
            df[col] = 0.0
        return df

    close = pd.to_numeric(df[c_col], errors="coerce")
    high  = pd.to_numeric(df[h_col], errors="coerce")
    low   = pd.to_numeric(df[l_col], errors="coerce")
    safe  = close.clip(lower=1e-9)

    tr = _true_range(high, low, close)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. SQUEEZE MOMENTUM — LazyBear (original: nqQ1DT5a)
    # ─────────────────────────────────────────────────────────────────────────
    # Détecte quand les Bollinger Bands (BB) entrent dans les Keltner Channels (KC)
    # → consolidation (squeeze on) puis explosion à la sortie (squeeze off).
    # Le momentum via linreg donne la direction probable du breakout.
    _bb_len  = 20
    _bb_mult = 2.0
    _kc_len  = 20
    _kc_mult = 1.5

    # Bollinger Bands
    bb_mid   = close.rolling(_bb_len).mean()
    bb_std   = close.rolling(_bb_len).std()
    bb_upper = bb_mid + _bb_mult * bb_std
    bb_lower = bb_mid - _bb_mult * bb_std

    # Keltner Channels (ATR via Wilder's EWM)
    atr_kc   = tr.ewm(alpha=1.0 / _kc_len, adjust=False).mean()
    kc_mid   = close.rolling(_kc_len).mean()
    kc_upper = kc_mid + _kc_mult * atr_kc
    kc_lower = kc_mid - _kc_mult * atr_kc

    # Squeeze détection
    sqz_on  = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    sqz_off = (bb_lower < kc_lower) & (bb_upper > kc_upper)
    df["sqz_in_squeeze"] = sqz_on.astype(float)
    df["sqz_on_release"] = sqz_off.astype(float)

    # Momentum : linreg(close - avg(midpoint, sma), KC_length)
    # avg(avg(highest_high, lowest_low), sma(close)) en Pine Script
    hh_kc    = high.rolling(_kc_len).max()
    ll_kc    = low.rolling(_kc_len).min()
    midpoint = (hh_kc + ll_kc) / 2.0
    sma_kc   = close.rolling(_kc_len).mean()
    val_input = close - (midpoint + sma_kc) / 2.0
    sqz_mom_raw = _linreg_value(val_input, _kc_len)
    df["sqz_momentum"]       = (sqz_mom_raw / safe).clip(-0.05, 0.05)
    df["sqz_momentum_accel"] = (df["sqz_momentum"] - df["sqz_momentum"].shift(3)).clip(-0.03, 0.03)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. SUPERTREND — ATR dynamique (factor=3, period=10)
    # ─────────────────────────────────────────────────────────────────────────
    # Ligne de support/résistance qui suit le prix via ATR.
    # Flip de direction = signal fort de retournement de tendance.
    _st_factor = 3.0
    _st_period = 10
    atr_st = tr.ewm(alpha=1.0 / _st_period, adjust=False).mean()

    st_line, st_dir = _supertrend(
        close.values, high.values, low.values, atr_st.values, _st_factor
    )
    st_line_s = pd.Series(st_line, index=close.index)
    st_dir_s  = pd.Series(st_dir,  index=close.index)

    df["supertrend_dir"]  = st_dir_s
    df["supertrend_dist"] = ((close - st_line_s) / safe).clip(-0.15, 0.15)

    # Flip : changement de direction ce bar
    prev_dir = st_dir_s.shift(1)
    df["supertrend_flip"] = np.where(
        (prev_dir == -1) & (st_dir_s == 1),   1.0,
        np.where((prev_dir == 1) & (st_dir_s == -1), -1.0, 0.0)
    )

    # ─────────────────────────────────────────────────────────────────────────
    # 3. WAVETREND OSCILLATOR — LazyBear (original: 2KE8wTuF)
    # ─────────────────────────────────────────────────────────────────────────
    # Oscillateur normé construit sur la déviation du prix de son EMA.
    # Niveaux : ±53 = zone signal, ±60 = extrêmes.
    _wt_n1 = 10   # channel period
    _wt_n2 = 21   # average period

    ap  = (high + low + close) / 3.0         # HLC3 = typical price
    esa = ap.ewm(span=_wt_n1, adjust=False).mean()
    d   = (ap - esa).abs().ewm(span=_wt_n1, adjust=False).mean().clip(lower=1e-9)
    ci  = (ap - esa) / (0.015 * d)
    wt1 = ci.ewm(span=_wt_n2, adjust=False).mean()
    wt2 = wt1.rolling(4).mean()

    df["wt1"]          = wt1.clip(-100, 100)
    df["wt_diff"]      = (wt1 - wt2).clip(-50, 50)
    df["wt_overbought"]= (wt1 > 53).astype(float)
    df["wt_oversold"]  = (wt1 < -53).astype(float)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. ADX + DIRECTIONAL MOVEMENT — Wilder (period=14)
    # ─────────────────────────────────────────────────────────────────────────
    # ADX mesure la FORCE de la tendance, pas sa direction.
    # +DI > -DI = haussier, inversement baissier.
    # ADX > 25 = tendance directionnelle établie.
    _adx_period = 14

    up_move   = (high - high.shift(1)).clip(lower=0.0)
    down_move = (low.shift(1) - low).clip(lower=0.0)

    dm_plus  = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=close.index
    )
    dm_minus = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=close.index
    )

    # Wilder's smoothing (EWM alpha = 1/period)
    atr_adx  = tr.ewm(alpha=1.0 / _adx_period, adjust=False).mean().clip(lower=1e-9)
    pdm_s    = dm_plus.ewm(alpha=1.0 / _adx_period, adjust=False).mean()
    ndm_s    = dm_minus.ewm(alpha=1.0 / _adx_period, adjust=False).mean()

    di_plus  = 100.0 * pdm_s / atr_adx
    di_minus = 100.0 * ndm_s / atr_adx
    di_sum   = (di_plus + di_minus).clip(lower=1e-9)
    dx       = 100.0 * (di_plus - di_minus).abs() / di_sum
    adx      = dx.ewm(alpha=1.0 / _adx_period, adjust=False).mean()

    df["adx_14"]      = adx.clip(0.0, 100.0)
    df["di_diff"]     = ((di_plus - di_minus) / 100.0).clip(-1.0, 1.0)
    df["adx_trending"]= (adx > 25.0).astype(float)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. HULL MOVING AVERAGE — Alan Hull (period=20)
    # ─────────────────────────────────────────────────────────────────────────
    # HMA = WMA(2·WMA(n/2) - WMA(n), √n)
    # Objectif : réduire le lag des MA classiques tout en restant lisse.
    _hma_period = 20
    hma = _hma(close, _hma_period)

    df["hma_dist"]  = ((close - hma) / safe).clip(-0.15, 0.15)
    df["hma_slope"] = ((hma - hma.shift(3)) / safe).clip(-0.05, 0.05)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. ZERO LAG MACD — (12/26/9)
    # ─────────────────────────────────────────────────────────────────────────
    # ZLEMA = EMA(src + (src - src.shift(lag)), period)
    # Plus réactif que le MACD standard car annule le biais de retard de l'EMA.
    fast_zl    = _zlema(close, 12)
    slow_zl    = _zlema(close, 26)
    zl_line    = fast_zl - slow_zl
    zl_signal  = _zlema(zl_line, 9)
    zl_hist    = zl_line - zl_signal

    # Normaliser par le prix pour rendre scale-free
    df["zlmacd_hist"]  = (zl_hist / safe).clip(-0.05, 0.05)
    df["zlmacd_slope"] = (df["zlmacd_hist"] - df["zlmacd_hist"].shift(3)).clip(-0.03, 0.03)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. LSMA + RÉGRESSION LINÉAIRE — (period=20)
    # ─────────────────────────────────────────────────────────────────────────
    # LSMA = valeur finale de la droite de moindres carrés sur 20 barres.
    # Mesure la déviation du prix par rapport à sa tendance linéaire optimale.
    # R² mesure si le marché est en trend (R²→1) ou en range (R²→0).
    _lr_period = 20
    lsma  = _linreg_value(close, _lr_period)
    slope = _linreg_slope(close, _lr_period)
    r2    = _linreg_r2(close, _lr_period, slope)

    df["lsma_dist"]  = ((close - lsma) / safe).clip(-0.15, 0.15)
    df["lsma_slope"] = (slope / safe).clip(-0.02, 0.02)
    df["lr_r2"]      = r2

    # ─────────────────────────────────────────────────────────────────────────
    # 8. CHANDELIER EXIT — Volman (22 barres, factor=3)
    # ─────────────────────────────────────────────────────────────────────────
    # CE_long  = max(high, 22) - 3 × ATR(22) : stop trail haussier
    # CE_short = min(low,  22) + 3 × ATR(22) : stop trail baissier
    # Interprétation features :
    #   chandelier_long_dist > 0 : close au-dessus du CE_long = zone bull protégée
    #   chandelier_short_dist < 0 : close en-dessous du CE_short = zone bear protégée
    _ce_period = 22
    _ce_factor = 3.0

    atr_ce   = tr.ewm(alpha=1.0 / _ce_period, adjust=False).mean()
    ce_long  = high.rolling(_ce_period).max() - _ce_factor * atr_ce
    ce_short = low.rolling(_ce_period).min()  + _ce_factor * atr_ce

    df["chandelier_long_dist"]  = ((close - ce_long)  / safe).clip(-0.15, 0.15)
    df["chandelier_short_dist"] = ((close - ce_short) / safe).clip(-0.15, 0.15)

    # ─────────────────────────────────────────────────────────────────────────
    for col in TRADINGVIEW_COLS:
        df[col] = df[col].fillna(0.0)

    return df
