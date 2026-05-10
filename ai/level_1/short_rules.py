"""
level_1/short_rules.py — GATE NO_SHORT INTELLIGENTE PAR CONTEXTE
================================================================

Complète rules.py (gate déterministe simple) avec une gate contextuelle
qui distingue POURQUOI le short est bloqué ou autorisé.

Architecture :
    compute_no_short_gate()           → bool Series (True = bloqué)
    compute_short_permission_context() → DataFrame de colonnes ctx_*

Logique gate :
    bull_trend_sain = True si plusieurs conditions haussières alignées
    exception_short = True si scores de dislocation extrêmes (foule, breakdown…)
    NO_SHORT = bull_trend_sain AND NOT exception_short

Principe :
    - En bull trend sain → pas de short (pas de contexte structurel baissier)
    - Exception : foule extrêmement longée, breakout raté, breakdown → short ok
      même en bull, car ces signaux précèdent des retournements violents.
    - Valeurs par défaut des percentiles calibrées sur z-scores typiques BTC.

Toutes les fonctions :
    - Vectorisées, NaN-safe (fillna avec valeur neutre avant les comparaisons)
    - Colonnes manquantes → valeur par défaut (pas d'exception levée)
    - Retour déterministe (aucun random)
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Percentiles par défaut — calibrés sur BTC 2017-2023
# ─────────────────────────────────────────────────────────────────────────────
# Ces valeurs correspondent aux seuils empiriques observés sur les scores
# composites définis dans le pipeline. Elles servent de fallback quand aucun
# train_percentiles n'est passé (inférence live, tests unitaires, etc.).

_DEFAULT_PERCENTILES: Dict[str, float] = {
    # Gate principale
    "p90_crowding":           1.5,   # z-score long_crowding_score > 1.5 = foule extrême
    "p85_failed_breakout":    0.6,   # score failed_breakout > 0.6 = signal de retournement
    "p85_liquidity":          0.5,   # score liquidity_stress > 0.5 = stress de liquidité
    "p85_breakdown":          0.5,   # score breakdown > 0.5 = structure cassée
    "p85_bear_cont":          0.5,   # score bear_continuation > 0.5 = trend baissier
    # Contextes individuels (p75)
    "p75_crowded_longs":      1.0,   # long_crowding_score > 1.0 = positionnement élevé
    "p75_breakdown":          0.4,   # breakdown_score > 0.4
    "p75_failed_breakout":    0.4,   # failed_breakout_score > 0.4
    "p75_liquidity":          0.4,   # liquidity proxy > 0.4
    "p75_bear_continuation":  0.4,   # bear_continuation_score > 0.4
    "p75_macro_riskoff":      0.6,   # score macro risk-off > 0.6
}


def _get(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    """Récupère une colonne NaN-safe avec valeur par défaut si absente."""
    if col in df.columns:
        return df[col].fillna(default)
    return pd.Series(default, index=df.index, dtype=np.float64)


def _pct(train_percentiles: Optional[Dict], key: str) -> float:
    """Récupère un percentile depuis le dict ou la valeur par défaut."""
    if train_percentiles and key in train_percentiles:
        return float(train_percentiles[key])
    return _DEFAULT_PERCENTILES.get(key, 0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Gate principale
# ─────────────────────────────────────────────────────────────────────────────

def compute_no_short_gate(
    df: pd.DataFrame,
    train_percentiles: Optional[Dict[str, float]] = None,
) -> pd.Series:
    """
    Retourne une Series booléenne : True = SHORT BLOQUÉ sur cette barre.

    Logique :
        bull_trend_sain = True si TOUTES les conditions suivantes sont vraies :
            - ema_spread_50_200 > 0  (EMA50 au-dessus de EMA200 = tendance haussière)
            - mom_logret_72 > 0      (momentum 72h positif)
            - rsi_14 > 50            (RSI bullish)
            - long_crowding_score < p90_crowding  (pas de foule extrême)
            - failed_breakout_score < p85_failed_breakout
            - breakdown_score < p85_breakdown

        exception_short = True si AU MOINS UNE condition :
            - long_crowding_score > p90_crowding   (foule extrême → fade)
            - failed_breakout_score > p85_failed_breakout
            - liquidity_stress_score > p85_liquidity  (approximé par squeeze_risk)

        NO_SHORT = bull_trend_sain AND NOT exception_short

    Arguments
    ---------
    df               : DataFrame avec les features et scores composites
    train_percentiles: dict optionnel des percentiles de train. Clés attendues :
                       p90_crowding, p85_failed_breakout, p85_liquidity,
                       p85_breakdown. Si absent → valeurs par défaut.

    Retourne
    --------
    pd.Series(bool, index=df.index) — True = short bloqué
    """
    p90_crowd  = _pct(train_percentiles, "p90_crowding")
    p85_failed = _pct(train_percentiles, "p85_failed_breakout")
    p85_liq    = _pct(train_percentiles, "p85_liquidity")
    p85_break  = _pct(train_percentiles, "p85_breakdown")

    # ── Features haussières ──────────────────────────────────────────────────
    ema_spread   = _get(df, "ema_spread_50_200", 0.0)
    mom_72       = _get(df, "mom_logret_72",     0.0)
    rsi          = _get(df, "rsi_14",            50.0)

    # ── Scores composites ────────────────────────────────────────────────────
    crowding      = _get(df, "long_crowding_score",    0.0)
    failed_bo     = _get(df, "failed_breakout_score",  0.0)
    breakdown     = _get(df, "breakdown_score",        0.0)
    # liquidity_stress_score n'est pas toujours calculé → fallback sur squeeze
    liq_stress    = _get(df, "liquidity_stress_score", None)
    if liq_stress is None or (liq_stress == 0.0).all():
        liq_stress = _get(df, "squeeze_risk_score", 0.0)

    # ── Bull trend sain : TOUTES les conditions doivent être vérifiées ────────
    bull_trend_sain = (
        (ema_spread > 0)
        & (mom_72   > 0)
        & (rsi      > 50)
        & (crowding  < p90_crowd)
        & (failed_bo < p85_failed)
        & (breakdown < p85_break)
    )

    # ── Exception short : AU MOINS UNE condition ─────────────────────────────
    exception_short = (
        (crowding  > p90_crowd)
        | (failed_bo > p85_failed)
        | (liq_stress > p85_liq)
    )

    # ── Gate finale ──────────────────────────────────────────────────────────
    no_short = bull_trend_sain & ~exception_short

    return no_short.rename("no_short")


# ─────────────────────────────────────────────────────────────────────────────
# Contexte détaillé
# ─────────────────────────────────────────────────────────────────────────────

def compute_short_permission_context(
    df: pd.DataFrame,
    train_percentiles: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """
    Retourne un DataFrame avec les colonnes de contexte booléennes suivantes :

    ctx_crowded_longs     : long_crowding_score élevé (foule extrêmement longée)
    ctx_breakdown         : breakdown_score élevé (structure cassée)
    ctx_failed_breakout   : failed_breakout_score élevé (faux breakout)
    ctx_liquidity_stress  : stress de liquidité (squeeze_risk ou liquidity_stress)
    ctx_bear_continuation : bear_continuation_score élevé (tendance baissière)
    ctx_macro_riskoff     : régime risk-off global (funding négatif + peur + OI baisse)
    ctx_general_short     : contexte générique — actif si aucun autre contexte
    no_short              : gate booléenne (True = short bloqué)

    Un contexte est actif si son score dépasse le p75 de train correspondant.

    Arguments
    ---------
    df               : DataFrame avec features et scores composites
    train_percentiles: dict optionnel des percentiles. Clés attendues :
                       p75_crowded_longs, p75_breakdown, p75_failed_breakout,
                       p75_liquidity, p75_bear_continuation, p75_macro_riskoff.

    Retourne
    --------
    pd.DataFrame avec colonnes ctx_* + no_short, même index que df.
    """
    p75_crowd  = _pct(train_percentiles, "p75_crowded_longs")
    p75_break  = _pct(train_percentiles, "p75_breakdown")
    p75_failed = _pct(train_percentiles, "p75_failed_breakout")
    p75_liq    = _pct(train_percentiles, "p75_liquidity")
    p75_bear   = _pct(train_percentiles, "p75_bear_continuation")
    p75_macro  = _pct(train_percentiles, "p75_macro_riskoff")

    # ── Scores composites ────────────────────────────────────────────────────
    crowding   = _get(df, "long_crowding_score",    0.0)
    breakdown  = _get(df, "breakdown_score",        0.0)
    failed_bo  = _get(df, "failed_breakout_score",  0.0)
    bear_cont  = _get(df, "bear_continuation_score", 0.0)

    # liquidity_stress_score : fallback sur squeeze_risk_score
    liq_stress = _get(df, "liquidity_stress_score", 0.0)
    squeeze    = _get(df, "squeeze_risk_score",     0.0)
    # Si liquidity_stress_score toujours à 0 (absente), utiliser squeeze
    liq_proxy  = liq_stress.where(liq_stress != 0.0, squeeze)

    # ── Macro risk-off : funding négatif + peur élevée + OI en baisse ────────
    # Approximation vectorisée : moyenne pondérée de signaux z-scorés inversés
    funding_z  = _get(df, "funding_rate_z_24",            0.0)
    fg_z       = _get(df, "fear_greed_value_z_24",        0.0)
    oi_z       = _get(df, "oihist_sumOpenInterest_z_24",  0.0)
    ls_z       = _get(df, "global_ls_longShortRatio_z_24", 0.0)

    # Risk-off = funding négatif (foule vendeuse) + peur (fg bas) + OI baisse
    # On inverse les z-scores pour que la valeur soit haute en risk-off
    macro_riskoff_score = (
        (-funding_z).clip(lower=0) * 0.35   # funding négatif = bear crowd
        + (-fg_z).clip(lower=0)    * 0.30   # peur = sentiment baissier
        + (-oi_z).clip(lower=0)    * 0.20   # OI baisse = liquidation positions
        + ls_z.clip(lower=0)       * 0.15   # L/S élevé = crowded longs → retournement
    )

    # ── Contextes booléens (> p75 de train) ──────────────────────────────────
    ctx_crowded_longs     = (crowding         > p75_crowd).astype(bool)
    ctx_breakdown         = (breakdown        > p75_break).astype(bool)
    ctx_failed_breakout   = (failed_bo        > p75_failed).astype(bool)
    ctx_liquidity_stress  = (liq_proxy        > p75_liq).astype(bool)
    ctx_bear_continuation = (bear_cont        > p75_bear).astype(bool)
    ctx_macro_riskoff     = (macro_riskoff_score > p75_macro).astype(bool)

    # Contexte général : aucun autre contexte actif
    any_specific_ctx = (
        ctx_crowded_longs
        | ctx_breakdown
        | ctx_failed_breakout
        | ctx_liquidity_stress
        | ctx_bear_continuation
        | ctx_macro_riskoff
    )
    ctx_general_short = ~any_specific_ctx

    # ── Gate no_short ─────────────────────────────────────────────────────────
    no_short = compute_no_short_gate(df, train_percentiles)

    # ── Assemblage du DataFrame de sortie ────────────────────────────────────
    out = pd.DataFrame(
        {
            "ctx_crowded_longs":     ctx_crowded_longs.values,
            "ctx_breakdown":         ctx_breakdown.values,
            "ctx_failed_breakout":   ctx_failed_breakout.values,
            "ctx_liquidity_stress":  ctx_liquidity_stress.values,
            "ctx_bear_continuation": ctx_bear_continuation.values,
            "ctx_macro_riskoff":     ctx_macro_riskoff.values,
            "ctx_general_short":     ctx_general_short.values,
            "no_short":              no_short.values,
        },
        index=df.index,
    )

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Calcul des percentiles de train (pour passer à compute_*)
# ─────────────────────────────────────────────────────────────────────────────

def compute_train_percentiles(df_train: pd.DataFrame) -> Dict[str, float]:
    """
    Calcule les percentiles nécessaires depuis le DataFrame de train.

    À appeler UNIQUEMENT sur les données de train pour éviter tout data leakage.

    Retourne
    --------
    dict utilisable directement comme train_percentiles dans compute_no_short_gate()
    et compute_short_permission_context().
    """

    def _pctile(col: str, q: float, default: float) -> float:
        if col not in df_train.columns:
            return default
        vals = df_train[col].dropna()
        if len(vals) == 0:
            return default
        return float(np.nanpercentile(vals.values, q * 100))

    # Proxy liquidity : prefer liquidity_stress_score, fallback squeeze_risk_score
    liq_col = (
        "liquidity_stress_score"
        if "liquidity_stress_score" in df_train.columns
        else "squeeze_risk_score"
    )

    return {
        # Gate principale
        "p90_crowding":          _pctile("long_crowding_score",    0.90, _DEFAULT_PERCENTILES["p90_crowding"]),
        "p85_failed_breakout":   _pctile("failed_breakout_score",  0.85, _DEFAULT_PERCENTILES["p85_failed_breakout"]),
        "p85_liquidity":         _pctile(liq_col,                  0.85, _DEFAULT_PERCENTILES["p85_liquidity"]),
        "p85_breakdown":         _pctile("breakdown_score",        0.85, _DEFAULT_PERCENTILES["p85_breakdown"]),
        "p85_bear_cont":         _pctile("bear_continuation_score", 0.85, _DEFAULT_PERCENTILES["p85_bear_cont"]),
        # Contextes individuels (p75)
        "p75_crowded_longs":     _pctile("long_crowding_score",    0.75, _DEFAULT_PERCENTILES["p75_crowded_longs"]),
        "p75_breakdown":         _pctile("breakdown_score",        0.75, _DEFAULT_PERCENTILES["p75_breakdown"]),
        "p75_failed_breakout":   _pctile("failed_breakout_score",  0.75, _DEFAULT_PERCENTILES["p75_failed_breakout"]),
        "p75_liquidity":         _pctile(liq_col,                  0.75, _DEFAULT_PERCENTILES["p75_liquidity"]),
        "p75_bear_continuation": _pctile("bear_continuation_score", 0.75, _DEFAULT_PERCENTILES["p75_bear_continuation"]),
        "p75_macro_riskoff":     _DEFAULT_PERCENTILES["p75_macro_riskoff"],  # score dérivé, pas de colonne directe
    }
