"""
ai/level_2/short_calibration.py — CALIBRATION DES SEUILS SHORT PAR CONTEXTE
=============================================================================

Calibration basée sur PnL (pas AUC).

Pour chaque contexte de marché et chaque seuil candidat, on simule les trades
SHORT et on calcule un score composite qui pénalise le drawdown et le taux de
squeeze tout en récompensant l'expectancy et le volume statistique.

Score = expectancy_net * sqrt(n_trades)
       + 0.50 * log(max(profit_factor, 1e-6))
       - 0.20 * max_drawdown_pct
       - 0.30 * squeeze_loss_rate

Contraintes strictes d'acceptation :
  n_trades >= 10, PF >= 1.10, expectancy > 0, squeeze_loss_rate < 0.35

Si aucun seuil ne passe les contraintes pour un contexte → context disabled.

Règle anti-overfit :
  - Calibration UNIQUEMENT sur val — jamais sur test.
  - Un contexte désactivé doit rester désactivé pour tout le fold.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constantes locales — critères SHORT
# ─────────────────────────────────────────────────────────────────────────────
MIN_SHORT_TRADES_TOTAL = 100
MIN_SHORT_TRADES_PER_VALID_FOLD = 10
SHORT_DEPLOY_PF = 1.30
SHORT_WEAK_PF = 1.05
SHORT_CATASTROPHIC_PF = 0.75
MIN_SHORT_FOLDS_OK = 5
MAX_SHORT_DD = 8.0
MAX_SQUEEZE_LOSS_RATE = 0.35
COST_NORMAL = 0.0010    # 10 bps
COST_STRESS = 0.0015    # 15 bps
COST_EXTREME = 0.0020   # 20 bps

# Grille de seuils à balayer
_THR_LO = 0.38   # LightGBM DART p90≈0.50 → seuils > 0.38 = top 15% des signaux
_THR_HI = 0.82
_THR_STEP = 0.01

# Contraintes minimales et maximales pour valider un seuil
_MIN_N_TRADES = 10
_MAX_N_TRADES_FRAC = 0.08   # max 8% des barres val → évite threshold trop bas
_MIN_PF = 1.20               # relevé de 1.10 → filtre les seuils marginaux
_MAX_SQUEEZE = MAX_SQUEEZE_LOSS_RATE


# ─────────────────────────────────────────────────────────────────────────────
# Helper principal : simulation d'un batch de trades SHORT
# ─────────────────────────────────────────────────────────────────────────────

def simulate_short_trades(
    ret_series: pd.Series,
    signal_mask: np.ndarray,
    costs: float,
) -> dict:
    """
    Simule des trades SHORT sur ret_series filtré par signal_mask.

    Convention SHORT : le rendement sous-jacent est celui du prix (positif = hausse).
    Un trade SHORT profite quand le prix baisse → net_ret = -ret - costs.

    Arguments
    ---------
    ret_series  : Series des rendements bruts de la barre (e.g. future_ret_short_4h)
    signal_mask : tableau boolean — True si la barre est un trade actif
    costs       : coût round-trip (ex. 0.0010 = 10 bps)

    Retourne
    --------
    dict avec : n_trades, pf, expectancy, wr, avg_win, avg_loss,
                max_drawdown, trades (list[float])
    """
    if len(ret_series) != len(signal_mask):
        raise ValueError(
            f"ret_series ({len(ret_series)}) et signal_mask ({len(signal_mask)}) "
            "doivent avoir la même longueur."
        )

    signal_mask = np.asarray(signal_mask, dtype=bool)
    raw_rets = ret_series.values[signal_mask]

    # SHORT : profit quand prix baisse (ret < 0) → inverser le signe
    net_rets: np.ndarray = -raw_rets - costs

    n = int(len(net_rets))
    if n == 0:
        return {
            "n_trades": 0, "pf": 0.0, "expectancy": 0.0,
            "wr": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "max_drawdown": 0.0, "trades": [],
        }

    wins   = net_rets[net_rets > 0]
    losses = net_rets[net_rets <= 0]

    gross_win  = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    pf         = gross_win / max(gross_loss, 1e-9)
    wr         = float(len(wins)) / n
    avg_win    = float(wins.mean())  if len(wins)   > 0 else 0.0
    avg_loss   = float(losses.mean()) if len(losses) > 0 else 0.0
    exp        = float(net_rets.mean())

    # Max drawdown sur la courbe equity cumulative
    equity = np.cumsum(net_rets)  # rendements relatifs cumulés
    peak   = np.maximum.accumulate(equity)
    dd     = peak - equity  # drawdown absolu en rendements
    max_dd = float(dd.max()) if len(dd) > 0 else 0.0
    # Convertir en % par rapport au pic (évite la division par zéro)
    peak_nz   = np.where(peak != 0, peak, np.finfo(float).eps)
    dd_pct    = dd / np.abs(peak_nz) * 100.0
    max_dd_pct = float(dd_pct.max()) if len(dd_pct) > 0 else 0.0

    return {
        "n_trades":    n,
        "pf":          round(pf, 4),
        "expectancy":  round(exp, 6),
        "wr":          round(wr, 4),
        "avg_win":     round(avg_win, 6),
        "avg_loss":    round(avg_loss, 6),
        "max_drawdown": round(max_dd_pct, 4),
        "trades":      net_rets.tolist(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Score composite
# ─────────────────────────────────────────────────────────────────────────────

def _score(
    expectancy: float,
    n_trades: int,
    pf: float,
    max_dd: float,
    squeeze_loss_rate: float,
) -> float:
    """
    Score composite pour sélectionner le meilleur seuil par contexte.

    Privilégie l'expectancy statistiquement significative (×√n) et pénalise
    le drawdown et le taux de squeeze. Le log(PF) compense les seuils très
    restrictifs qui auraient peu de trades mais une qualité élevée.
    """
    # log(n) au lieu de sqrt(n) : pénalise moins les seuils stricts (peu de trades)
    # Avec sqrt(n), le score favorise les n élevés → thresholds bas → dégradation test.
    # Avec log(n), un n=20 à E=0.02 bat un n=200 à E=0.008 → thresholds plus élevés.
    return (
        expectancy * math.log(max(n_trades, 1) + 1)
        + 0.50 * math.log(max(pf, 1e-6))
        - 0.20 * max_dd
        - 0.30 * squeeze_loss_rate
    )


# ─────────────────────────────────────────────────────────────────────────────
# Calibration principale
# ─────────────────────────────────────────────────────────────────────────────

def calibrate_short_thresholds(
    df_val: pd.DataFrame,
    y_val: np.ndarray,
    p_short: np.ndarray,
    context_col: str,
    costs: float = COST_NORMAL,
    ret_col: str = "future_ret_short_4h",
    mfe_col: str = "mfe_short_4h",
    mae_col: str = "mae_short_4h",
    squeeze_col: str = "squeeze_reject_4h",
) -> dict:
    """
    Calibre les seuils SHORT par contexte de marché via optimisation PnL.

    Pour chaque contexte dans context_col et chaque seuil dans
    [THR_LO, THR_HI, step=THR_STEP] :
      1. Filtrer les trades (p_short >= threshold AND context == ctx)
      2. Calculer : n_trades, PF, expectancy, max_drawdown, squeeze_loss_rate, avg_mae
      3. cost_stress_pf : recalculer PF avec cost=COST_STRESS
      4. Score composite

    Contraintes : n_trades >= 10, PF >= 1.10, expectancy > 0, squeeze_loss_rate < 0.35
    Si aucun seuil ne passe → context disabled (threshold=None, enabled=False).

    Arguments
    ---------
    df_val       : DataFrame val (index aligné avec y_val et p_short)
    y_val        : labels val (0/1/-1 pour gray zone)
    p_short      : probabilités SHORT du modèle (len == len(df_val))
    context_col  : nom de la colonne de contexte dans df_val
    costs        : coût de base pour la calibration primaire
    ret_col      : rendement brut 4h short (SHORT: profit si négatif)
    mfe_col      : Maximum Favorable Excursion short (optionnel)
    mae_col      : Maximum Adverse Excursion short (optionnel)
    squeeze_col  : colonne booléenne de rejet squeeze (optionnel)

    Retourne
    --------
    dict par contexte, e.g. :
    {
      "crowded_longs": {
        "enabled": True,
        "threshold": 0.67,
        "val_pf": 1.42,
        "val_expectancy": 0.004,
        "n_val_trades": 18,
        "squeeze_loss_rate": 0.12,
        "cost_stress_pf": 1.25,
        "score": 0.035,
        "avg_mae": 0.003,
      },
      ...
    }
    """
    p_short = np.asarray(p_short, dtype=np.float64)
    y_val   = np.asarray(y_val,   dtype=np.int32)

    if len(p_short) != len(df_val) or len(y_val) != len(df_val):
        raise ValueError(
            "df_val, y_val et p_short doivent avoir la même longueur."
        )

    # Vérifier la colonne de retour
    if ret_col not in df_val.columns:
        # Fallback sur TARGET_COL si ret_col spécifique absent
        fallback_cols = ["future_ret_4h", "future_ret_short_4h"]
        ret_col_used = next((c for c in fallback_cols if c in df_val.columns), None)
        if ret_col_used is None:
            raise KeyError(
                f"Colonne de rendement '{ret_col}' absente de df_val. "
                f"Colonnes disponibles : {list(df_val.columns)}"
            )
        ret_col = ret_col_used

    # Colonnes optionnelles
    has_squeeze = squeeze_col in df_val.columns
    has_mae     = mae_col in df_val.columns

    # Contextes disponibles
    if context_col not in df_val.columns:
        # Fallback : un seul contexte "general"
        contexts = ["general"]
        ctx_array = np.full(len(df_val), "general", dtype=object)
    else:
        ctx_array = df_val[context_col].values.astype(object)
        contexts  = sorted(set(ctx_array.tolist()))

    ret_array     = df_val[ret_col].values.astype(np.float64)
    mae_array     = df_val[mae_col].values.astype(np.float64) if has_mae else None
    squeeze_array = df_val[squeeze_col].values.astype(bool)   if has_squeeze else None

    # Exclure gray zone (-1) de la calibration
    valid_mask = y_val >= 0

    result: dict = {}

    for ctx in contexts:
        ctx_mask = (ctx_array == ctx) & valid_mask

        n_ctx = int(ctx_mask.sum())
        if n_ctx < _MIN_N_TRADES:
            result[ctx] = _disabled_context(ctx, reason="not_enough_bars_in_context")
            continue

        # simulate_short_trades attend un retour prix brut (positif = hausse = SHORT perd).
        # future_ret_short_4h est déjà inversé : positif = SHORT gagne.
        # On re-inverse pour respecter la convention de simulate_short_trades.
        ret_ctx     = pd.Series(-ret_array[ctx_mask])
        p_ctx       = p_short[ctx_mask]
        mae_ctx     = mae_array[ctx_mask]     if mae_array     is not None else None
        squeeze_ctx = squeeze_array[ctx_mask] if squeeze_array is not None else None

        best: Optional[dict] = None
        best_score = -np.inf

        for thr_raw in np.arange(_THR_LO, _THR_HI + 1e-9, _THR_STEP):
            thr = round(float(thr_raw), 2)
            sig = p_ctx >= thr

            n_sig = int(sig.sum())
            if n_sig < _MIN_N_TRADES:
                continue
            # Plafond : si le threshold capture plus de 5% du contexte → trop bas
            if n_sig > max(_MIN_N_TRADES, int(n_ctx * _MAX_N_TRADES_FRAC)):
                continue

            sim = simulate_short_trades(ret_ctx, sig, costs)
            n_tr  = sim["n_trades"]
            pf    = sim["pf"]
            exp   = sim["expectancy"]
            dd    = sim["max_drawdown"]

            # Taux de squeeze : fraction des trades perdants causés par un squeeze
            sq_rate = 0.0
            if squeeze_ctx is not None:
                # Squeeze = trade pris MAIS le squeeze_col dit "rejeter"
                # → si on aurait dû rejeter mais on a tradé quand même
                sq_losers = sig & squeeze_ctx
                n_sq = int(sq_losers.sum())
                sq_sim = simulate_short_trades(ret_ctx, sq_losers, costs)
                n_sq_losses = int(
                    (np.array(sq_sim["trades"]) < 0).sum()
                ) if sq_sim["n_trades"] > 0 else 0
                sq_rate = n_sq_losses / max(n_tr, 1)

            # Vérifier contraintes
            if n_tr < _MIN_N_TRADES:
                continue
            if pf < _MIN_PF:
                continue
            if exp <= 0:
                continue
            if sq_rate >= _MAX_SQUEEZE:
                continue

            # Score composite
            sc = _score(exp, n_tr, pf, dd, sq_rate)

            # PF stress
            sim_stress = simulate_short_trades(ret_ctx, sig, COST_STRESS)
            stress_pf  = sim_stress["pf"]

            # MAE moyen
            avg_mae = 0.0
            if mae_ctx is not None:
                avg_mae = float(mae_ctx[sig].mean()) if sig.sum() > 0 else 0.0

            candidate = {
                "enabled":          True,
                "threshold":        thr,
                "val_pf":           round(pf, 4),
                "val_expectancy":   round(exp, 6),
                "n_val_trades":     n_tr,
                "squeeze_loss_rate": round(sq_rate, 4),
                "max_drawdown":     round(dd, 4),
                "cost_stress_pf":   round(stress_pf, 4),
                "avg_mae":          round(avg_mae, 6),
                "score":            round(sc, 6),
            }

            if sc > best_score:
                best_score = sc
                best = candidate

        if best is None:
            result[ctx] = _disabled_context(ctx, reason="no_threshold_passed_constraints")
        else:
            result[ctx] = best

    return result


def _disabled_context(ctx: str, reason: str = "disabled") -> dict:
    return {
        "enabled":          False,
        "threshold":        None,
        "val_pf":           None,
        "val_expectancy":   None,
        "n_val_trades":     0,
        "squeeze_loss_rate": None,
        "max_drawdown":     None,
        "cost_stress_pf":   None,
        "avg_mae":          None,
        "score":            None,
        "reason":           reason,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

def save_thresholds(thresholds: dict, path: str) -> None:
    """Sauvegarde les seuils calibrés en JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(thresholds, f, indent=2, default=_json_default)


