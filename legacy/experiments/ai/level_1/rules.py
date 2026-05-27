"""
level_1/rules.py — FILTRE DE RÉGIME DÉTERMINISTE POUR LE SHORT
===============================================================

Gate dure déterministe : interdit le short dans les phases haussières actives.

Fondement économique :
  - En crypto, les bull markets actifs génèrent une asymétrie haussière persistante
    (funding positif, liquidations long rares, biais institutionnel).
  - Shorter pendant un bull actif n'est pas un "mauvais trade" — c'est un trade
    structurellement hors contexte : le modèle ne peut pas prédire quelque chose
    qui dépend de forces macro qui ne figurent pas dans les features.
  - Le filtre réduit radicalement les faux positifs en supprimant le "bruit haussier".

Régimes :
  NO_SHORT   = biais haussier actif  → short interdit
  SHORTABLE  = structure baissière confirmée → short autorisé
  NEUTRAL    = range/correction modérée → short autorisé avec seuil plus élevé

Définition déterministe (pas de ML — pas de leakage possible) :
  NO_SHORT  : prix > EMA50 ET EMA50 > EMA200 ET RSI > 55
  SHORTABLE : prix < EMA50 ET (EMA50 < EMA200 OU RSI < 50)
              OU [prix < EMA50 ET RSI < 45 ET dist_from_local_high_24 < -0.02]
  NEUTRAL   : tout le reste

Utilisation dans le backtest :
  rc_result = apply_regime_filter(row, p_short, threshold)
  → returns (should_trade, regime, adjusted_threshold)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ai.level_0.constants import REGIME_COL

REGIME_NO_SHORT  = "NO_SHORT"
REGIME_SHORTABLE = "SHORTABLE"
REGIME_NEUTRAL   = "NEUTRAL"


@dataclass
class RegimeFilter:
    """
    Filtre de régime configurable.

    neutral_threshold_boost : à combien élever le seuil en NEUTRAL (vs SHORTABLE)
      - En SHORTABLE, le seuil de base est utilisé.
      - En NEUTRAL, le seuil est augmenté de neutral_threshold_boost.
      - En NO_SHORT, toujours HOLD.
    """
    neutral_threshold_boost: float = 0.06   # +6 points en NEUTRAL vs SHORTABLE
    require_shortable_only: bool   = False   # si True, ignorer NEUTRAL aussi
    min_bars_in_regime: int        = 3       # ignorer les régimes trop courts (bruit)

    def gate(
        self,
        regime: str,
        p_short: float,
        base_threshold: float,
    ) -> Tuple[bool, float]:
        """
        Décide si le signal short est autorisé selon le régime.

        Retourne (allow_trade, effective_threshold).
        """
        if regime == REGIME_NO_SHORT:
            return False, base_threshold

        if regime == REGIME_NEUTRAL:
            if self.require_shortable_only:
                return False, base_threshold
            effective_thr = base_threshold + self.neutral_threshold_boost
            return p_short >= effective_thr, effective_thr

        # SHORTABLE : seuil de base
        return p_short >= base_threshold, base_threshold


def apply_regime_filter(
    row: dict,
    p_short: float,
    base_threshold: float,
    regime_filter: Optional[RegimeFilter] = None,
) -> Tuple[bool, str, float]:
    """
    Applique le filtre de régime sur une barre.

    Arguments
    ---------
    row            : dict de la barre courante (doit contenir REGIME_COL si disponible)
    p_short        : probabilité short calibrée
    base_threshold : seuil de décision short
    regime_filter  : RegimeFilter configuré (crée un défaut si None)

    Retourne
    --------
    (allow_trade, regime_str, effective_threshold)
    """
    rf = regime_filter or RegimeFilter()

    regime = str(row.get(REGIME_COL, REGIME_NEUTRAL))

    if REGIME_COL not in row:
        regime = _compute_regime_from_row(row)

    allow, thr = rf.gate(regime, p_short, base_threshold)
    return allow, regime, thr


def _compute_regime_from_row(row: dict) -> str:
    """
    Calcule le régime à la volée à partir d'une seule barre.
    Utilisé comme fallback si REGIME_COL n'est pas dans le DataFrame.
    """
    dist_ema50     = row.get("dist_ema_50", 0.0)
    ema_spread     = row.get("ema_spread_50_200", 0.0)
    rsi            = row.get("rsi_14", 50.0)

    price_above_50  = dist_ema50 > 0
    ema50_above_200 = ema_spread > 0
    rsi_bullish     = rsi > 55
    rsi_bearish     = rsi < 50

    if price_above_50 and ema50_above_200 and rsi_bullish:
        return REGIME_NO_SHORT
    if (not price_above_50) and ((not ema50_above_200) or rsi_bearish):
        return REGIME_SHORTABLE
    return REGIME_NEUTRAL


def diagnose_regime_distribution(
    df: pd.DataFrame,
    val_mask: np.ndarray,
    test_mask: np.ndarray,
) -> Dict:
    """
    Analyse la distribution des régimes sur val et test.

    Retourne un rapport avec :
      - % de NO_SHORT, SHORTABLE, NEUTRAL par split
      - Corrélation régime vs y_short (validation de cohérence)
    """
    report: Dict = {}

    for split_name, mask in [("val", val_mask), ("test", test_mask)]:
        if REGIME_COL not in df.columns:
            report[split_name] = {"error": f"{REGIME_COL} absent"}
            continue

        df_split  = df.loc[mask]
        n         = len(df_split)
        regimes   = df_split[REGIME_COL]

        n_no      = int((regimes == REGIME_NO_SHORT).sum())
        n_short   = int((regimes == REGIME_SHORTABLE).sum())
        n_neutral = int((regimes == REGIME_NEUTRAL).sum())

        entry: Dict = {
            "n_total":      n,
            "pct_no_short":  round(n_no / max(n, 1), 3),
            "pct_shortable": round(n_short / max(n, 1), 3),
            "pct_neutral":   round(n_neutral / max(n, 1), 3),
        }

        if "y_short" in df_split.columns:
            for reg, label in [
                (REGIME_NO_SHORT, "no_short"),
                (REGIME_SHORTABLE, "shortable"),
                (REGIME_NEUTRAL, "neutral"),
            ]:
                mask_reg = (regimes == reg) & (df_split["y_short"] >= 0)
                n_reg = int(mask_reg.sum())
                if n_reg > 0:
                    pct_pos = float((df_split.loc[mask_reg, "y_short"] == 1).mean())
                    entry[f"short_rate_{label}"] = round(pct_pos, 4)
                else:
                    entry[f"short_rate_{label}"] = None

        report[split_name] = entry

        print(f"   Régimes [{split_name}] : "
              f"NO_SHORT={n_no/max(n,1):.1%}  "
              f"SHORTABLE={n_short/max(n,1):.1%}  "
              f"NEUTRAL={n_neutral/max(n,1):.1%}")
        if "short_rate_shortable" in entry and entry["short_rate_shortable"] is not None:
            print(f"     → short_rate en SHORTABLE={entry['short_rate_shortable']:.1%}  "
                  f"NO_SHORT={entry.get('short_rate_no_short', 'N/A')}")

    return report


def compute_regime_stats_by_year(df: pd.DataFrame, mask: np.ndarray) -> Dict:
    """
    Distribution des régimes par année — pour diagnostiquer les années problématiques.
    """
    if REGIME_COL not in df.columns:
        return {"error": f"{REGIME_COL} absent"}

    df_sub = df.loc[mask].copy()

    if not isinstance(df_sub.index, pd.DatetimeIndex):
        try:
            df_sub.index = pd.to_datetime(df_sub.index)
        except Exception:
            return {"error": "index non-datetime"}

    results: Dict = {}
    for yr in sorted(df_sub.index.year.unique()):
        yr_mask = df_sub.index.year == yr
        df_yr   = df_sub.loc[yr_mask]
        n       = len(df_yr)
        regimes = df_yr[REGIME_COL]
        results[str(yr)] = {
            "n": n,
            "pct_no_short":  round(int((regimes == REGIME_NO_SHORT).sum())  / max(n, 1), 3),
            "pct_shortable": round(int((regimes == REGIME_SHORTABLE).sum()) / max(n, 1), 3),
            "pct_neutral":   round(int((regimes == REGIME_NEUTRAL).sum())   / max(n, 1), 3),
        }

    return results
