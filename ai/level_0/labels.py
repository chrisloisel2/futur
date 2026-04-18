"""
level_0/labels.py — FACTORY DE LABELS CANONIQUE
================================================

Labels produits :
  tradeable_net   (0/1) : |ret| > seuil — mouvement suffisant pour couvrir les frais
  y_long          (0/1/-1) : ret > thr_long   (−1 = gray zone, exclue du training)
  y_short         (0/1/-1) : ret < −thr_short  avec contrainte non-retournement
  regime_short    (str) : "SHORTABLE" | "NEUTRAL" | "NO_SHORT" — filtre de contexte

Asymétrie structurelle long vs short :
  - thr_short > thr_long : seuil plus élevé → moins de labels mais plus propres
  - cost_short = cost * COST_SHORT_MULT : funding + slippage de recovery inclus
  - non-retournement : un short valide ne se retourne pas dans les 3 barres suivantes
  - gray_zone_short plus large (0.25 vs 0.15) car signal plus bruité aux frontières

Conventions anti-leakage :
  - Tous les seuils sont calibrés sur train_mask uniquement.
  - future_ret_h3_max est calculé dans compute_short_reversal_col() avant build_labels().
  - Le régime est déterministe (indicateurs techniques) — pas de leakage possible.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional

from ai.level_0.constants import (
    TRADEABLE_QUANTILE, TRADEABLE_QUANTILE_LONG, TRADEABLE_QUANTILE_SHORT,
    COST_PCT, COST_SHORT_MULT, PNL_COST_MULT,
    LONG_MIN_ABS_RETURN, GRAY_ZONE_FACTOR_LONG,
    NON_REVERSAL_WINDOW_LONG, NON_REVERSAL_THRESHOLD_FACTOR_LONG,
    NON_REVERSAL_WINDOW, NON_REVERSAL_THRESHOLD_FACTOR,
    GRAY_ZONE_FACTOR_SHORT,
    TARGET_COL, TARGET_REVERSAL_COL, TARGET_REVERSAL_COL_LONG,
    HORIZON_BARS, REGIME_COL, REGIME_COL_LONG,
    assert_horizon,
)


# ─────────────────────────────────────────────────────────────────────────────
# Étape 0 — Colonnes dérivées (à appeler AVANT build_labels)
# ─────────────────────────────────────────────────────────────────────────────

def compute_long_reversal_col(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule future_ret_h3_min = min(ret[t+1], ret[t+2], ret[t+3]).

    Utilisé pour le filtre anti-retournement du label LONG :
    si le prix dip fortement juste après l'entrée, le long est un faux signal.
    Calculée avant le split, jamais utilisée comme feature.
    """
    ret = df[TARGET_COL].values.astype(np.float64)
    n   = len(ret)
    h3_min = np.full(n, np.nan)
    for i in range(n - NON_REVERSAL_WINDOW_LONG):
        h3_min[i] = np.min(ret[i + 1 : i + 1 + NON_REVERSAL_WINDOW_LONG])
    df = df.copy()
    df[TARGET_REVERSAL_COL_LONG] = h3_min
    return df


