"""
level_0/short_features.py — FEATURES SPÉCIFIQUES SHORT (GAMECHANGER)
======================================================================

Ce module calcule des features dédiées aux signaux SHORT, organisées en
cinq familles distinctes :

  1. Crowding          — surpositionnement haussier extrême (contra)
  2. Breakdown         — ruptures de support, structure baissière
  3. Failed breakout   — faux cassages haussiers (bull traps)
  4. Liquidity stress  — pression vendeuse et expansion de range
  5. Squeeze risk      — risque de short-squeeze (signal négatif pour le short)

Scores composites (compute_short_context_scores) :
  long_crowding_score, breakdown_score, squeeze_risk_score,
  failed_breakout_score, bear_continuation_score

Point d'entrée principal : compute_all_short_features(df)

Conventions :
  - Vectorisé (aucune boucle Python explicite sur les barres)
  - NaN-safe : fillna(0) uniquement sur les features booléennes/binaires
  - inf-safe : np.clip + replace([inf, -inf], nan)
  - Aucun leakage : pas de shift(-n), .values[t+k] ou toute donnée future
  - Compatible live : peut être appelé barre par barre (rolling window suffisant)
  - Z-scores locaux : rolling(168).mean / rolling(168).std avec min_periods=24
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

_ZSCORE_WINDOW   = 168   # fenêtre z-score local (7 jours horaires)
_ZSCORE_MIN_PER  = 24    # min_periods z-score
_EPS             = 1e-9  # éviter les divisions par zéro


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────

def _local_zscore(s: pd.Series,
                  window: int = _ZSCORE_WINDOW,
                  min_periods: int = _ZSCORE_MIN_PER) -> pd.Series:
    """
    Z-score local rolling.
    NaN là où la std est nulle ou insuffisante — pas de division aveugle.
    """
    mu  = s.rolling(window, min_periods=min_periods).mean()
    sig = s.rolling(window, min_periods=min_periods).std()
    z   = (s - mu) / sig.clip(lower=_EPS)
    return z.replace([np.inf, -np.inf], np.nan)


def _safe(s: pd.Series) -> pd.Series:
    """Remplace inf par NaN."""
    return s.replace([np.inf, -np.inf], np.nan)


def _get_close(df: pd.DataFrame) -> pd.Series:
    if "Close" in df.columns:
        return pd.to_numeric(df["Close"], errors="coerce")
    if "close" in df.columns:
        return pd.to_numeric(df["close"], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _get_volume(df: pd.DataFrame) -> pd.Series:
    if "Volume" in df.columns:
        return pd.to_numeric(df["Volume"], errors="coerce")
    if "volume" in df.columns:
        return pd.to_numeric(df["volume"], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _get_high(df: pd.DataFrame) -> pd.Series:
    if "High" in df.columns:
        return pd.to_numeric(df["High"], errors="coerce")
    if "high" in df.columns:
        return pd.to_numeric(df["high"], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _get_low(df: pd.DataFrame) -> pd.Series:
    if "Low" in df.columns:
        return pd.to_numeric(df["Low"], errors="coerce")
    if "low" in df.columns:
        return pd.to_numeric(df["low"], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """Retourne la colonne ou une série de NaN si absente."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


