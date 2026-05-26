"""
institutional_features.py — Sélection de features depuis ohlcv_institutional_features_btcusdt
==============================================================================================

Principes de sélection :
  1. Fill ≥ 80% confirmé par audit mid-dataset (2019-2024)
  2. Un représentant par famille : pas de doublon sémantique
  3. Séparation LONG / SHORT pour capturer l'asymétrie directionnelle
  4. Présence garantie : get_available_features() filtre les absentes au runtime

Architecture :
  FEATURES_INST_COMMON  : contexte de marché neutre — filtre, long et short
  FEATURES_INST_LONG    : COMMON + signaux momentum / breakout / haussiers
  FEATURES_INST_SHORT   : COMMON + signaux reversal / breakdown / baissiers
  FEATURES_INST_FILTER  : sous-ensemble direction-agnostique pour le filtre Stage 1
  FEATURES_INST_REGIME  : classification bull/bear/neutral (niveau 1)
"""
from __future__ import annotations

import logging
from typing import List

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES COMMUNES (contexte marché, direction-agnostique)
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_INST_COMMON: List[str] = [
    # ── Momentum court terme ──────────────────────────────────────────────────
    "return_5",
    "return_10",
    "return_20",
    "log_return_5",
    "log_return_10",

    # ── Volatilité (estimateurs institutionnels) ──────────────────────────────
    "garman_klass_vol_20",      # 8× plus efficace que close-to-close
    "yang_zhang_vol_20",        # gère les overnight gaps
    "realized_vol_20",
    "atr_pct_20",               # ATR normalisé par le prix
    "bb_width_20",              # étendue des bandes de Bollinger

    # ── Position dans la range ────────────────────────────────────────────────
    "bb_percent_b_20",          # position dans les BB [0-1]
    "close_position_in_range",  # position dans la range du bar courant
    "body_to_range",            # force de la bougie (corps/range)
    "high_low_range_pct",       # amplitude relative du bar

    # ── Trend et EMA ──────────────────────────────────────────────────────────
    "distance_ema_20",          # % distance à la EMA20
    "distance_ema_50",          # % distance à la EMA50
    "ema_21_50_spread",         # alignement EMA moyen terme
    "ema_slope_20",             # pente de la EMA20 (direction)

    # ── MACD ─────────────────────────────────────────────────────────────────
    "macd_hist",                # MACD histogram
    "macd_hist_slope",          # accélération du MACD

    # ── Oscillateurs ─────────────────────────────────────────────────────────
    "rsi_13",                   # RSI 14-period équivalent (EWM alpha=1/13)
    "rsi_20",                   # RSI 20-period (moyen terme)
    "stoch_k_20",               # Stochastique %K

    # ── Force de tendance ────────────────────────────────────────────────────
    "adx_20",                   # ADX (force, pas direction)
    "di_spread_20",             # +DI − −DI (direction nette)
    "choppiness_20",            # trending vs ranging
    "efficiency_ratio_20",      # efficience directionnelle du prix

    # ── Volume / flow ─────────────────────────────────────────────────────────
    "volume_ratio_20",          # volume relatif vs moyenne 20 barres
    "cmf_20",                   # Chaikin Money Flow (accumulation / distribution)
    "obv_slope_20",             # pente de l'OBV

    # ── Scores composites (synthèse multi-indicateurs) ────────────────────────
    "trend_score",
    "momentum_score",
    "volatility_score",
    "market_quality_score",

    # ── Temporels (encodage cyclique) ─────────────────────────────────────────
    "hour_sin", "hour_cos",
    "dow_sin",  "dow_cos",
    "session_europe",
    "session_us",
]


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES LONG (additionnelles) — signaux haussiers
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_INST_LONG_EXTRA: List[str] = [
    # Momentum multi-horizon
    "return_50",
    "log_return_20",
    "return_accel_5",           # accélération du momentum (2e dérivée)
    "return_accel_10",

    # Structure de tendance long terme
    "distance_ema_200",         # position vs EMA200 (régime macro)
    "ema_50_200_spread",        # golden/death cross long terme
    "regression_slope_50",      # tendance linéaire sur 50 barres
    "regression_r2_50",         # qualité de la tendance linéaire

    # Breakout et Donchian
    "breakout_high_20",         # close > rolling_high récent
    "breakout_high_50",
    "donchian_position_20",     # position dans le canal Donchian
    "donchian_position_50",

    # Patterns haussiers
    "hammer_score",             # bougie marteau (support)
    "lower_wick_to_range",      # mèche basse = pression d'achat

    # Pressions directionnelles
    "di_plus_20",               # +DI (pression haussière)
    "di_plus_50",

    # Statistiques de queue
    "return_skew_20",           # asymétrie de distribution
    "return_kurt_20",           # kurtosis (fat tails)

    # Vol directionnelle
    "upside_vol_10",            # volatilité des up-moves
    "upside_vol_20",

    # Volume haussier
    "mfi_20",                   # Money Flow Index
    "dollar_volume_ratio_20",   # dollar volume relatif

    # Régime de tendance
    "hurst_proxy_50",           # tendance persistante vs mean-reverting
    "hurst_proxy_100",
    "current_runup_50",         # run-up depuis bas récent
    "liquidity_score",          # liquidité (institutionnels actifs = signal)

    # Résistance / support
    "distance_low_20",          # distance du bas récent
    "distance_low_50",

    # MTF 4h — perspective intermédiaire
    "mtf_4h_adx_20",
    "mtf_4h_adx_10",
    "mtf_4h_ema_distance_20",
    "mtf_4h_rsi_10",
    "mtf_4h_return_5",
    "mtf_4h_donchian_position_20",
    "mtf_4h_realized_vol_10",

    # MTF 1d — perspective journalière
    "mtf_1d_return_5",
    "mtf_1d_adx_5",
    "mtf_1d_ema_distance_5",
    "mtf_1d_rsi_5",
    "mtf_1d_donchian_position_5",
]