def compute_short_reversal_col(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule future_ret_h3_max = max(ret[t+1], ret[t+2], ret[t+3]).

    Cette colonne est requise par build_labels() pour la contrainte
    de non-retournement du label short. Elle DOIT être calculée avant
    le split train/val/test (les valeurs forward existent dans le CSV entier),
    mais JAMAIS utilisée comme feature (leakage si exposée au modèle).

    Appelée une seule fois, au chargement du CSV.
    """
    ret = df[TARGET_COL].values.astype(np.float64)
    n   = len(ret)
    h3_max = np.full(n, np.nan)
    for i in range(n - NON_REVERSAL_WINDOW):
        h3_max[i] = np.max(ret[i + 1 : i + 1 + NON_REVERSAL_WINDOW])
    df = df.copy()
    df[TARGET_REVERSAL_COL] = h3_max
    return df


def compute_long_regime_col(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule regime_long = "LONGABLE" | "NEUTRAL" | "NO_LONG".

    NO_LONG   = bear confirmé → long interdit
                prix < EMA200 ET EMA50 < EMA200 ET RSI < 45 ET momentum 72h < 0
    LONGABLE  = structure haussière → long autorisé
                prix > EMA50 ET EMA50 > EMA200 ET RSI > 40
    NEUTRAL   = tout le reste (range, consolidation)

    Requiert les colonnes : dist_ema_50, dist_ema_200, ema_spread_50_200, rsi_14, mom_logret_72
    """
    df = df.copy()
    required = ["dist_ema_50", "ema_spread_50_200", "rsi_14"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        print(f"   ⚠  compute_long_regime_col: colonnes manquantes {missing} → NEUTRAL partout")
        df[REGIME_COL_LONG] = "NEUTRAL"
        return df

    price_above_ema50  = df["dist_ema_50"] > 0
    ema50_above_ema200 = df["ema_spread_50_200"] > 0
    rsi                = df["rsi_14"]
    mom72              = df["mom_logret_72"] if "mom_logret_72" in df.columns else pd.Series(0.0, index=df.index)

    # NO_LONG : bear confirmé sur tous les critères
    no_long_mask   = (~price_above_ema50) & (~ema50_above_ema200) & (rsi < 45) & (mom72 < 0)
    # LONGABLE : structure haussière
    longable_mask  = price_above_ema50 & ema50_above_ema200 & (rsi > 40)

    regime = np.where(no_long_mask, "NO_LONG",
             np.where(longable_mask, "LONGABLE", "NEUTRAL"))
    df[REGIME_COL_LONG] = regime

    n_total    = len(df)
    n_no_long  = int((regime == "NO_LONG").sum())
    n_longable = int((regime == "LONGABLE").sum())
    n_neutral  = int((regime == "NEUTRAL").sum())
    print(f"   Régimes LONG : NO_LONG={n_no_long/n_total:.1%}  "
          f"LONGABLE={n_longable/n_total:.1%}  "
          f"NEUTRAL={n_neutral/n_total:.1%}")

    return df


def compute_regime_col(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule regime_short = "SHORTABLE" | "NEUTRAL" | "NO_SHORT".

    Basé sur des indicateurs techniques déterministes :
      NO_SHORT   = biais haussier actif  → prix > EMA50 ET EMA50 > EMA200 ET RSI > 55
      SHORTABLE  = structure baissière   → prix < EMA50 ET (EMA50 < EMA200 OU RSI < 50)
      NEUTRAL    = tout le reste         → range, correction modérée

    Requiert les colonnes : dist_ema_50, ema_spread_50_200, rsi_14
    (toutes présentes dans FEATURES_COMMON).

    Le régime est une gate dure dans le backtest, pas une feature du modèle.
    Ne jamais inclure REGIME_COL dans les listes de features.
    """
    df = df.copy()

    required = ["dist_ema_50", "ema_spread_50_200", "rsi_14"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        print(f"   ⚠  compute_regime_col: colonnes manquantes {missing} → régime = NEUTRAL partout")
        df[REGIME_COL] = "NEUTRAL"
        return df

    # dist_ema_50 > 0 ⟺ prix > EMA50
    price_above_ema50  = df["dist_ema_50"] > 0
    ema50_above_ema200 = df["ema_spread_50_200"] > 0  # EMA50 > EMA200
    rsi_bearish        = df["rsi_14"] < 48   # rsi < 48 au lieu de 50 — plus strict
    rsi_bullish        = df["rsi_14"] > 55

    no_short_mask   = price_above_ema50 & ema50_above_ema200 & rsi_bullish

    # SHORTABLE renforcé : exige les DEUX conditions structurelles (vs une seule avant)
    dist_high_24 = df["dist_from_local_high_24"] if "dist_from_local_high_24" in df.columns \
                   else pd.Series(-0.05, index=df.index)
    shortable_strict = (~price_above_ema50) & (~ema50_above_ema200) & rsi_bearish
    shortable_extra  = (~price_above_ema50) & (df["rsi_14"] < 42) & (dist_high_24 < -0.015)
    shortable_mask   = shortable_strict | shortable_extra

    regime = np.where(no_short_mask, "NO_SHORT",
             np.where(shortable_mask, "SHORTABLE", "NEUTRAL"))
    df[REGIME_COL] = regime

    n_total     = len(df)
    n_no_short  = int((regime == "NO_SHORT").sum())
    n_shortable = int((regime == "SHORTABLE").sum())
    n_neutral   = int((regime == "NEUTRAL").sum())
    print(f"   Régimes : NO_SHORT={n_no_short/n_total:.1%}  "
          f"SHORTABLE={n_shortable/n_total:.1%}  "
          f"NEUTRAL={n_neutral/n_total:.1%}")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Factory principale
# ─────────────────────────────────────────────────────────────────────────────

def build_labels(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    tradeable_quantile: float = TRADEABLE_QUANTILE_LONG,
    tradeable_quantile_short: float = TRADEABLE_QUANTILE_SHORT,
    cost_pct: float = COST_PCT,
    gray_zone_factor: float = GRAY_ZONE_FACTOR_LONG,
    gray_zone_factor_short: float = GRAY_ZONE_FACTOR_SHORT,
    use_reversal_filter: bool = True,
    use_long_reversal_filter: bool = True,
    use_regime_filter: bool = True,
    use_long_regime: bool = True,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Construit tous les labels avec asymétrie structurelle long vs short.

    Arguments
    ---------
    df                       : DataFrame avec TARGET_COL (future_ret_h)
                               et TARGET_REVERSAL_COL (future_ret_h3_max) si use_reversal_filter
    train_mask               : masque booléen train (calibration des seuils)
    tradeable_quantile       : quantile long (défaut 0.88)
    tradeable_quantile_short : quantile short (défaut 0.82) — plus élevé → plus strict
    cost_pct                 : coût round-trip long
    gray_zone_factor         : zone grise long (défaut 0.20)
    gray_zone_factor_short   : zone grise short (défaut 0.25)
    use_reversal_filter      : appliquer la contrainte de non-retournement (défaut True)
    use_regime_filter        : calculer la colonne régime (défaut True)

    Retourne
    --------
    df_labeled : DataFrame avec colonnes labels et régime ajoutées
    stats      : dict de statistiques
    """
    assert_horizon(HORIZON_BARS, "build_labels")

    if TARGET_COL not in df.columns:
        raise RuntimeError(
            f"Colonne '{TARGET_COL}' manquante. "
            f"Le CSV doit contenir les rendements forward {HORIZON_BARS} barre(s)."
        )

    ret = df[TARGET_COL].values.astype(np.float64)

    # ── Seuils calibrés UNIQUEMENT sur train ──────────────────────────────────
    ret_train     = ret[train_mask]
    thr_long_raw  = float(np.quantile(np.abs(ret_train), tradeable_quantile))
    thr_long      = max(thr_long_raw, LONG_MIN_ABS_RETURN + cost_pct)
    thr_short_raw = float(np.quantile(np.abs(ret_train), tradeable_quantile_short))

    cost_short   = cost_pct * COST_SHORT_MULT
    thr_short    = thr_short_raw + cost_short

    # ── Label long strict ─────────────────────────────────────────────────────
    raw_long_signal = ret > thr_long

    n_reversal_long_rejected = 0
    if use_long_reversal_filter and TARGET_REVERSAL_COL_LONG in df.columns:
        h3_min    = df[TARGET_REVERSAL_COL_LONG].values.astype(np.float64)
        no_dip    = h3_min > -thr_long * NON_REVERSAL_THRESHOLD_FACTOR_LONG
        y_long_pos  = raw_long_signal & no_dip
        y_long_dip  = raw_long_signal & (~no_dip)
        y_long = np.zeros(len(ret), dtype=np.int8)
        y_long[y_long_pos] = 1
        y_long[y_long_dip] = -1
        n_reversal_long_rejected = int(y_long_dip.sum())
    else:
        y_long = raw_long_signal.astype(np.int8)
        if use_long_reversal_filter:
            print(f"   ⚠  {TARGET_REVERSAL_COL_LONG} absent — filtre non-retournement long désactivé")

    if gray_zone_factor > 0.0:
        thr_long_hi = thr_long * (1.0 + gray_zone_factor)
        gray_long   = (ret > thr_long) & (ret < thr_long_hi) & (y_long == 1)
        y_long[gray_long] = -1

    # ── Label tradeable (commun) ──────────────────────────────────────────────
    tradeable = ((ret > thr_long) | (ret < -thr_short_raw)).astype(np.int8)

    # ── Label short — avec contrainte de non-retournement ────────────────────
    raw_short_signal = ret < -thr_short

    if use_reversal_filter and TARGET_REVERSAL_COL in df.columns:
        h3_max = df[TARGET_REVERSAL_COL].values.astype(np.float64)
        no_reversal = h3_max < thr_short_raw * NON_REVERSAL_THRESHOLD_FACTOR

        y_short_pos = raw_short_signal & no_reversal
        y_short_reversal_gray = raw_short_signal & (~no_reversal)

        y_short = np.zeros(len(ret), dtype=np.int8)
        y_short[y_short_pos]           = 1
        y_short[y_short_reversal_gray] = -1

        n_reversal_rejected = int(y_short_reversal_gray.sum())
    else:
        y_short = raw_short_signal.astype(np.int8)
        n_reversal_rejected = 0
        if use_reversal_filter:
            print(f"   ⚠  {TARGET_REVERSAL_COL} absent — filtre non-retournement désactivé")
            print(f"      → Appeler compute_short_reversal_col(df) avant build_labels()")

    if gray_zone_factor_short > 0.0:
        thr_short_lo = thr_short * (1.0 + gray_zone_factor_short)
        gray_short   = (ret < -thr_short) & (ret > -thr_short_lo)
        y_short[gray_short] = -1

    # ── Labels nets de coût ────────────────────────────────────────────────────
    y_long_net  = (ret > thr_long + cost_pct).astype(np.int8)
    y_short_net = (ret < -(thr_short + cost_pct)).astype(np.int8)

    # ── Régimes (gates contextuelles) ────────────────────────────────────────
    if use_regime_filter and REGIME_COL not in df.columns:
        df = compute_regime_col(df)
    elif not use_regime_filter:
        df = df.copy()
        df[REGIME_COL] = "NEUTRAL"
    else:
        df = df.copy()

    if use_long_regime and REGIME_COL_LONG not in df.columns:
        df = compute_long_regime_col(df)

    # ── Écriture dans le DataFrame ────────────────────────────────────────────
    df["tradeable_net"] = tradeable
    df["y_long"]        = y_long
    df["y_short"]       = y_short
    df["y_long_net"]    = y_long_net
    df["y_short_net"]   = y_short_net

    # ── Statistiques ─────────────────────────────────────────────────────────
    n           = len(df)
    n_tr        = int(tradeable.sum())
    n_long      = int((y_long  == 1).sum())
    n_short     = int((y_short == 1).sum())
    n_lgray     = int((y_long  == -1).sum())
    n_sgray     = int((y_short == -1).sum())
    n_lnet      = int(y_long_net.sum())
    n_snet      = int(y_short_net.sum())
    regime_dist      = df[REGIME_COL].value_counts().to_dict()      if REGIME_COL      in df.columns else {}
    regime_long_dist = df[REGIME_COL_LONG].value_counts().to_dict() if REGIME_COL_LONG in df.columns else {}

    stats: Dict = {
        "thr_long":                  round(thr_long, 6),
        "thr_long_raw_quantile":     round(thr_long_raw, 6),
        "thr_short_raw":             round(thr_short_raw, 6),
        "thr_short_with_cost":       round(thr_short, 6),
        "cost_pct_long":             cost_pct,
        "cost_pct_short":            round(cost_short, 6),
        "tradeable_quantile_long":   tradeable_quantile,
        "tradeable_quantile_short":  tradeable_quantile_short,
        "n_total":          n,
        "n_tradeable":      n_tr,
        "n_long":           n_long,
        "n_short":          n_short,
        "n_long_gray":      n_lgray,
        "n_short_gray":     n_sgray,
        "n_reversal_rejected":      n_reversal_rejected,
        "n_reversal_long_rejected": n_reversal_long_rejected,
        "n_long_net":       n_lnet,
        "n_short_net":      n_snet,
        "frac_long":        round(n_long  / n, 4),
        "frac_short":       round(n_short / n, 4),
        "long_vs_short_ratio": round(n_long / max(n_short, 1), 4),
        "regime_distribution":      regime_dist,
        "regime_long_distribution": regime_long_dist,
        "warning_low_long":      n_long  < 500,
        "warning_low_short":     n_short < 300,
        "warning_imbalanced":    n_long / max(n_short, 1) > 3.0,
        "warning_short_too_rare": n_short / max(n, 1) < 0.03,
    }

    print(f"\n   Labels construits :")
    print(f"   LONG  : {n_long:,} ({n_long/n:.1%})  gray={n_lgray:,}  "
          f"thr={thr_long:.5f}  reversal_rejetés_long={n_reversal_long_rejected:,}")
    print(f"   SHORT : {n_short:,} ({n_short/n:.1%})  gray={n_sgray:,}  reversal_rejetés={n_reversal_rejected:,}")
    print(f"           thr_brut={thr_short_raw:.5f}  thr_net={thr_short:.5f}  cost_short={cost_short:.5f}")

    if stats["warning_low_short"]:
        print(f"   ⚠  Peu d'exemples SHORT ({n_short}) — "
              f"ajuster tradeable_quantile_short ou vérifier les données")
    if stats["warning_imbalanced"]:
        print(f"   ⚠  Déséquilibre long/short ({stats['long_vs_short_ratio']:.1f}x) — normal en crypto haussier")
    if stats["warning_short_too_rare"]:
        print(f"   ⚠  SHORT < 3% du dataset ({n_short/n:.2%}) — trop rare pour entraîner")

    return df, stats


def build_bear_regime_label(
    df: pd.DataFrame,
    horizon_bars: int = 72,
    threshold: float = -0.02,
) -> pd.Series:
    """
    Construit le label y_bear_regime pour le méta-modèle de régime.

    Méthode : label STRUCTUREL backward-looking (pas de leakage futur).

    y_bear_regime[t] = 1 si la barre t est en régime structurellement baissier,
    défini par la combinaison de :
      (a) prix < EMA50 (dist_ema_50 < 0)
      (b) EMA50 < EMA200 (ema_spread_50_200 < 0)  ← mort croisée
      (c) RSI < 50 (pression baissière)
      (d) mom_logret_72 < 0  ← tendance 3j négative

    `horizon_bars` et `threshold` sont conservés pour compatibilité API
    mais ne sont plus utilisés dans la méthode structurelle.
    """
    if REGIME_COL in df.columns:
        y_bear = (df[REGIME_COL] == "SHORTABLE").astype(np.int8)
        return y_bear

    has_dist50    = "dist_ema_50" in df.columns
    has_spread    = "ema_spread_50_200" in df.columns
    has_rsi       = "rsi_14" in df.columns
    has_mom72     = "mom_logret_72" in df.columns

    if not (has_dist50 and has_spread):
        if "Close" in df.columns:
            close = df["Close"]
        elif "close" in df.columns:
            close = df["close"]
        else:
            return pd.Series(np.zeros(len(df), dtype=np.int8), index=df.index)

        forward_ret = close.shift(-horizon_bars) / close - 1.0
        y_bear = (forward_ret < threshold).astype(np.int8)
        y_bear.iloc[-horizon_bars:] = -1
        return y_bear

    price_below_ema50  = df["dist_ema_50"] < 0
    ema50_below_ema200 = df["ema_spread_50_200"] < 0
    rsi_bearish        = df["rsi_14"] < 50     if has_rsi   else pd.Series(True, index=df.index)
    mom_neg            = df["mom_logret_72"] < 0 if has_mom72 else pd.Series(True, index=df.index)

    y_bear = (price_below_ema50 & ema50_below_ema200 & rsi_bearish & mom_neg).astype(np.int8)
    return y_bear


def build_pnl_labels(
    df: pd.DataFrame,
    cost_pct: float = COST_PCT,
    cost_short_mult: float = COST_SHORT_MULT,
) -> pd.DataFrame:
    """
    Construit les cibles PnL pour la régression.

    Garantit la cohérence entre l'entraînement et le backtest :
    la même définition doit être utilisée aux deux endroits.

      y_long_pnl  = ret - 2*fee       (entrée + sortie round-trip)
      y_short_pnl = -ret - 2*fee_s    (position courte + couverture)

    Appeler cette fonction depuis train_pipeline.py pour construire les cibles
    de régression — ne jamais re-définir cette formule ailleurs.
    """
    df = df.copy()
    ret = df[TARGET_COL].values.astype(np.float64)
    cost_short = cost_pct * cost_short_mult
    df["y_long_pnl"]  = ret - PNL_COST_MULT * cost_pct
    df["y_short_pnl"] = -ret - PNL_COST_MULT * cost_short
    return df


def get_train_labels(df: pd.DataFrame, mask: np.ndarray,
                     label_col: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Extrait les labels pour l'entraînement, en excluant les gray zones (-1).
    Retourne (y_clean, valid_mask).
    """
    y     = df.loc[mask, label_col].values.astype(np.int32)
    valid = y >= 0
    return y[valid], valid