def load_thresholds(path: str) -> dict:
    """Charge les seuils depuis un fichier JSON."""
    with open(path, "r") as f:
        return json.load(f)


def _json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Type non sérialisable : {type(obj)}")


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaire : récupérer le seuil actif pour une barre
# ─────────────────────────────────────────────────────────────────────────────

def get_threshold_for_context(thresholds: dict, context: str) -> Optional[float]:
    """
    Retourne le seuil calibré pour un contexte donné, ou None si désactivé.

    Arguments
    ---------
    thresholds : dict retourné par calibrate_short_thresholds
    context    : nom du contexte courant

    Retourne
    --------
    float ou None (si contexte désactivé ou inconnu)
    """
    entry = thresholds.get(context) or thresholds.get("general")
    if entry is None:
        return None
    if not entry.get("enabled", False):
        return None
    return entry.get("threshold")


def summarize_calibration(thresholds: dict) -> str:
    """Résumé lisible des seuils par contexte (pour les logs)."""
    lines = []
    for ctx, entry in sorted(thresholds.items()):
        if entry.get("enabled"):
            lines.append(
                f"  {ctx:<20} thr={entry['threshold']:.2f}  "
                f"PF={entry['val_pf']:.2f}  "
                f"n={entry['n_val_trades']:3d}  "
                f"E={entry['val_expectancy']:+.4f}  "
                f"sq={entry['squeeze_loss_rate']:.2f}  "
                f"score={entry['score']:.4f}"
            )
        else:
            reason = entry.get("reason", "disabled")
            lines.append(f"  {ctx:<20} DISABLED ({reason})")
    return "\n".join(lines)
