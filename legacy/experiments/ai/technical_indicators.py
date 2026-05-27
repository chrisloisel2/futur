"""
ai/level_0/technical_indicators.py — MODULES TECHNIQUES AVANCÉS
================================================================

Trois modules indépendants, chacun avec son propre "avis" sur le marché.
Chaque module produit des features orthogonales aux indicateurs existants
(momentum log-returns, EMA distances, VWAP) pour maximiser l'edge.

  compute_ichimoku_features(df)  → 12 features Nuage de Ichimoku
  compute_rsi_features(df)       →  8 features RSI multi-période + divergences
  compute_volume_features(df)    →  8 features analyse de volumes avancée

Convention anti-leakage universelle :
  - Toutes les fenêtres rolling utilisent uniquement les données [0..t].
  - Les Senkou Spans Ichimoku sont shiftés de +26 barres (calcul au passé).
  - Chikou = close[t] vs close[t-26], données historiques uniquement.
  - NaN de début de série gérés par fillna(0.0) en fin de chaque fonction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Helpers partagés
# ─────────────────────────────────────────────────────────────────────────────

def _get_ohlcv(df: pd.DataFrame):
    """Résout les noms de colonnes OHLCV (majuscules ou minuscules)."""
    c = "Close"  if "Close"  in df.columns else ("close"  if "close"  in df.columns else None)
    h = "High"   if "High"   in df.columns else ("high"   if "high"   in df.columns else None)
    l = "Low"    if "Low"    in df.columns else ("low"    if "low"    in df.columns else None)
    v = "Volume" if "Volume" in df.columns else ("volume" if "volume" in df.columns else None)
    return c, h, l, v


def _wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI méthode Wilder (EWM com = period-1) — identique à live_features."""
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.clip(lower=1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 1 — NUAGE DE ICHIMOKU
# ─────────────────────────────────────────────────────────────────────────────

ICHIMOKU_COLS = [
    "ichi_tenkan_dist",    # distance close / Tenkan-sen (conversion line 9)
    "ichi_kijun_dist",     # distance close / Kijun-sen  (base line 26)
    "ichi_cloud_dist_top", # distance close / dessus du nuage (extension ou résistance)
    "ichi_cloud_dist_bot", # distance close / dessous du nuage (support ou extension)
    "ichi_cloud_thickness",# épaisseur relative du nuage (force de la tendance)
    "ichi_above_cloud",    # 1 si close > cloud_top   → contexte haussier fort
    "ichi_below_cloud",    # 1 si close < cloud_bot   → contexte baissier fort
    "ichi_in_cloud",       # 1 si close dans le nuage → indécision / transition
    "ichi_cloud_bullish",  # 1 si Span A > Span B     → nuage vert (tendance haussière)
    "ichi_tk_bullish",     # 1 si Tenkan > Kijun      → momentum court terme haussier
    "ichi_tk_bearish",     # 1 si Tenkan < Kijun      → momentum court terme baissier
    "ichi_chikou_dist",    # (close - close[t-26]) / close[t-26] → Chikou span signal
]


def compute_ichimoku_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nuage de Ichimoku — 12 features sans aucun leakage futur.

    Architecture no-leakage :
      Tenkan (9) et Kijun (26) : midpoint rolling sur données passées ✓
      Span A/B : calculés au présent puis shiftés de +26 barres → équivaut
                 exactement au nuage affiché sur le graphe au moment t ✓
      Chikou   : close[t] vs close[t-26], données historiques uniquement ✓

    Interprétation pour les modèles :
      above_cloud + cloud_bullish + tk_bullish + chikou_dist > 0 = setup long fort
      below_cloud + cloud bearish + tk_bearish + chikou_dist < 0 = setup short fort
      in_cloud = zone d'indécision, attendre confirmation
    """
    df = df.copy()
    c_col, h_col, l_col, _ = _get_ohlcv(df)

    if any(x is None for x in [c_col, h_col, l_col]):
        for col in ICHIMOKU_COLS:
            df[col] = 0.0
        return df

    close = pd.to_numeric(df[c_col], errors="coerce")
    high  = pd.to_numeric(df[h_col], errors="coerce")
    low   = pd.to_numeric(df[l_col], errors="coerce")
    safe  = close.clip(lower=1e-9)

    # ── Tenkan-sen : midpoint des 9 dernières barres ──────────────────────────
    tenkan = (high.rolling(9, min_periods=5).max()
              + low.rolling(9, min_periods=5).min()) / 2

    # ── Kijun-sen : midpoint des 26 dernières barres ──────────────────────────
    kijun = (high.rolling(26, min_periods=13).max()
             + low.rolling(26, min_periods=13).min()) / 2

    # ── Senkou Span A : calculé maintenant, décalé de 26 → nuage actuel ───────
    # span_a.shift(26) at time t = (tenkan[t-26] + kijun[t-26]) / 2
    # Données utilisées : high/low[t-26-24:t-26] → passé uniquement ✓
    span_a = ((tenkan + kijun) / 2).shift(26)

    # ── Senkou Span B : midpoint 52 barres, décalé de 26 → nuage actuel ───────
    # Données utilisées : high/low[t-77:t-26] → passé uniquement ✓
    span_b = (
        (high.rolling(52, min_periods=26).max()
         + low.rolling(52, min_periods=26).min()) / 2
    ).shift(26)

    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)

    # ── Features dérivées ────────────────────────────────────────────────────
    df["ichi_tenkan_dist"]     = ((close - tenkan)    / safe).clip(-0.15, 0.15)
    df["ichi_kijun_dist"]      = ((close - kijun)     / safe).clip(-0.20, 0.20)
    df["ichi_cloud_dist_top"]  = ((close - cloud_top) / safe).clip(-0.30, 0.30)
    df["ichi_cloud_dist_bot"]  = ((close - cloud_bot) / safe).clip(-0.30, 0.30)
    df["ichi_cloud_thickness"] = ((cloud_top - cloud_bot) / safe).clip(0.0, 0.30)
    df["ichi_above_cloud"]     = (close > cloud_top).astype(float)
    df["ichi_below_cloud"]     = (close < cloud_bot).astype(float)
    df["ichi_in_cloud"]        = ((close >= cloud_bot) & (close <= cloud_top)).astype(float)
    df["ichi_cloud_bullish"]   = (span_a > span_b).astype(float)
    df["ichi_tk_bullish"]      = (tenkan > kijun).astype(float)
    df["ichi_tk_bearish"]      = (tenkan < kijun).astype(float)

    # Chikou : close actuel vs close il y a 26 barres — données passées ✓
    close_26 = close.shift(26).clip(lower=1e-9)
    df["ichi_chikou_dist"] = ((close - close_26) / close_26).clip(-0.30, 0.30)

    for col in ICHIMOKU_COLS:
        df[col] = df[col].fillna(0.0)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 2 — RSI ÉTENDU
# ─────────────────────────────────────────────────────────────────────────────

RSI_COLS = [
    "rsi_7",              # RSI rapide  7 barres  (surréactions intraday)
    "rsi_21",             # RSI lent   21 barres  (structure intermédiaire)
    "rsi_slope_6",        # pente RSI-14 sur 6 barres (accélération ou épuisement)
    "rsi_divergence_bull",# RSI monte plus vite que le prix → force cachée (long)
    "rsi_divergence_bear",# RSI baisse plus vite que le prix → faiblesse cachée (short)
    "rsi_oversold_bars",  # barres consécutives RSI < 30 (profondeur de l'oversold)
    "stoch_rsi_k",        # Stochastic RSI %K 14 barres (sensible aux extremes)
    "stoch_rsi_d",        # Stochastic RSI %D lissé sur 3 barres (signal line)
]


def compute_rsi_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    RSI multi-période, divergences prix/RSI et Stochastic RSI.

    Dépendances : rsi_14 doit déjà exister (compute_live_features) ou
                  la colonne close doit être présente pour le recalculer.

    Divergences (méthode normalisée 12 barres) :
      rsi_divergence_bull > 0  : RSI monte plus vite que le prix
                                 → force cachée, potentiel retournement long
      rsi_divergence_bear > 0  : RSI baisse plus vite que le prix
                                 → faiblesse cachée, potentiel retournement short

    Stochastic RSI :
      stoch_rsi_k = (RSI-14 - min(RSI-14, 14)) / (max - min) ∈ [0, 1]
      stoch_rsi_d = moyenne mobile 3 barres de %K (plus stable)
      k < 0.2 = oversold RSI → rebond potentiel
      k > 0.8 = overbought RSI → retournement potentiel
    """
    df = df.copy()
    c_col, _, _, _ = _get_ohlcv(df)

    if c_col is None and "rsi_14" not in df.columns:
        for col in RSI_COLS:
            df[col] = 0.0
        return df

    # RSI-14 existant ou recalculé
    if c_col is not None:
        close = pd.to_numeric(df[c_col], errors="coerce")
        rsi14 = df["rsi_14"] if "rsi_14" in df.columns else _wilder_rsi(close, 14)
        df["rsi_7"]  = _wilder_rsi(close, 7)
        df["rsi_21"] = _wilder_rsi(close, 21)
    else:
        close = None
        rsi14 = df["rsi_14"]
        df["rsi_7"]  = rsi14
        df["rsi_21"] = rsi14

    # ── Pente RSI-14 sur 6 barres ─────────────────────────────────────────────
    df["rsi_slope_6"] = rsi14 - rsi14.shift(6)

    # ── Divergences prix/RSI sur 12 barres ────────────────────────────────────
    # Méthode : normaliser les deux séries par leur propre écart type
    # rsi_divergence > 0 → RSI surperforme le prix (signal bull)
    # rsi_divergence < 0 → RSI sous-performe le prix (signal bear)
    if close is not None:
        rsi_mom_12   = (rsi14 - rsi14.shift(12)) / 20.0      # normé ≈ [-5, +5]
        price_ret_12 = np.log(close / close.shift(12).clip(lower=1e-9)) / 0.05  # normé ≈ [-6, +6]
        divergence   = rsi_mom_12 - price_ret_12
        df["rsi_divergence_bull"] = divergence.clip(0.0, 3.0)
        df["rsi_divergence_bear"] = (-divergence).clip(0.0, 3.0)
    else:
        df["rsi_divergence_bull"] = 0.0
        df["rsi_divergence_bear"] = 0.0

    # ── Barres consécutives en oversold (RSI < 30) ────────────────────────────
    oversold_mask = (rsi14 < 30).astype(int).values
    count = np.zeros(len(oversold_mask), dtype=np.float64)
    c = 0
    for i in range(len(oversold_mask)):
        c = c + 1 if oversold_mask[i] == 1 else 0
        count[i] = c
    df["rsi_oversold_bars"] = count

    # ── Stochastic RSI 14 barres ──────────────────────────────────────────────
    rsi_min14  = rsi14.rolling(14, min_periods=7).min()
    rsi_max14  = rsi14.rolling(14, min_periods=7).max()
    rsi_range  = (rsi_max14 - rsi_min14).clip(lower=0.5)
    stoch_k    = (rsi14 - rsi_min14) / rsi_range
    df["stoch_rsi_k"] = stoch_k
    df["stoch_rsi_d"] = stoch_k.rolling(3, min_periods=1).mean()

    for col in RSI_COLS:
        df[col] = df[col].fillna(0.0)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 3 — ANALYSE DE VOLUMES AVANCÉE
# ─────────────────────────────────────────────────────────────────────────────

VOLUME_COLS = [
    "obv_slope_12",    # pente OBV normée 12 barres (flux directionnel net)
    "cmf_20",          # Chaikin Money Flow 20 barres ∈ [-1, +1]
    "mfi_14",          # Money Flow Index 14 barres (RSI pondéré volume)
    "ad_slope_12",     # pente ligne Accumulation/Distribution (smart money)
    "vol_climax_buy",  # volume spike × close fort (effort → épuisement ou breakout)
    "vol_climax_sell", # volume spike × close faible (capitulation ou breakdown)
    "vol_dry_up_12",   # fraction barres à faible volume sur 12 barres (consolidation)
    "vol_delta_accel", # accélération du delta taker (changement de pression nette)
]


def compute_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Analyse avancée des volumes : flux, money flow, OBV, climax, A/D.

    Colonnes ajoutées :
      obv_slope_12   — (OBV[t] - OBV[t-12]) / vol_mean : direction du flux net
                       positif = accumulation, négatif = distribution
      cmf_20         — Chaikin Money Flow : pression acheteuse vs vendeuse
                       [-1, +1] pondéré par la position dans le range de la barre
      mfi_14         — Money Flow Index : RSI appliqué au (prix typique × volume)
                       [0, 100] — proxy d'épuisement similaire au RSI mais volumétrique
      ad_slope_12    — pente de la ligne A/D de Chaikin normée par le volume
                       capte la pression institutionnelle même sans vol spike
      vol_climax_buy — z-score(vol) × z-score(ret positif)
                       spike = effort haussier excessif → potentiel retournement short
      vol_climax_sell— z-score(vol) × z-score(ret négatif)
                       spike = effort baissier excessif → potentiel retournement long
      vol_dry_up_12  — fraction de barres < 60% du volume médian sur 12 barres
                       valeur élevée = consolidation silencieuse avant breakout
      vol_delta_accel— différence EMA6/EMA12 du delta taker
                       capte les changements de régime acheteur/vendeur
    """
    df = df.copy()
    c_col, h_col, l_col, v_col = _get_ohlcv(df)

    if any(x is None for x in [c_col, h_col, l_col, v_col]):
        for col in VOLUME_COLS:
            df[col] = 0.0
        return df

    close = pd.to_numeric(df[c_col], errors="coerce")
    high  = pd.to_numeric(df[h_col], errors="coerce")
    low   = pd.to_numeric(df[l_col], errors="coerce")
    vol   = pd.to_numeric(df[v_col], errors="coerce").clip(lower=1e-9)

    # Fenêtre de normalisation commune
    vol_mean_12 = vol.rolling(12, min_periods=6).mean().clip(lower=1e-9)
    hl_range    = (high - low).clip(lower=1e-9)

    # ── OBV (On-Balance Volume) — pente normée ────────────────────────────────
    # OBV cumule le volume des barres haussières, soustrait celui des baissières.
    direction = np.sign(close.diff().fillna(0.0))
    obv = (direction * vol).cumsum()
    df["obv_slope_12"] = ((obv - obv.shift(12)) / (vol_mean_12 * 12)).clip(-3.0, 3.0)

    # ── Chaikin Money Flow (CMF 20 barres) ────────────────────────────────────
    # MFM = (2C - H - L) / (H - L) → position de la clôture dans le range [-1, +1]
    # CMF = Σ(MFM × vol, 20) / Σ(vol, 20)
    mfm = (2 * close - high - low) / hl_range
    mfv = mfm * vol
    df["cmf_20"] = (
        mfv.rolling(20, min_periods=10).sum()
        / vol.rolling(20, min_periods=10).sum().clip(lower=1e-9)
    ).clip(-1.0, 1.0)

    # ── Money Flow Index (MFI 14 barres) ─────────────────────────────────────
    # MFI = 100 × POS_MF / (POS_MF + NEG_MF), où MF = typical_price × volume
    # Analogue au RSI mais pondéré par le volume → divergences vol/prix.
    tp     = (high + low + close) / 3.0
    raw_mf = tp * vol
    tp_up  = raw_mf.where(tp.diff() > 0, 0.0).rolling(14, min_periods=7).sum()
    tp_dn  = raw_mf.where(tp.diff() <= 0, 0.0).rolling(14, min_periods=7).sum()
    total  = (tp_up + tp_dn).clip(lower=1e-9)
    df["mfi_14"] = (100.0 * tp_up / total).clip(0.0, 100.0)

    # ── Accumulation / Distribution — pente normée ───────────────────────────
    # CLV = (2C - H - L) / (H - L) : localisation de la clôture dans le range
    # A/D = cumsum(CLV × vol) → capte la pression smart money sans spike de vol.
    clv = mfm  # identique au MFM du CMF
    ad  = (clv * vol).cumsum()
    df["ad_slope_12"] = ((ad - ad.shift(12)) / (vol_mean_12 * 12)).clip(-3.0, 3.0)

    # ── Volume Climax ─────────────────────────────────────────────────────────
    # Produit des z-scores de vol et de return → détecte les barres "effort extrême".
    # Climax buy  (vol spike + close up)   : effort haussier excessif = épuisement
    # Climax sell (vol spike + close down) : effort baissier excessif = capitulation
    vol_std  = vol.rolling(24, min_periods=12).std().clip(lower=1e-9)
    vol_mean = vol.rolling(24, min_periods=12).mean()
    vol_z    = (vol - vol_mean) / vol_std

    log_ret  = np.log(close / close.shift(1))
    ret_std  = log_ret.rolling(24, min_periods=12).std().clip(lower=1e-9)
    ret_mean = log_ret.rolling(24, min_periods=12).mean()
    ret_z    = (log_ret - ret_mean) / ret_std

    df["vol_climax_buy"]  = (vol_z * ret_z.clip(lower=0.0)).clip(0.0, 5.0)
    df["vol_climax_sell"] = (vol_z * (-ret_z).clip(lower=0.0)).clip(0.0, 5.0)

    # ── Volume sec / consolidation ────────────────────────────────────────────
    # Fraction de barres à faible volume sur 12 barres.
    # Valeur élevée → marché en pause = souvent précurseur d'un mouvement.
    vol_med_24  = vol.rolling(24, min_periods=12).median().clip(lower=1e-9)
    low_vol_bar = (vol < 0.6 * vol_med_24).astype(float)
    df["vol_dry_up_12"] = low_vol_bar.rolling(12, min_periods=6).mean()

    # ── Accélération delta taker ──────────────────────────────────────────────
    # Différence entre EMA rapide et EMA lente du delta taker.
    # Capte les changements de régime acheteur/vendeur avant que le prix bouge.
    if "delta_taker_pressure" in df.columns:
        dtp = df["delta_taker_pressure"]
        df["vol_delta_accel"] = (
            dtp.rolling(6, min_periods=3).mean()
            - dtp.rolling(12, min_periods=6).mean()
        ).clip(-1.0, 1.0)
    else:
        df["vol_delta_accel"] = 0.0

    for col in VOLUME_COLS:
        df[col] = df[col].fillna(0.0)

    return df