FEATURES_INST_LONG: List[str] = FEATURES_INST_COMMON + FEATURES_INST_LONG_EXTRA


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES SHORT (additionnelles) — signaux baissiers
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_INST_SHORT_EXTRA: List[str] = [
    # Momentum multi-horizon
    "return_50",
    "log_return_20",
    "return_accel_5",
    "return_accel_10",

    # Structure de tendance long terme
    "distance_ema_200",
    "ema_50_200_spread",
    "regression_slope_50",
    "regression_r2_50",

    # Breakdown et distance des hauts
    "breakdown_low_20",         # close < rolling_low récent
    "breakdown_low_50",
    "distance_high_20",         # distance du haut récent (retournement)
    "distance_high_50",
    "current_drawdown_50",      # drawdown depuis le récent high

    # Patterns baissiers
    "shooting_star_score",      # bougie étoile filante (résistance)
    "upper_wick_to_range",      # mèche haute = pression de vente

    # Pressions directionnelles
    "di_minus_20",              # −DI (pression baissière)
    "di_minus_50",

    # Statistiques de queue
    "return_skew_20",
    "return_kurt_20",

    # Vol directionnelle (fear premium)
    "downside_vol_10",          # volatilité des down-moves
    "downside_vol_20",

    # Volume baissier / distribution
    "mfi_20",
    "dollar_volume_ratio_20",

    # Bruit et épuisement de tendance
    "noise_to_signal_20",       # bruit vs signal (marché épuisé)
    "reversal_score",           # score composite de retournement

    # Résistance
    "distance_high_100",        # far from highs = accroche short rare
    "distance_high_200",

    # MTF 4h — perspective intermédiaire
    "mtf_4h_adx_20",
    "mtf_4h_adx_10",
    "mtf_4h_ema_distance_20",
    "mtf_4h_rsi_10",
    "mtf_4h_return_5",
    "mtf_4h_donchian_position_20",
    "mtf_4h_realized_vol_10",

    # MTF 1d — perspective journalière
    "mtf_1d_return_5",
    "mtf_1d_adx_5",
    "mtf_1d_ema_distance_5",
    "mtf_1d_rsi_5",
    "mtf_1d_donchian_position_5",
]

FEATURES_INST_SHORT: List[str] = FEATURES_INST_COMMON + FEATURES_INST_SHORT_EXTRA


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES FILTRE — Stage 1 (direction-agnostique, détecte les barres actives)
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_INST_FILTER: List[str] = [
    "return_5", "return_10", "return_20",
    "garman_klass_vol_20", "yang_zhang_vol_20",
    "atr_pct_20",
    "bb_width_20",
    "choppiness_20",
    "efficiency_ratio_20",
    "volume_ratio_20",
    "obv_slope_20",
    "adx_20",
    "market_quality_score",
    "high_low_range_pct",
    "hour_sin", "hour_cos",
    "dow_sin",  "dow_cos",
    "session_europe", "session_us",
]


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES RÉGIME — classification bull/bear/neutral (Niveau 1)
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_INST_REGIME: List[str] = [
    "distance_ema_20",
    "distance_ema_50",
    "distance_ema_200",
    "ema_21_50_spread",
    "ema_50_200_spread",
    "return_20",
    "return_50",
    "log_return_20",
    "rsi_20",
    "adx_20",
    "realized_vol_20",
    "garman_klass_vol_20",
    "trend_score",
    "momentum_score",
    "regression_slope_50",
    "regression_r2_50",
    "current_drawdown_50",
    "mtf_1d_return_5",
    "mtf_1d_adx_5",
]


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────────────────────────────────────

def get_available_features(
    df: pd.DataFrame,
    feature_list: List[str],
    min_fill: float = 0.75,
) -> List[str]:
    """
    Filtre la liste en ne conservant que les features présentes ET assez remplies.
    Idempotent, safe à appeler à chaque training run.
    """
    available = []
    dropped   = []
    low_fill  = []

    for f in feature_list:
        if f not in df.columns:
            dropped.append(f)
            continue
        fill = float(df[f].notna().mean())
        if fill < min_fill:
            low_fill.append((f, fill))
            continue
        available.append(f)

    if dropped:
        LOG.warning("Features absentes (%d) : %s", len(dropped), dropped[:8])
    if low_fill:
        LOG.warning(
            "Features fill < %.0f%% (%d) : %s",
            min_fill * 100, len(low_fill),
            [(f, f"{v:.1%}") for f, v in low_fill[:5]],
        )

    LOG.info("Features disponibles : %d / %d", len(available), len(feature_list))
    return available


def deduplicate(feature_list: List[str]) -> List[str]:
    """Preserve order, remove duplicates."""
    seen = set()
    out  = []
    for f in feature_list:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# Listes déduplicatées (au cas où COMMON apparaîtrait deux fois)
FEATURES_INST_LONG  = deduplicate(FEATURES_INST_LONG)
FEATURES_INST_SHORT = deduplicate(FEATURES_INST_SHORT)
