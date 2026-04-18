"""
core/labels_pnl.py — LABELS PnL RÉGRESSION
============================================

Remplace les labels binaires y_long/y_short par des cibles continues :

  y_long_pnl  = future_ret_h − 2 × fee   (PnL net long, en fraction)
  y_short_pnl = −future_ret_h − 2 × fee  (PnL net short, en fraction)

Règle de trading au backtest :
  if pnl_pred > fee + margin → trade
  else → skip

Ce format permet à XGBRegressor d'apprendre l'amplitude du mouvement,
pas seulement sa direction — ce qui est plus difficile mais plus informatif.
Le modèle peut alors calibrer la taille de position proportionnellement à
la prédiction (Kelly fractionnel).
"""
from __future__ import annotations

from typing import Optional, Tuple
import numpy as np
import pandas as pd


def build_pnl_labels(
    df: pd.DataFrame,
    fee: float = 0.001,
    horizon: int = 1,
) -> pd.DataFrame:
    """
    Ajoute les colonnes de labels PnL continus dans df.

    Paramètres
    ----------
    df      : DataFrame avec colonne `future_ret_h` (log-return horizon h)
    fee     : coût aller-retour (ex: 0.001 = 10 bps)
    horizon : nombre de barres pour le label (1 = 1h si données 1h)
              Pour horizon > 1, future_ret_h doit être déjà calculé sur ce horizon.

    Colonnes ajoutées
    -----------------
    y_long_pnl    : PnL net du long  = future_ret_h - 2*fee
    y_short_pnl   : PnL net du short = -future_ret_h - 2*fee
    y_long_sign   : 1 si y_long_pnl > 0, 0 sinon  (pour diagnostics)
    y_short_sign  : 1 si y_short_pnl > 0, 0 sinon
    """
    df = df.copy()
    ret = df["future_ret_h"].astype(np.float64)

    df["y_long_pnl"]   = ret  - 2.0 * fee
    df["y_short_pnl"]  = -ret - 2.0 * fee
    df["y_long_sign"]  = (df["y_long_pnl"]  > 0.0).astype(np.int32)
    df["y_short_sign"] = (df["y_short_pnl"] > 0.0).astype(np.int32)

    return df


def pnl_label_stats(df: pd.DataFrame) -> dict:
    """Statistiques descriptives des labels PnL — utile pour le diagnostic."""
    out = {}
    for col in ["y_long_pnl", "y_short_pnl"]:
        if col not in df.columns:
            continue
        v = df[col].dropna().values
        out[col] = {
            "mean":       round(float(np.mean(v)), 6),
            "std":        round(float(np.std(v)), 6),
            "pct_positive": round(float((v > 0).mean()), 4),
            "p25":        round(float(np.percentile(v, 25)), 6),
            "p75":        round(float(np.percentile(v, 75)), 6),
            "max":        round(float(np.max(v)), 6),
            "min":        round(float(np.min(v)), 6),
        }
    return out


def decision_threshold_regression(
    pnl_pred: np.ndarray,
    fee: float = 0.001,
    margin: float = 0.001,
) -> np.ndarray:
    """
    Règle de décision pour le régresseur PnL.

    Trade si pnl_pred > fee + margin.
    margin contrôle la sélectivité : plus grand = moins de trades, meilleure qualité.

    Retourne un masque booléen.
    """
    return pnl_pred > (fee + margin)


def top_percentile_filter(
    pnl_pred: np.ndarray,
    top_pct: float = 0.01,
) -> np.ndarray:
    """
    Filtre par centile supérieur — garde uniquement le top X% des prédictions.

    top_pct=0.01 → top 1% (objectif: <1% des signaux = trades très sélectifs).
    Retourne un masque booléen.
    """
    threshold = np.percentile(pnl_pred, (1.0 - top_pct) * 100.0)
    return pnl_pred >= threshold


def calibrate_regression_margin(
    pnl_pred_val: np.ndarray,
    y_true_val: np.ndarray,
    fee: float = 0.001,
    target_win_rate: float = 0.55,
    min_trades: int = 30,
) -> float:
    """
    Calibre la margin optimale sur le validation set.

    Cherche la margin minimale telle que win_rate ≥ target_win_rate
    avec au moins min_trades trades. Retourne la margin calibrée.
    """
    best_margin = 0.001
    for margin in np.arange(0.0, 0.02, 0.0005):
        mask = decision_threshold_regression(pnl_pred_val, fee, margin)
        n = int(mask.sum())
        if n < min_trades:
            break
        wr = float((y_true_val[mask] > 0).mean())
        if wr >= target_win_rate:
            best_margin = margin
    return best_margin