# ─────────────────────────────────────────────────────────────────────────────
# Calcul des EMA de base (partagées entre les sous-modules)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_emas(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Retourne (ema20, ema50, ema200) calculées depuis Close si absentes.
    Elles sont calculées dans cet ordre précis pour garantir la cohérence.
    """
    close = _get_close(df)

    if "ema_20" in df.columns:
        ema20 = pd.to_numeric(df["ema_20"], errors="coerce")
    else:
        ema20 = _ema(close, 20)

    if "ema_50" in df.columns:
        ema50 = pd.to_numeric(df["ema_50"], errors="coerce")
    else:
        ema50 = _ema(close, 50)

    if "ema_200" in df.columns:
        ema200 = pd.to_numeric(df["ema_200"], errors="coerce")
    else:
        ema200 = _ema(close, 200)

    return ema20, ema50, ema200


# ─────────────────────────────────────────────────────────────────────────────
# 1. CROWDING FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def compute_crowding_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Détecte le surpositionnement haussier extrême (contra-signal pour le short).

    Colonnes ajoutées :
      funding_extreme_positive   — bool : funding_rate_z_24 > 2.0
      funding_accel_24           — accélération court terme du funding
      funding_accel_72           — accélération moyen terme du funding
      long_short_extreme         — bool : global_ls_longShortRatio_z_24 > 2.0
      open_interest_expansion    — bool : oihist_sumOpenInterest_z_24 > 1.5
      oi_price_divergence        — bool : OI monte mais prix baisse sur 24h
      oi_up_price_flat           — bool : OI monte mais prix flat
      oi_up_price_down           — bool : OI monte mais prix baisse nette
      fear_greed_extreme         — bool : fear_greed_value_z_24 > 2.0 (greed extrême)
      long_crowding_score        — score composite de foule haussière [calculé dans scores]
    """
    df = df.copy()

    fr24  = _col(df, "funding_rate_z_24")
    fr72  = _col(df, "funding_rate_z_72")
    ls24  = _col(df, "global_ls_longShortRatio_z_24")
    oi24  = _col(df, "oihist_sumOpenInterest_z_24")
    fg24  = _col(df, "fear_greed_value_z_24")
    mom24 = _col(df, "mom_logret_24")

    # ── Funding ──────────────────────────────────────────────────────────────
    df["funding_extreme_positive"] = (fr24 > 2.0).astype(float).fillna(0.0)
    df["funding_accel_24"]         = _safe(fr24 - fr72)
    # accélération 72h : variation de fr72 sur 72 barres passées
    if "funding_rate_z_72" in df.columns:
        df["funding_accel_72"] = _safe(fr72 - fr72.shift(72))
    else:
        df["funding_accel_72"] = np.nan

    # ── Long/Short ratio ─────────────────────────────────────────────────────
    df["long_short_extreme"]    = (ls24 > 2.0).astype(float).fillna(0.0)

    # ── Open Interest ────────────────────────────────────────────────────────
    df["open_interest_expansion"] = (oi24 > 1.5).astype(float).fillna(0.0)
    oi_up_mask = oi24 > 1.0
    df["oi_price_divergence"]  = (oi_up_mask & (mom24 < 0)).astype(float).fillna(0.0)
    df["oi_up_price_flat"]     = (oi_up_mask & (mom24.abs() < 0.005)).astype(float).fillna(0.0)
    df["oi_up_price_down"]     = (oi_up_mask & (mom24 < -0.005)).astype(float).fillna(0.0)

    # ── Fear & Greed ─────────────────────────────────────────────────────────
    df["fear_greed_extreme"] = (fg24 > 2.0).astype(float).fillna(0.0)

    # long_crowding_score est calculé dans compute_short_context_scores
    # On pose un placeholder NaN pour que la liste FEATURES_SHORT_GAMECHANGER
    # soit complète à la sortie de compute_all_short_features.
    df["long_crowding_score"] = np.nan

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 2. BREAKDOWN FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def compute_breakdown_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Identifie les structures de marché baissières et les pertes de supports.

    Colonnes ajoutées :
      breakdown_strength_24    — magnitude du move baissier 24h
      breakdown_strength_168   — magnitude du move baissier 168h
      below_vwap_4h            — bool : dist_vwap_pct < -0.005
      below_vwap_12h           — fraction des 12 dernières barres sous VWAP
      vwap_loss_event          — cross sous VWAP sur 2 barres consécutives
      below_ema20              — bool : Close < EMA20
      below_ema50              — bool : Close < EMA50
      below_ema200             — bool : proxy depuis ema_spread_50_200
      ema_stack_bearish        — bool : EMA20 < EMA50 < EMA200 (proxy)
      local_low_break_24       — bool : Close < min des 24h précédentes
      local_low_break_168      — bool : Close < min des 168h précédentes
      downside_vol_ratio_24    — proportion du volume baissier sur 24 barres
      rv_downside_24           — std des log returns négatifs sur 24 barres
      breakdown_score          — score composite [calculé dans scores]
    """
    df = df.copy()

    close    = _get_close(df)
    ema20, ema50, ema200 = _compute_emas(df)
    dist_vwap = _col(df, "dist_vwap_pct")
    above_vwap = _col(df, "above_vwap_4h")
    mom24      = _col(df, "mom_logret_24")
    ema_sp     = _col(df, "ema_spread_50_200")
    rv_ratio   = _col(df, "rv_ratio_24_72")

    # ── Magnitude du breakdown ────────────────────────────────────────────────
    df["breakdown_strength_24"]  = _safe((-mom24).clip(lower=0.0))

    if "mom_logret_168" in df.columns:
        mom168 = _col(df, "mom_logret_168")
    else:
        log_c  = np.log(close.clip(lower=_EPS))
        mom168 = _safe(log_c - log_c.shift(168))
    df["breakdown_strength_168"] = _safe((-mom168).clip(lower=0.0))

    # ── Position relative au VWAP ─────────────────────────────────────────────
    df["below_vwap_4h"]  = (dist_vwap < -0.005).astype(float).fillna(0.0)
    # Proportion des 12 dernières barres sous le VWAP
    # above_vwap_4h = fraction barres au-dessus ; 1 - above_vwap_4h = sous le VWAP
    if above_vwap.notna().any():
        df["below_vwap_12h"] = (
            (1.0 - above_vwap).rolling(12, min_periods=6).mean()
        ).fillna(0.0)
    else:
        below_vwap_flag = (dist_vwap < -0.005).astype(float)
        df["below_vwap_12h"] = below_vwap_flag.rolling(12, min_periods=6).mean().fillna(0.0)

    # Évènement : VWAP perdu sur 2 barres consécutives (pas de future — shift positif)
    bv4h = df["below_vwap_4h"]
    df["vwap_loss_event"] = ((bv4h > 0.5) & (bv4h.shift(1).fillna(0.0) > 0.5)).astype(float).fillna(0.0)

    # ── EMAs ─────────────────────────────────────────────────────────────────
    df["below_ema20"] = (close < ema20).astype(float).fillna(0.0)
    df["below_ema50"] = (close < ema50).astype(float).fillna(0.0)
    # proxy EMA200 via ema_spread_50_200 (négatif = EMA50 < EMA200 ≈ prix sous EMA200)
    df["below_ema200"]     = (ema_sp < -0.02).astype(float).fillna(0.0)
    # Stack bearish : EMA20 < EMA50 < EMA200
    df["ema_stack_bearish"] = (
        (ema20 < ema50) & (ema50 < ema200)
    ).astype(float).fillna(0.0)

    # ── Ruptures de supports locaux ───────────────────────────────────────────
    # On utilise shift(1) pour éviter d'inclure la barre courante dans le min
    df["local_low_break_24"]  = (
        close < close.rolling(24, min_periods=6).min().shift(1)
    ).astype(float).fillna(0.0)
    df["local_low_break_168"] = (
        close < close.rolling(168, min_periods=24).min().shift(1)
    ).astype(float).fillna(0.0)

    # ── Volatilité baissière ──────────────────────────────────────────────────
    log_ret = _safe(np.log(close.clip(lower=_EPS)).diff())

    # downside_vol_ratio_24 : proportion de la variance totale expliquée par les baisses
    neg_sq  = (log_ret.clip(upper=0.0) ** 2).rolling(24, min_periods=12).sum()
    tot_sq  = (log_ret ** 2).rolling(24, min_periods=12).sum().clip(lower=_EPS)
    df["downside_vol_ratio_24"] = _safe(neg_sq / tot_sq).fillna(0.5)

    # rv_downside_24 : std des log returns négatifs (estimation sur fenêtre glissante)
    # Vectorisé via rolling apply (pandas) — pas de boucle Python explicite sur les barres
    def _downside_std(x: pd.Series) -> float:
        neg = x[x < 0]
        return float(neg.std()) if len(neg) > 1 else 0.0

    df["rv_downside_24"] = log_ret.rolling(24, min_periods=12).apply(
        _downside_std, raw=False
    )

    # breakdown_score est calculé dans compute_short_context_scores
    df["breakdown_score"] = np.nan

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 3. FAILED BREAKOUT FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def compute_failed_breakout_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Détecte les faux cassages haussiers (bull traps) : prix dépasse le high
    puis retombe immédiatement.

    Colonnes ajoutées :
      failed_high_6             — bull trap sur la fenêtre 6h
      failed_high_12            — bull trap sur la fenêtre 12h
      failed_high_24            — bull trap sur la fenêtre 24h
      upper_wick_pct            — proportion de la mèche haute dans le range
      upper_wick_z_24           — z-score de upper_wick_pct sur 24 barres
      close_rejection_from_high — (High - Close) normé par range 24h
      volume_exhaustion_high    — volume z-score négatif quand nouveau high
      taker_buy_exhaustion      — ratio taker décroissant quand prix monte
      bull_trap_score           — score composite
      failed_breakout_score     — score composite [calculé dans scores]
    """
    df = df.copy()

    close  = _get_close(df)
    high   = _get_high(df)
    low    = _get_low(df)
    vol    = _get_volume(df)
    taker_z = _col(df, "taker_ls_buySellRatio_z_24")
    mom4    = _col(df, "mom_logret_4")

    # ── Failed high sur N barres ──────────────────────────────────────────────
    # Logique : Close[t] > max(Close, window=N) de [t-N..t-1]
    #           ET Close[t+2] < Close[t]          (retour rapide)
    # IMPORTANT : pour éviter le leakage, on ne peut pas regarder t+2.
    # On approche le signal de retournement via :
    #   close_at_high = bool (close dépasse le rolling high passé)
    #   ET la mèche haute immédiate est large (rejet déjà inscrit dans la barre)
    # Cette version est compatible live et sans lookahead.
    for window, col_name in [(6, "failed_high_6"), (12, "failed_high_12"), (24, "failed_high_24")]:
        prev_high = close.rolling(window, min_periods=max(3, window // 2)).max().shift(1)
        above_prev_high = close > prev_high                      # cassage
        # rejet intra-barre : mèche haute significative (> 30 % du range)
        bar_range    = (high - low).clip(lower=_EPS)
        wick_high    = (high - close).clip(lower=0.0)
        rejected     = (wick_high / bar_range) > 0.30
        df[col_name] = (above_prev_high & rejected).astype(float).fillna(0.0)

    # ── Mèche haute ───────────────────────────────────────────────────────────
    bar_range = (high - low).clip(lower=_EPS)
    wick_high = (high - close).clip(lower=0.0)
    df["upper_wick_pct"] = _safe(wick_high / bar_range)
    df["upper_wick_z_24"] = _local_zscore(df["upper_wick_pct"], window=24, min_periods=12)

    # ── Rejet depuis le high (normé par range 24h) ────────────────────────────
    low_24h  = low.rolling(24, min_periods=6).min().shift(1)
    denom    = (high - low_24h).clip(lower=_EPS)
    df["close_rejection_from_high"] = _safe((high - close) / denom)

    # ── Volume exhaustion au high ─────────────────────────────────────────────
    # z-score local du volume sur 24 barres
    vol_z = _local_zscore(vol, window=24, min_periods=12)
    new_high = high > high.rolling(24, min_periods=6).max().shift(1)
    # volume faible (z < 0) en même temps que nouveau high = épuisement
    df["volume_exhaustion_high"] = (new_high & (vol_z < 0.0)).astype(float).fillna(0.0)

    # ── Taker buy exhaustion ──────────────────────────────────────────────────
    # taker_ls_buySellRatio_z_24 décroissant quand le prix monte = divergence
    if taker_z.notna().any() and mom4.notna().any():
        taker_declining  = taker_z.diff().fillna(0.0) < 0.0
        price_rising     = mom4 > 0.002
        df["taker_buy_exhaustion"] = (taker_declining & price_rising).astype(float).fillna(0.0)
    else:
        df["taker_buy_exhaustion"] = np.nan

    # ── Bull trap score composite (pré-normalisation, avant context_scores) ───
    df["bull_trap_score"] = (
        0.35 * df["failed_high_12"].fillna(0.0)
        + 0.30 * df["upper_wick_pct"].fillna(0.0).clip(0.0, 1.0)
        + 0.20 * df["volume_exhaustion_high"].fillna(0.0)
        + 0.15 * df["taker_buy_exhaustion"].fillna(0.0)
    )

    # failed_breakout_score est calculé dans compute_short_context_scores
    df["failed_breakout_score"] = np.nan

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 4. LIQUIDITY STRESS FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def compute_liquidity_stress_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mesure la pression vendeuse, les expansions de range et la liquidité dégradée.

    Colonnes ajoutées :
      liq_long_spike_12        — proxy liquidations longs sur 12 barres
      liq_long_spike_24        — proxy liquidations longs sur 24 barres
      liq_imbalance_short      — proportion liquidité favorable au short
      spread_proxy             — (High - Low) / Close — proxy spread
      range_expansion_6        — (H-L) courant vs mean(H-L, 24h)
      range_expansion_24       — (H-L).mean(24) vs (H-L).mean(168) — tendance range
      downside_range_expansion — range expansion uniquement sur barres baissières
      sell_volume_shock        — z-score volume sur barres baissières
      taker_sell_cumul_12      — accumulation volume vendeur sur 12 barres
      taker_sell_pressure      — ratio sell volume z-scoré
    """
    df = df.copy()

    close   = _get_close(df)
    high    = _get_high(df)
    low     = _get_low(df)
    vol     = _get_volume(df)
    taker_z = _col(df, "taker_ls_buySellRatio_z_24")
    taker_b = _col(df, "taker_ls_imbalance")   # > 0 = buy dominant

    # ── Proxy liquidations longs ───────────────────────────────────────────────
    # Volume important + barre baissière = forced longs closing
    ret = _safe(np.log(close.clip(lower=_EPS)).diff())
    vol_z = _local_zscore(vol, window=24, min_periods=12)
    bear_bar  = (ret < 0).astype(float)
    liq_proxy = (vol_z * bear_bar).clip(lower=0.0)

    df["liq_long_spike_12"] = liq_proxy.rolling(12, min_periods=4).sum()
    df["liq_long_spike_24"] = liq_proxy.rolling(24, min_periods=8).sum()

    # ── Imbalance favorable au short ──────────────────────────────────────────
    # Si taker_ls_imbalance disponible (valeur négative = sell dominant)
    if taker_b.notna().any():
        sell_dom = (-taker_b).clip(lower=0.0)
        df["liq_imbalance_short"] = sell_dom.rolling(12, min_periods=4).mean()
    elif taker_z.notna().any():
        sell_dom = (-taker_z).clip(lower=0.0)
        df["liq_imbalance_short"] = sell_dom.rolling(12, min_periods=4).mean()
    else:
        df["liq_imbalance_short"] = np.nan

    # ── Spread proxy et range expansion ──────────────────────────────────────
    bar_range   = (high - low).clip(lower=_EPS)
    df["spread_proxy"] = _safe(bar_range / close.clip(lower=_EPS))

    mean_range_24  = bar_range.rolling(24, min_periods=8).mean().clip(lower=_EPS)
    mean_range_168 = bar_range.rolling(168, min_periods=24).mean().clip(lower=_EPS)

    df["range_expansion_6"]  = _safe(bar_range / mean_range_24)
    df["range_expansion_24"] = _safe(mean_range_24 / mean_range_168)

    # Expansion de range uniquement sur les barres baissières
    open_col = _col(df, "Open") if "Open" in df.columns else close.shift(1)
    bear_range = (bar_range * bear_bar).replace(0.0, np.nan)
    bear_range_mean_168 = bear_range.rolling(168, min_periods=24).mean().clip(lower=_EPS)
    df["downside_range_expansion"] = _safe(bar_range * bear_bar / bear_range_mean_168)
    df["downside_range_expansion"] = df["downside_range_expansion"].fillna(0.0)

    # ── Volume choc sur barres baissières ────────────────────────────────────
    # z-score du volume uniquement quand la barre est baissière
    sell_vol_shock = vol_z * bear_bar
    df["sell_volume_shock"] = sell_vol_shock.fillna(0.0)

    # ── Accumulation volume vendeur ───────────────────────────────────────────
    # Approximation sell_volume = Volume * (1 - taker_ratio)
    if "taker_buy_ratio_base" in df.columns:
        taker_buy_ratio = pd.to_numeric(df["taker_buy_ratio_base"], errors="coerce")
        sell_vol = vol * (1.0 - taker_buy_ratio.clip(0.0, 1.0))
    elif taker_b.notna().any():
        # taker_ls_imbalance ∈ [-1, 1] ; sell_vol ∝ (1 - imbalance) / 2
        sell_vol = vol * ((1.0 - taker_b.clip(-1.0, 1.0)) / 2.0)
    else:
        sell_vol = vol * bear_bar  # proxy minimal : volume sur barres baissières

    df["taker_sell_cumul_12"] = sell_vol.rolling(12, min_periods=4).sum()

    # z-score du ratio sell volume
    sell_ratio = (sell_vol / vol.clip(lower=_EPS)).clip(0.0, 1.0)
    df["taker_sell_pressure"] = _local_zscore(sell_ratio, window=24, min_periods=12).fillna(0.0)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 5. SQUEEZE RISK FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def compute_squeeze_risk_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mesure le risque de short-squeeze (signal NÉGATIF pour le short — à éviter).

    Colonnes ajoutées :
      positive_momentum_accel   — bool : mom_4 > mom_12 > 0
      price_above_vwap          — bool : dist_vwap_pct > 0.01
      funding_negative_squeeze  — bool : funding_z_24 < -1.5 (shorts payent)
      taker_buy_pressure        — bool : taker_ls_buySellRatio_z_24 > 1.0
      reclaim_vwap_event        — bool : Close franchit VWAP à la hausse
      rsi_recovery_from_oversold — bool : RSI monte depuis < 30
      short_late_entry_risk     — composite
      squeeze_risk_score        — score composite [calculé dans scores]
    """
    df = df.copy()

    mom4   = _col(df, "mom_logret_4")
    mom12  = _col(df, "mom_logret_12")
    dvwap  = _col(df, "dist_vwap_pct")
    fr24   = _col(df, "funding_rate_z_24")
    taker_z = _col(df, "taker_ls_buySellRatio_z_24")
    rsi    = _col(df, "rsi_14")

    # ── Momentum accel positif ────────────────────────────────────────────────
    df["positive_momentum_accel"] = (
        (mom4 > 0.002) & (mom12 > 0.002) & (mom4 > mom12)
    ).astype(float).fillna(0.0)

    # ── Prix au-dessus du VWAP ────────────────────────────────────────────────
    df["price_above_vwap"] = (dvwap > 0.01).astype(float).fillna(0.0)

    # ── Funding négatif — les shorts payent (danger pour short) ───────────────
    df["funding_negative_squeeze"] = (fr24 < -1.5).astype(float).fillna(0.0)

    # ── Pression acheteuse des takers ─────────────────────────────────────────
    df["taker_buy_pressure"] = (taker_z > 1.0).astype(float).fillna(0.0)

    # ── Reclaim VWAP : Close > 0 sur dist_vwap alors qu'il était < 0 ─────────
    vwap_cross_up = (dvwap > 0.0) & (dvwap.shift(1).fillna(dvwap) < 0.0)
    df["reclaim_vwap_event"] = vwap_cross_up.astype(float).fillna(0.0)

    # ── Récupération RSI depuis oversold ──────────────────────────────────────
    if rsi.notna().any():
        was_oversold = (rsi.shift(1) < 30.0).fillna(False)
        rsi_rising   = rsi > rsi.shift(1).fillna(rsi)
        df["rsi_recovery_from_oversold"] = (was_oversold & rsi_rising).astype(float).fillna(0.0)
    else:
        df["rsi_recovery_from_oversold"] = np.nan

    # ── Score de risque d'entrée short tardive ────────────────────────────────
    df["short_late_entry_risk"] = (
        0.30 * df["positive_momentum_accel"]
        + 0.25 * df["price_above_vwap"]
        + 0.25 * df["taker_buy_pressure"]
        + 0.20 * df["funding_negative_squeeze"]
    )

    # squeeze_risk_score est calculé dans compute_short_context_scores
    df["squeeze_risk_score"] = np.nan

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 6. SCORES COMPOSITES
# ─────────────────────────────────────────────────────────────────────────────

def compute_short_context_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les cinq scores composites à partir des features déjà présentes.

    Doit être appelé APRÈS les quatre compute_* ci-dessus.

    Scores produits :
      long_crowding_score       — 0.30 * z(fr24) + 0.25 * z(ls24) + 0.20 * z(oi24)
                                  + 0.15 * z(fg24) + 0.10 * z(vwap)
      breakdown_score           — 0.30 * below_vwap_4h + 0.25 * below_ema20
                                  + 0.20 * local_low_break_24 + 0.15 * taker_sell_pressure
                                  + 0.10 * rv_ratio_24_72
      squeeze_risk_score        — 0.35 * positive_momentum_accel + 0.25 * price_above_vwap
                                  + 0.20 * taker_buy_pressure + 0.20 * funding_negative_squeeze
      failed_breakout_score     — 0.30 * failed_high_12 + 0.25 * upper_wick_z_24
                                  + 0.20 * close_rejection_from_high
                                  + 0.15 * taker_buy_exhaustion + 0.10 * volume_exhaustion_high
      bear_continuation_score   — 0.30 * ema_stack_bearish + 0.25 * below_vwap_12h
                                  + 0.20 * max(0, -mom_logret_72)
                                  + 0.15 * downside_vol_ratio_24 + 0.10 * weak_bounce_score
    """
    df = df.copy()

    # ── Helpers locaux ────────────────────────────────────────────────────────
    def _c(name: str) -> pd.Series:
        return _col(df, name)

    def _z(name: str) -> pd.Series:
        return _local_zscore(_c(name))

    # ── long_crowding_score ───────────────────────────────────────────────────
    df["long_crowding_score"] = _safe(
        0.30 * _z("funding_rate_z_24")
        + 0.25 * _z("global_ls_longShortRatio_z_24")
        + 0.20 * _z("oihist_sumOpenInterest_z_24")
        + 0.15 * _z("fear_greed_value_z_24")
        + 0.10 * _z("dist_vwap_pct")
    )

    # ── breakdown_score ───────────────────────────────────────────────────────
    rv_ratio = _c("rv_ratio_24_72")
    rv_z     = _local_zscore(rv_ratio)  # z-score du ratio de vol (spike = bear)

    df["breakdown_score"] = _safe(
        0.30 * _c("below_vwap_4h").fillna(0.0)
        + 0.25 * _c("below_ema20").fillna(0.0)
        + 0.20 * _c("local_low_break_24").fillna(0.0)
        + 0.15 * _c("taker_sell_pressure").fillna(0.0)
        + 0.10 * rv_z.fillna(0.0)
    )

    # ── squeeze_risk_score ────────────────────────────────────────────────────
    df["squeeze_risk_score"] = _safe(
        0.35 * _c("positive_momentum_accel").fillna(0.0)
        + 0.25 * _c("price_above_vwap").fillna(0.0)
        + 0.20 * _c("taker_buy_pressure").fillna(0.0)
        + 0.20 * _c("funding_negative_squeeze").fillna(0.0)
    )

    # ── failed_breakout_score ─────────────────────────────────────────────────
    wick_z = _c("upper_wick_z_24").fillna(0.0).clip(-3.0, 3.0)
    rej    = _local_zscore(_c("close_rejection_from_high")).fillna(0.0).clip(-3.0, 3.0)
    df["failed_breakout_score"] = _safe(
        0.30 * _c("failed_high_12").fillna(0.0)
        + 0.25 * wick_z
        + 0.20 * rej
        + 0.15 * _c("taker_buy_exhaustion").fillna(0.0)
        + 0.10 * _c("volume_exhaustion_high").fillna(0.0)
    )

    # ── bear_continuation_score ───────────────────────────────────────────────
    mom72     = _c("mom_logret_72")
    mom72_neg = (-mom72).clip(lower=0.0)                          # max(0, -mom72)

    # weak_bounce_score : RSI < 40 après un rebond partiel
    rsi = _c("rsi_14")
    if rsi.notna().any():
        # rebond partiel : mom_logret_4 > 0 mais RSI encore faible
        mom4 = _c("mom_logret_4")
        partial_bounce  = (mom4 > 0.001) & (rsi < 40.0)
        weak_bounce_score = partial_bounce.astype(float).fillna(0.0)
    else:
        weak_bounce_score = pd.Series(0.0, index=df.index)

    df["weak_bounce_score"] = weak_bounce_score

    df["bear_continuation_score"] = _safe(
        0.30 * _c("ema_stack_bearish").fillna(0.0)
        + 0.25 * _c("below_vwap_12h").fillna(0.0)
        + 0.20 * mom72_neg.fillna(0.0)
        + 0.15 * _c("downside_vol_ratio_24").fillna(0.5)
        + 0.10 * weak_bounce_score
    )

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 7. POINT D'ENTRÉE PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def compute_all_short_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule toutes les features SHORT et retourne le DataFrame enrichi.

    Ordre d'appel garanti :
      1. compute_crowding_features          — foule extrême
      2. compute_breakdown_features         — structures baissières
      3. compute_failed_breakout_features   — bull traps
      4. compute_liquidity_stress_features  — pression vendeuse
      5. compute_squeeze_risk_features      — risque de squeeze
      6. compute_short_context_scores       — scores composites

    Toutes les features créées sont listées dans FEATURES_SHORT_GAMECHANGER.
    Le DataFrame d'entrée n'est pas modifié (copy systématique dans chaque sous-fonction).
    """
    df = compute_crowding_features(df)
    df = compute_breakdown_features(df)
    df = compute_failed_breakout_features(df)
    df = compute_liquidity_stress_features(df)
    df = compute_squeeze_risk_features(df)
    df = compute_short_context_scores(df)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# LISTE EXHAUSTIVE DES FEATURES CRÉÉES PAR CE MODULE
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_SHORT_GAMECHANGER: List[str] = [
    # ── Crowding ──────────────────────────────────────────────────────────────
    "funding_extreme_positive",
    "funding_accel_24",
    "funding_accel_72",
    "long_short_extreme",
    "open_interest_expansion",
    "oi_price_divergence",
    "oi_up_price_flat",
    "oi_up_price_down",
    "fear_greed_extreme",
    "long_crowding_score",
    # ── Breakdown ────────────────────────────────────────────────────────────
    "breakdown_strength_24",
    "breakdown_strength_168",
    "below_vwap_4h",
    "below_vwap_12h",
    "vwap_loss_event",
    "below_ema20",
    "below_ema50",
    "below_ema200",
    "ema_stack_bearish",
    "local_low_break_24",
    "local_low_break_168",
    "downside_vol_ratio_24",
    "rv_downside_24",
    "breakdown_score",
    # ── Failed breakout ───────────────────────────────────────────────────────
    "failed_high_6",
    "failed_high_12",
    "failed_high_24",
    "upper_wick_pct",
    "upper_wick_z_24",
    "close_rejection_from_high",
    "volume_exhaustion_high",
    "taker_buy_exhaustion",
    "bull_trap_score",
    "failed_breakout_score",
    # ── Liquidity stress ──────────────────────────────────────────────────────
    "liq_long_spike_12",
    "liq_long_spike_24",
    "liq_imbalance_short",
    "spread_proxy",
    "range_expansion_6",
    "range_expansion_24",
    "downside_range_expansion",
    "sell_volume_shock",
    "taker_sell_cumul_12",
    "taker_sell_pressure",
    # ── Squeeze risk ──────────────────────────────────────────────────────────
    "positive_momentum_accel",
    "price_above_vwap",
    "funding_negative_squeeze",
    "taker_buy_pressure",
    "reclaim_vwap_event",
    "rsi_recovery_from_oversold",
    "short_late_entry_risk",
    "squeeze_risk_score",
    # ── Scores composites ─────────────────────────────────────────────────────
    "weak_bounce_score",
    "bear_continuation_score",
]
