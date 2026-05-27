"""
level_3/contexts.py — DÉFINITION DES CONTEXTES DE MARCHÉ
=========================================================

5 contextes mutuellement exclusifs (+ NEUTRAL fallback) détectés par règles
déterministes. Aucun ML ici → zéro leakage possible.

Hiérarchie de priorité (du plus fort au plus faible) :
  1. HIGH_VOL       : spike de volatilité — contexte le plus particulier
  2. BREAKOUT       : cassure directionnelle forte
  3. TREND_LONG     : tendance haussière structurelle
  4. TREND_SHORT    : tendance baissière structurelle
  5. MEAN_REVERSION : retour à la moyenne actif
  6. NEUTRAL        : tout le reste → fallback sur level_2

Pourquoi des règles et pas du ML pour le routeur ?
  - Les règles sont transparentes et reproductibles.
  - Aucun risque de leakage future → condition préalable absolue.
  - Les contextes doivent être stables OOS pour que les experts soient fiables.
  - Le ML n'apporte pas d'edge pour de la classification de régime à cette granularité.

Usage
-----
    from ai.level_3.contexts import assign_context, MarketContext
    ctx_series = assign_context(df)
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Enum des contextes
# ─────────────────────────────────────────────────────────────────────────────

class MarketContext(str, Enum):
    """
    Contextes de marché pour le routage vers un expert spécialisé.
    Hérite de str pour permettre la sérialisation JSON directe.
    """
    TREND_LONG     = "TREND_LONG"
    TREND_SHORT    = "TREND_SHORT"
    MEAN_REVERSION = "MEAN_REVERSION"
    BREAKOUT       = "BREAKOUT"
    HIGH_VOL       = "HIGH_VOL"
    NEUTRAL        = "NEUTRAL"


# Contextes directionnels : applicables aux labels long/short
LONG_CONTEXTS  = {MarketContext.TREND_LONG, MarketContext.BREAKOUT,
                  MarketContext.MEAN_REVERSION, MarketContext.NEUTRAL}
SHORT_CONTEXTS = {MarketContext.TREND_SHORT, MarketContext.BREAKOUT,
                  MarketContext.MEAN_REVERSION, MarketContext.NEUTRAL}
# HIGH_VOL s'applique aux deux côtés

ALL_CONTEXTS = list(MarketContext)


# ─────────────────────────────────────────────────────────────────────────────
# Seuils déterministes — calibrés sur la logique économique, pas sur les données
# ─────────────────────────────────────────────────────────────────────────────

# HIGH_VOL : la vol court terme est significativement au-dessus de la vol long terme
HIGH_VOL_RV_RATIO_THRESHOLD: float = 1.6    # rv_ratio_24_72 > 1.6
HIGH_VOL_ATR_THRESHOLD: float = 0.008       # atr_pct_14 > 0.8% fallback

# BREAKOUT : mouvement directionnel fort avec expansion de range
BREAKOUT_EFF_RATIO_THRESHOLD: float = 0.55  # eff_ratio_24 > 0.55 → tendance directionnelle
BREAKOUT_BOLL_EXPANSION_THRESHOLD: float = 0.3  # boll_expansion_6 > 0.3 → expansion
BREAKOUT_BOLL_POS_EXTREME: float = 0.75    # |boll_pos_20| > 0.75 → prix aux extrêmes

# TREND_LONG : structure haussière claire sur plusieurs horizons
TREND_LONG_MOM_72: float = 0.005           # mom_logret_72 > +0.5% sur 3j
TREND_LONG_MOM_24: float = 0.0             # mom_logret_24 > 0 (confirmation court terme)
TREND_LONG_EMA_SPREAD: float = 0.0        # ema_spread_50_200 > 0 → golden cross

# TREND_SHORT : structure baissière claire
TREND_SHORT_MOM_72: float = -0.005         # mom_logret_72 < -0.5%
TREND_SHORT_MOM_24: float = 0.0            # mom_logret_24 < 0
TREND_SHORT_EMA_SPREAD: float = 0.0       # ema_spread_50_200 < 0 → death cross

# MEAN_REVERSION : prix aux extrêmes + signal de retournement
MR_BOLL_POS_EXTREME: float = 0.80         # |boll_pos_20| > 0.80 → extension
MR_AUTOCORR_THRESHOLD: float = 0.05       # ret_neg_autocorr_12 > 0.05 → mean-rev actif
MR_RSI_OB: float = 68.0                   # surachat
MR_RSI_OS: float = 32.0                   # survente


# ─────────────────────────────────────────────────────────────────────────────
# Fonction principale : assigner un contexte à chaque barre
# ─────────────────────────────────────────────────────────────────────────────

def assign_context(
    df: pd.DataFrame,
    priority_order: Optional[list] = None,
) -> pd.Series:
    """
    Assigne un MarketContext à chaque barre du DataFrame.

    Retourne une pd.Series de strings (MarketContext.value) de même index que df.

    Ordre de priorité par défaut :
      HIGH_VOL → BREAKOUT → TREND_LONG → TREND_SHORT → MEAN_REVERSION → NEUTRAL

    Arguments
    ---------
    df             : DataFrame avec les features calculées
    priority_order : liste optionnelle de MarketContext pour changer l'ordre

    Colonnes utilisées (toutes optionnelles avec fallback)
    ------
    rv_ratio_24_72, atr_pct_14,
    eff_ratio_24, boll_expansion_6, boll_pos_20,
    mom_logret_72, mom_logret_24, ema_spread_50_200, dist_ema_50,
    ret_neg_autocorr_12, rsi_14
    """
    n = len(df)
    ctx = np.full(n, MarketContext.NEUTRAL.value, dtype=object)

    # Extraire les vecteurs (avec fallbacks sûrs)
    rv_ratio    = _col(df, "rv_ratio_24_72",    default=1.0)
    atr_pct     = _col(df, "atr_pct_14",        default=0.003)
    eff_r24     = _col(df, "eff_ratio_24",       default=0.3)
    boll_exp    = _col(df, "boll_expansion_6",   default=0.0)
    boll_pos    = _col(df, "boll_pos_20",        default=0.0)
    mom72       = _col(df, "mom_logret_72",      default=0.0)
    mom24       = _col(df, "mom_logret_24",      default=0.0)
    ema_spr     = _col(df, "ema_spread_50_200",  default=0.0)
    dist_ema50  = _col(df, "dist_ema_50",        default=0.0)
    autocorr    = _col(df, "ret_neg_autocorr_12",default=0.0)
    rsi         = _col(df, "rsi_14",             default=50.0)

    order = priority_order or [
        MarketContext.HIGH_VOL,
        MarketContext.BREAKOUT,
        MarketContext.TREND_LONG,
        MarketContext.TREND_SHORT,
        MarketContext.MEAN_REVERSION,
    ]

    # Appliquer dans l'ordre inverse (le dernier appliqué gagne → priorité max en premier)
    for context in reversed(order):
        mask = _detect(
            context, rv_ratio, atr_pct, eff_r24, boll_exp,
            boll_pos, mom72, mom24, ema_spr, dist_ema50, autocorr, rsi
        )
        ctx[mask] = context.value

    return pd.Series(ctx, index=df.index, name="market_context")


def _detect(
    context: MarketContext,
    rv_ratio, atr_pct, eff_r24, boll_exp,
    boll_pos, mom72, mom24, ema_spr, dist_ema50, autocorr, rsi
) -> np.ndarray:
    """Retourne un masque booléen pour un contexte donné."""

    if context == MarketContext.HIGH_VOL:
        return (rv_ratio > HIGH_VOL_RV_RATIO_THRESHOLD) | (atr_pct > HIGH_VOL_ATR_THRESHOLD)

    if context == MarketContext.BREAKOUT:
        directional    = eff_r24 > BREAKOUT_EFF_RATIO_THRESHOLD
        expanding      = boll_exp > BREAKOUT_BOLL_EXPANSION_THRESHOLD
        at_extreme     = np.abs(boll_pos) > BREAKOUT_BOLL_POS_EXTREME
        return directional & (expanding | at_extreme)

    if context == MarketContext.TREND_LONG:
        return (
            (mom72   > TREND_LONG_MOM_72)
            & (mom24  > TREND_LONG_MOM_24)
            & (ema_spr > TREND_LONG_EMA_SPREAD)
            & (dist_ema50 > 0)            # prix au-dessus EMA50
        )

    if context == MarketContext.TREND_SHORT:
        return (
            (mom72   < TREND_SHORT_MOM_72)
            & (mom24  < TREND_SHORT_MOM_24)
            & (ema_spr < TREND_SHORT_EMA_SPREAD)
            & (dist_ema50 < 0)            # prix en-dessous EMA50
        )

    if context == MarketContext.MEAN_REVERSION:
        extension  = np.abs(boll_pos) > MR_BOLL_POS_EXTREME
        mean_rev   = autocorr > MR_AUTOCORR_THRESHOLD
        overbought = rsi > MR_RSI_OB
        oversold   = rsi < MR_RSI_OS
        return extension & (mean_rev | overbought | oversold)

    return np.zeros(len(rv_ratio), dtype=bool)   # NEUTRAL — ne pas écraser


def _col(df: pd.DataFrame, name: str, default: float) -> np.ndarray:
    """Extrait une colonne ou retourne un vecteur constant si absente."""
    if name in df.columns:
        return df[name].fillna(default).values.astype(np.float64)
    return np.full(len(df), default, dtype=np.float64)


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostic
# ─────────────────────────────────────────────────────────────────────────────

def diagnose_context_distribution(
    df: pd.DataFrame,
    context_col: str = "market_context",
    masks: Optional[dict] = None,
) -> dict:
    """
    Affiche et retourne la distribution des contextes par split.

    Arguments
    ---------
    df          : DataFrame avec la colonne context_col
    context_col : nom de la colonne de contexte
    masks       : dict {"train": mask_arr, "val": mask_arr, "test": mask_arr}
    """
    if context_col not in df.columns:
        df = df.copy()
        df[context_col] = assign_context(df)

    report = {}
    splits = masks or {"all": np.ones(len(df), dtype=bool)}

    for split_name, mask in splits.items():
        sub = df.loc[mask, context_col]
        n   = len(sub)
        dist = {}
        for ctx in MarketContext:
            count = int((sub == ctx.value).sum())
            dist[ctx.value] = {"n": count, "pct": round(count / max(n, 1), 3)}
        report[split_name] = dist

        print(f"\n   Contextes [{split_name}] (n={n:,}) :")
        for ctx_name, stats in dist.items():
            bar = "█" * int(stats["pct"] * 30)
            print(f"     {ctx_name:<18} {stats['pct']:>5.1%}  {bar}")

    return report
