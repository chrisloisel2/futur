"""
level_0/filter_calibrate.py — CALIBRATION DU SEUIL FILTRE
==========================================================

Calibre le seuil de décision du filtre sur la validation uniquement.

Principe :
  On ne cherche pas le seuil qui maximise F1 brut.
  On cherche le seuil qui maximise l'impact business :
    - Pour LONG : favoriser le recall (F-beta > 1) — mieux vaut ne pas manquer
      une opportunité que de rejeter un trade correct.
    - Pour SHORT : balanced (F-beta = 1) — le short doit être plus sélectif.

Convention anti-overfit :
  - Calibration TOUJOURS sur val (jamais test)
  - Vérifier la stabilité du seuil optimal (variance du score autour de ±0.05)
  - Un seuil fragile (score chute > 30% si ±0.05) indique un overfit
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np


def calibrate_filter_threshold(
    proba: np.ndarray,
    y_true: np.ndarray,
    beta: float = 1.5,
    min_positives: int = 20,
) -> float:
    """
    Choisit le seuil optimal par maximisation du F-beta sur la validation.

    F-beta = (1 + beta²) × précision × rappel / (beta² × précision + rappel)

    beta > 1 → favorise le recall (moins de trades manqués)
    beta = 1 → F1 standard
    beta < 1 → favorise la précision (moins de faux positifs)

    Arguments
    ---------
    proba         : probabilités P(tradeable=1) sur val
    y_true        : vrais labels sur val
    beta          : paramètre F-beta
    min_positives : nombre minimal de prédictions positives pour accepter un seuil

    Retourne
    --------
    float : seuil optimal dans [0.10, 0.90]
    """
    best_score, best_thr = 0.0, 0.40
    sweep = threshold_sweep(proba, y_true, beta=beta,
                            min_positives=min_positives)
    for entry in sweep:
        if entry["fbeta"] > best_score:
            best_score = entry["fbeta"]
            best_thr   = entry["threshold"]
    return best_thr


def threshold_sweep(
    proba: np.ndarray,
    y_true: np.ndarray,
    beta: float = 1.0,
    min_positives: int = 10,
) -> List[Dict]:
    """
    Balaye les seuils de 0.10 à 0.90 et calcule les métriques pour chacun.
    Retourne une liste de dicts triée par threshold croissant.
    """
    results = []
    for thr in np.arange(0.10, 0.91, 0.02):
        y_pred = (proba >= thr).astype(int)
        tp = int(((y_pred == 1) & (y_true == 1)).sum())
        fp = int(((y_pred == 1) & (y_true == 0)).sum())
        fn = int(((y_pred == 0) & (y_true == 1)).sum())
        n_pred_pos = tp + fp

        if n_pred_pos < min_positives:
            continue

        prec = tp / max(tp + fp, 1)
        rec  = tp / max(tp + fn, 1)
        fb   = (1 + beta**2) * prec * rec / max(beta**2 * prec + rec, 1e-9)

        results.append({
            "threshold":    round(float(thr), 2),
            "precision":    round(prec, 4),
            "recall":       round(rec, 4),
            "fbeta":        round(fb, 4),
            "n_predicted_positive": n_pred_pos,
            "tp": tp, "fp": fp, "fn": fn,
        })

    return results


def check_threshold_stability(
    proba: np.ndarray,
    y_true: np.ndarray,
    best_thr: float,
    beta: float = 1.0,
    delta: float = 0.05,
    max_drop_pct: float = 0.30,
) -> Tuple[bool, str]:
    """
    Vérifie que le seuil optimal est stable (pas d'overfit).

    Un seuil est "fragile" si le score F-beta chute de plus de max_drop_pct
    lorsqu'on déplace le seuil de ±delta.

    Retourne (is_stable, message)
    """
    def fbeta_at(thr):
        y_pred = (proba >= thr).astype(int)
        tp = int(((y_pred == 1) & (y_true == 1)).sum())
        fp = int(((y_pred == 1) & (y_true == 0)).sum())
        fn = int(((y_pred == 0) & (y_true == 1)).sum())
        p = tp / max(tp + fp, 1)
        r = tp / max(tp + fn, 1)
        return (1 + beta**2) * p * r / max(beta**2 * p + r, 1e-9)

    score_best  = fbeta_at(best_thr)
    score_minus = fbeta_at(max(best_thr - delta, 0.05))
    score_plus  = fbeta_at(min(best_thr + delta, 0.95))
    min_neighbor = min(score_minus, score_plus)

    if score_best < 1e-9:
        return False, f"Score nul au seuil {best_thr:.2f}"

    drop = (score_best - min_neighbor) / score_best
    stable = drop <= max_drop_pct
    msg = (
        f"seuil={best_thr:.2f}  score={score_best:.4f}  "
        f"voisins=[{score_minus:.4f}, {score_plus:.4f}]  "
        f"chute={drop:.1%}  {'stable' if stable else 'FRAGILE'}"
    )
    return stable, msg
