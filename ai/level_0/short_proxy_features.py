"""
level_0/short_proxy_features.py — FEATURES PROXY MACRO POUR LE SHORT
======================================================================

Synthétise les proxies des features macro manquantes dans les CSV
(funding rate, open interest, fear & greed) à partir des colonnes
OHLCV + taker disponibles.

Cinq proxies principaux :
  1. funding_rate_proxy      — déséquilibre taker accumulé ≈ funding perpetuel
  2. oi_proxy_z              — volume × RV z-scoré ≈ OI normalisé
  3. fear_greed_proxy        — composite momentum / volatilité / RSI / skew
  4. cross_asset_momentum    — momentum relatif long terme ≈ dominance BTC
  5. crowding_pressure       — score composite de crowding haussier

Features dérivées additionnelles :
  6. funding_accel_proxy     — accélération du funding (diff 12h)
  7. funding_extreme_proxy   — bool, funding_proxy > 0.6
  8. oi_expansion_proxy      — bool, OI proxy > percentile 75 rolling
  9. fear_greed_extreme_proxy — bool, fear_greed_proxy > 0.75

Conventions :
  - Tout vectorisé (aucune boucle Python explicite sur les barres)
  - NaN-safe et inf-safe (replace + clip systématique)
  - Aucun leakage : pas de shift(-n) ni de données futures
  - rolling avec min_periods = max(window // 3, 1)
  - Z-scores locaux : rolling(168).mean / rolling(168).std(min_periods=24)
  - Clipping z-scores : np.clip(result, -5, 5)
  - Booleans : astype(float) avant de retourner
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List

# ─────────────────────────────────────────────────────────────────────────────
# Constantes internes
# ─────────────────────────────────────────────────────────────────────────────

_ZSCORE_WINDOW: int = 168      # fenêtre z-score local (7 jours en données horaires)
_ZSCORE_MIN_PER: int = 24      # min_periods pour le z-score
_EPS: float = 1e-9             # éviter les divisions par zéro


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────

def _safe(s: pd.Series) -> pd.Series:
    """Remplace inf/-inf par NaN."""
    return s.replace([np.inf, -np.inf], np.nan)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """Retourne la colonne ou une série de NaN si absente."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _local_zscore(
    s: pd.Series,
    window: int = _ZSCORE_WINDOW,
    min_periods: int = _ZSCORE_MIN_PER,
) -> pd.Series:
    """
    Z-score rolling local, clippé [-5, 5], NaN-safe et inf-safe.
    std divisée par clip(lower=_EPS) pour éviter la division par zéro.
    """
    mu = s.rolling(window, min_periods=min_periods).mean()
    sigma = s.rolling(window, min_periods=min_periods).std(ddof=1)
    z = (s - mu) / sigma.clip(lower=_EPS)
    z = _safe(z)
    return np.clip(z, -5.0, 5.0)


def _rolling_mean(s: pd.Series, window: int) -> pd.Series:
    """Rolling mean avec min_periods = max(window // 3, 1)."""
    mp = max(window // 3, 1)
    return s.rolling(window, min_periods=mp).mean()


def _rolling_std(s: pd.Series, window: int) -> pd.Series:
    """Rolling std avec min_periods = max(window // 3, 1)."""
    mp = max(window // 3, 1)
    return s.rolling(window, min_periods=mp).std(ddof=1)


# ─────────────────────────────────────────────────────────────────────────────
# 1. FUNDING RATE PROXY
# ─────────────────────────────────────────────────────────────────────────────

def compute_funding_rate_proxy(df: pd.DataFrame) -> pd.Series:
    """
    Proxy du funding rate = déséquilibre taker accumulé.

    Funding positif = longs paient = foule longée.

    Stratégie :
      - Primaire : rolling_mean(delta_taker_pressure, 24) normalisé [-1, +1]
      - Fallback : (taker_buy_ratio_base - 0.5) * 2, smoothed 24h

    Retourne une Series [-1, +1] clippée, NaN-safe.
    """
    delta = _col(df, "delta_taker_pressure")
    tbr = _col(df, "taker_buy_ratio_base")

    if delta.notna().sum() >= 24:
        # Voie primaire : rolling mean du delta de pression taker sur 24h
        raw = _rolling_mean(delta, 24)
        # Normaliser par rolling std 168h pour obtenir un signal [-1, +1]
        sigma = _rolling_std(raw, _ZSCORE_WINDOW).clip(lower=_EPS)
        mu = raw.rolling(_ZSCORE_WINDOW, min_periods=_ZSCORE_MIN_PER).mean()
        proxy = (raw - mu) / sigma
        proxy = _safe(proxy)
        # Ramener en [-1, +1] via tanh (plus doux que simple clip)
        proxy = np.tanh(proxy / 2.0)
    elif tbr.notna().sum() >= 24:
        # Fallback : taker_buy_ratio_base centré et lissé
        raw = _rolling_mean((tbr - 0.5) * 2.0, 24)
        proxy = raw.clip(-1.0, 1.0)
        proxy = _safe(proxy)
    else:
        proxy = pd.Series(np.nan, index=df.index)

    proxy = proxy.clip(-1.0, 1.0)
    return proxy.rename("funding_rate_proxy")


# ─────────────────────────────────────────────────────────────────────────────
# 2. OPEN INTEREST PROXY
# ─────────────────────────────────────────────────────────────────────────────

def compute_oi_proxy(df: pd.DataFrame) -> pd.Series:
    """
    Proxy de l'open interest = volume × volatilité réalisée.

    OI élevé = positions ouvertes = crowding potentiel.

    Formule : rolling_mean(Volume * rv_24, 48) z-scored 168h
    Retourne un z-score clippé [-5, 5].
    """
    volume = _col(df, "Volume")
    rv24 = _col(df, "rv_24")

    # Produit volume × RV (proxy de la liquidité engagée)
    raw = _safe(volume * rv24)
    # Lissage 48h
    smoothed = _rolling_mean(raw, 48)
    # Z-score local 168h
    z = _local_zscore(smoothed, window=_ZSCORE_WINDOW, min_periods=_ZSCORE_MIN_PER)
    return z.rename("oi_proxy_z")


# ─────────────────────────────────────────────────────────────────────────────
# 3. FEAR & GREED PROXY
# ─────────────────────────────────────────────────────────────────────────────

def compute_fear_greed_proxy(df: pd.DataFrame) -> pd.Series:
    """
    Proxy du Fear & Greed = composite de :
      - zscore_close_24  (momentum)              poids 0.35
      - rv_ratio_24_72   (volatilité relative)   poids 0.25
      - rsi_14 normé [0, 1]                      poids 0.25
      - skew_ret_24      (asymétrie returns)      poids 0.15

    Greed élevé (proche de 1) = marché en surachat = signal short potentiel.
    Retourne un score [0, 1] NaN-safe.
    """
    # Composante 1 : momentum normé [0, 1]
    zc24 = _col(df, "zscore_close_24")
    momentum_norm = pd.Series(np.nan, index=df.index)
    if zc24.notna().any():
        # zscore_close_24 est déjà un z-score : on le ramène [0,1] via sigmoid
        momentum_norm = 1.0 / (1.0 + np.exp(-zc24.clip(-5.0, 5.0)))

    # Composante 2 : volatilité relative normée [0, 1]
    rv_ratio = _col(df, "rv_ratio_24_72")
    vol_norm = pd.Series(np.nan, index=df.index)
    if rv_ratio.notna().any():
        rv_z = _local_zscore(rv_ratio, window=_ZSCORE_WINDOW, min_periods=_ZSCORE_MIN_PER)
        vol_norm = 1.0 / (1.0 + np.exp(-rv_z))

    # Composante 3 : RSI normé [0, 1]
    rsi = _col(df, "rsi_14")
    rsi_norm = pd.Series(np.nan, index=df.index)
    if rsi.notna().any():
        rsi_norm = (rsi.clip(0.0, 100.0) / 100.0)

    # Composante 4 : skew normé [0, 1] — skew négatif = fear, positif = greed
    skew = _col(df, "skew_ret_24")
    skew_norm = pd.Series(np.nan, index=df.index)
    if skew.notna().any():
        skew_z = _local_zscore(skew, window=_ZSCORE_WINDOW, min_periods=_ZSCORE_MIN_PER)
        skew_norm = 1.0 / (1.0 + np.exp(-skew_z))

    # Score composite pondéré — NaN-safe : on somme uniquement les composantes disponibles
    weights = [
        (0.35, momentum_norm),
        (0.25, vol_norm),
        (0.25, rsi_norm),
        (0.15, skew_norm),
    ]

    # Pondération adaptative si certaines composantes sont NaN
    total_weight = pd.Series(0.0, index=df.index)
    total_score = pd.Series(0.0, index=df.index)
    for w, comp in weights:
        mask = comp.notna()
        total_score = total_score + comp.fillna(0.0) * w
        total_weight = total_weight + mask.astype(float) * w

    proxy = _safe(total_score / total_weight.clip(lower=_EPS))
    proxy = proxy.clip(0.0, 1.0)
    return proxy.rename("fear_greed_proxy")


# ─────────────────────────────────────────────────────────────────────────────
# 4. CROSS-ASSET MOMENTUM
# ─────────────────────────────────────────────────────────────────────────────

def compute_cross_asset_momentum(df: pd.DataFrame) -> pd.Series:
    """
    Proxy de la dominance BTC = momentum relatif cross-asset.

    En pratique, utilise mom_logret_72 z-scoré sur 168h.
    Signal fort positif = BTC sur-performe = alt season moins probable.
    Signal fort négatif = BTC sous-performe = potentiellement favorable short BTC.

    Retourne un z-score clippé [-5, 5].
    """
    mom72 = _col(df, "mom_logret_72")

    if mom72.notna().sum() < 24:
        # Fallback : construire depuis mom_logret_24 + mom_logret_48
        mom24 = _col(df, "mom_logret_24")
        mom48 = _col(df, "mom_logret_48") if "mom_logret_48" in df.columns else pd.Series(np.nan, index=df.index)
        # Approximation : mom72 ≈ mom24 × 3 lissé (meilleur que rien)
        mom72 = _rolling_mean(mom24, 3) if mom24.notna().sum() >= 3 else pd.Series(np.nan, index=df.index)

    z = _local_zscore(mom72, window=_ZSCORE_WINDOW, min_periods=_ZSCORE_MIN_PER)
    return z.rename("cross_asset_momentum")


# ─────────────────────────────────────────────────────────────────────────────
# 5. CROWDING PRESSURE
# ─────────────────────────────────────────────────────────────────────────────

def compute_crowding_pressure(
    df: pd.DataFrame,
    funding_proxy: pd.Series,
    oi_proxy_z: pd.Series,
    fear_greed_proxy: pd.Series,
) -> pd.Series:
    """
    Score de crowding synthétique composite :

      crowding = 0.40 × z(funding_proxy)
               + 0.30 × z(taker_buy_cumul_12)
               + 0.20 × z(fear_greed_proxy)
               + 0.10 × oi_proxy_z

    Valeur haute = longs overcrowdés = signal short.

    Les composantes sont z-scorées localement avant combinaison pour
    garantir des contributions équilibrées.
    """
    # Z-score local de chaque composante
    funding_z = _local_zscore(funding_proxy, window=_ZSCORE_WINDOW, min_periods=_ZSCORE_MIN_PER)

    tbc12 = _col(df, "taker_buy_cumul_12")
    tbc12_z = _local_zscore(tbc12, window=_ZSCORE_WINDOW, min_periods=_ZSCORE_MIN_PER)

    fg_z = _local_zscore(fear_greed_proxy, window=_ZSCORE_WINDOW, min_periods=_ZSCORE_MIN_PER)

    # oi_proxy_z est déjà z-scoré
    oi_z = oi_proxy_z.copy()
    oi_z = np.clip(_safe(oi_z), -5.0, 5.0)

    # Score composite pondéré — NaN-safe
    weights = [
        (0.40, funding_z),
        (0.30, tbc12_z),
        (0.20, fg_z),
        (0.10, oi_z),
    ]

    total_weight = pd.Series(0.0, index=df.index)
    total_score = pd.Series(0.0, index=df.index)
    for w, comp in weights:
        mask = comp.notna()
        total_score = total_score + comp.fillna(0.0) * w
        total_weight = total_weight + mask.astype(float) * w

    crowding = _safe(total_score / total_weight.clip(lower=_EPS))
    crowding = np.clip(crowding, -5.0, 5.0)
    return crowding.rename("crowding_pressure")


# ─────────────────────────────────────────────────────────────────────────────
# 6. POINT D'ENTRÉE PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def compute_all_proxy_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ajoute toutes les features proxy macro au DataFrame.

    Colonnes ajoutées :
      funding_rate_proxy       — proxy du funding rate [-1, +1]
      oi_proxy_z               — proxy OI z-scoré [-5, +5]
      fear_greed_proxy         — proxy Fear & Greed [0, 1]
      cross_asset_momentum     — momentum relatif z-scoré [-5, +5]
      crowding_pressure        — score de crowding composite z-scoré [-5, +5]
      funding_accel_proxy      — accélération du funding (diff 12h)
      funding_extreme_proxy    — bool (float), funding_proxy > 0.6
      oi_expansion_proxy       — bool (float), OI proxy > p75 rolling
      fear_greed_extreme_proxy — bool (float), fear_greed_proxy > 0.75

    Le DataFrame d'entrée n'est pas modifié (copy).
    Aucun leakage : seuls des shifts positifs (passé) sont utilisés.
    """
    df = df.copy()

    # ── Proxies primaires ─────────────────────────────────────────────────────
    funding_proxy = compute_funding_rate_proxy(df)
    oi_proxy_z = compute_oi_proxy(df)
    fear_greed_proxy = compute_fear_greed_proxy(df)
    cross_asset_momentum = compute_cross_asset_momentum(df)
    crowding_pressure = compute_crowding_pressure(df, funding_proxy, oi_proxy_z, fear_greed_proxy)

    df["funding_rate_proxy"] = funding_proxy
    df["oi_proxy_z"] = oi_proxy_z
    df["fear_greed_proxy"] = fear_greed_proxy
    df["cross_asset_momentum"] = cross_asset_momentum
    df["crowding_pressure"] = crowding_pressure

    # ── Features dérivées ─────────────────────────────────────────────────────

    # Accélération du funding : différence sur 12 barres (passé uniquement)
    funding_accel = _safe(funding_proxy - funding_proxy.shift(12))
    df["funding_accel_proxy"] = np.clip(funding_accel, -2.0, 2.0)

    # Signal extrême de funding : funding_proxy > 0.6 (foule fortement longée)
    df["funding_extreme_proxy"] = (funding_proxy > 0.6).astype(float)

    # OI expansion : OI proxy z-score > percentile 75 rolling sur 168 barres
    # Utilisation du quantile rolling pour un seuil adaptatif
    oi_q75 = oi_proxy_z.rolling(
        _ZSCORE_WINDOW, min_periods=max(_ZSCORE_WINDOW // 3, 1)
    ).quantile(0.75)
    df["oi_expansion_proxy"] = (oi_proxy_z > oi_q75).astype(float)

    # Fear & Greed extrême : > 0.75 (zone greed extrême = surachat)
    df["fear_greed_extreme_proxy"] = (fear_greed_proxy > 0.75).astype(float)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# LISTE DES FEATURES CRÉÉES PAR CE MODULE
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_SHORT_PROXY: List[str] = [
    "funding_rate_proxy",
    "oi_proxy_z",
    "fear_greed_proxy",
    "cross_asset_momentum",
    "crowding_pressure",
    "funding_accel_proxy",
    "funding_extreme_proxy",
    "oi_expansion_proxy",
    "fear_greed_extreme_proxy",
]
