"""
src/institutional/evaluation/event_metrics.py
─────────────────────────────────────────────────────────────────────────────
Métriques pour labels rares (event detection).

AUC ROC seul est insuffisant quand la classe positive est rare (<10%).
Ces métriques complètent le diagnostic :

  PR-AUC        : Precision-Recall, robuste aux déséquilibres de classes
  Precision@k%  : parmi les top k% signaux, quelle fraction est correcte ?
  Expectancy@k% : rendement moyen réalisé des top k% signaux (net frais)
  Confusion matrix UP/FLAT/DOWN
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    roc_auc_score,
)


# ─── Résultat complet ─────────────────────────────────────────────────────────

@dataclass
class EventEvaluation:
    """Évaluation complète pour un fold ou un test set."""
    asset:        str
    fold_id:      str
    n_samples:    int
    n_up:         int
    n_flat:       int
    n_down:       int
    prevalence_up:   float
    prevalence_down: float

    # AUC
    auc_ovr:      float
    auc_up:       float
    auc_down:     float

    # PR-AUC (Precision-Recall area)
    pr_auc_up:    float
    pr_auc_down:  float

    # Precision@k (classe UP)
    precision_at_1pct:  float
    precision_at_5pct:  float
    precision_at_10pct: float

    # Expectancy@k (rendement moyen net, requires fwd_ret column)
    expectancy_at_1pct:  Optional[float] = None
    expectancy_at_5pct:  Optional[float] = None
    expectancy_at_10pct: Optional[float] = None

    # Confusion matrix
    conf_matrix_labels: Tuple[int, ...] = (-1, 0, 1)
    conf_matrix:        Optional[List[List[int]]] = None

    def print(self) -> None:
        import logging
        log = logging.getLogger(__name__)
        log.info(f"\n  EventEval [{self.asset} fold={self.fold_id}]")
        log.info(f"  n={self.n_samples}  prevalence UP={self.prevalence_up:.1%} DOWN={self.prevalence_down:.1%}")
        log.info(f"  AUC OVR={self.auc_ovr:.4f}  AUC UP={self.auc_up:.4f}  AUC DOWN={self.auc_down:.4f}")
        log.info(f"  PR-AUC UP={self.pr_auc_up:.4f}  PR-AUC DOWN={self.pr_auc_down:.4f}")
        log.info(f"  Precision @1%={self.precision_at_1pct:.2%}  @5%={self.precision_at_5pct:.2%}  @10%={self.precision_at_10pct:.2%}")
        log.info(f"  Random baseline UP: {self.prevalence_up:.2%}")
        if self.expectancy_at_5pct is not None:
            log.info(f"  Expectancy @1%={self.expectancy_at_1pct:.4f}  @5%={self.expectancy_at_5pct:.4f}  @10%={self.expectancy_at_10pct:.4f}")

    def to_dict(self) -> Dict:
        return {
            "asset":           self.asset,
            "fold_id":         self.fold_id,
            "n_samples":       self.n_samples,
            "prevalence_up":   round(self.prevalence_up, 4),
            "prevalence_down": round(self.prevalence_down, 4),
            "auc_ovr":         round(self.auc_ovr, 4),
            "auc_up":          round(self.auc_up, 4),
            "auc_down":        round(self.auc_down, 4),
            "pr_auc_up":       round(self.pr_auc_up, 4),
            "pr_auc_down":     round(self.pr_auc_down, 4),
            "precision_at_1pct":  round(self.precision_at_1pct, 4),
            "precision_at_5pct":  round(self.precision_at_5pct, 4),
            "precision_at_10pct": round(self.precision_at_10pct, 4),
            "expectancy_at_1pct":  round(self.expectancy_at_1pct, 4) if self.expectancy_at_1pct else None,
            "expectancy_at_5pct":  round(self.expectancy_at_5pct, 4) if self.expectancy_at_5pct else None,
            "expectancy_at_10pct": round(self.expectancy_at_10pct, 4) if self.expectancy_at_10pct else None,
        }

    def verdict(self, target: str = "event") -> str:
        """Verdict basé sur PR-AUC et precision@5% vs baseline."""
        pr_lift = self.pr_auc_up / max(self.prevalence_up, 1e-6) - 1
        prec5_lift = self.precision_at_5pct / max(self.prevalence_up, 1e-6) - 1
        if pr_lift > 0.5 and prec5_lift > 1.0:
            return "STRONG"
        elif pr_lift > 0.2 and prec5_lift > 0.5:
            return "MODERATE"
        elif pr_lift > 0.0:
            return "WEAK"
        return "NO_SIGNAL"


# ─── Fonctions de calcul ──────────────────────────────────────────────────────

def _precision_at_k(
    y_true: np.ndarray,
    scores: np.ndarray,
    k_pct: float,
    positive_class: int,
) -> float:
    """
    Precision@k pour une classe donnée.

    Trie les exemples par score descendant, prend les top k%,
    retourne la fraction qui est effectivement de la classe positive.

    k_pct=1.0 → top 1% des prédictions
    """
    n = len(scores)
    n_top = max(1, int(n * k_pct / 100))
    top_idx = np.argsort(scores)[-n_top:]
    return float((y_true[top_idx] == positive_class).mean())


def _expectancy_at_k(
    fwd_ret: np.ndarray,
    scores: np.ndarray,
    k_pct: float,
    cost_frac: float = 0.001,
) -> Optional[float]:
    """
    Expectancy@k : rendement moyen (net frais) des top k% signaux.

    Retourne None si fwd_ret est entièrement NaN.
    """
    if fwd_ret is None or np.isnan(fwd_ret).all():
        return None
    n = len(scores)
    n_top = max(1, int(n * k_pct / 100))
    top_idx = np.argsort(scores)[-n_top:]
    valid = ~np.isnan(fwd_ret[top_idx])
    if valid.sum() == 0:
        return None
    return float(fwd_ret[top_idx][valid].mean()) - cost_frac


def compute_event_evaluation(
    y_true:     pd.Series,
    proba:      np.ndarray,
    classes:    np.ndarray,
    asset:      str     = "unknown",
    fold_id:    str     = "unknown",
    fwd_ret:    Optional[pd.Series] = None,
    cost_bps:   float   = 10.0,
) -> EventEvaluation:
    """
    Évaluation complète pour labels rares (event detection).

    Paramètres
    ----------
    y_true   : labels vrais (-1, 0, +1)
    proba    : matrice de probabilités (n_samples × n_classes)
    classes  : array des classes (ex: [-1, 0, 1])
    asset    : nom de l'actif
    fold_id  : identifiant du fold
    fwd_ret  : rendements futurs réels (optionnel, pour expectancy)
    cost_bps : coût aller-retour pour expectancy
    """
    y_arr  = y_true.values
    cost_f = cost_bps / 10_000

    cls_list = list(classes)
    n = len(y_arr)

    # Indices par classe
    def _idx(cls_val):
        return cls_list.index(cls_val) if cls_val in cls_list else None

    idx_up   = _idx(1)
    idx_down = _idx(-1)

    # Counts
    n_up   = int((y_arr == 1).sum())
    n_down = int((y_arr == -1).sum())
    n_flat = int((y_arr == 0).sum())

    # AUC OVR
    try:
        auc_ovr = float(roc_auc_score(y_arr, proba, multi_class="ovr", labels=classes))
    except Exception:
        auc_ovr = 0.5

    # AUC per class (binary OVR)
    def _binary_auc(cls_val, cls_col_idx):
        if cls_col_idx is None or (y_arr == cls_val).sum() == 0:
            return 0.5
        try:
            return float(roc_auc_score((y_arr == cls_val).astype(int), proba[:, cls_col_idx]))
        except Exception:
            return 0.5

    auc_up   = _binary_auc(1,  idx_up)
    auc_down = _binary_auc(-1, idx_down)

    # PR-AUC per class
    def _pr_auc(cls_val, cls_col_idx):
        if cls_col_idx is None or (y_arr == cls_val).sum() == 0:
            return 0.0
        try:
            return float(average_precision_score(
                (y_arr == cls_val).astype(int), proba[:, cls_col_idx]
            ))
        except Exception:
            return 0.0

    pr_auc_up   = _pr_auc(1,  idx_up)
    pr_auc_down = _pr_auc(-1, idx_down)

    # Precision@k (classe UP)
    if idx_up is not None:
        scores_up = proba[:, idx_up]
    else:
        scores_up = np.zeros(n)

    p1  = _precision_at_k(y_arr, scores_up, 1.0,  1)
    p5  = _precision_at_k(y_arr, scores_up, 5.0,  1)
    p10 = _precision_at_k(y_arr, scores_up, 10.0, 1)

    # Expectancy@k
    fwd_arr = fwd_ret.values if fwd_ret is not None else None
    e1  = _expectancy_at_k(fwd_arr, scores_up, 1.0,  cost_f) if fwd_arr is not None else None
    e5  = _expectancy_at_k(fwd_arr, scores_up, 5.0,  cost_f) if fwd_arr is not None else None
    e10 = _expectancy_at_k(fwd_arr, scores_up, 10.0, cost_f) if fwd_arr is not None else None

    # Confusion matrix
    try:
        cm = confusion_matrix(y_arr, proba.argmax(axis=1) if proba.ndim == 2 else proba,
                              labels=cls_list).tolist()
    except Exception:
        cm = None

    return EventEvaluation(
        asset=asset,
        fold_id=fold_id,
        n_samples=n,
        n_up=n_up,
        n_flat=n_flat,
        n_down=n_down,
        prevalence_up=n_up / max(n, 1),
        prevalence_down=n_down / max(n, 1),
        auc_ovr=auc_ovr,
        auc_up=auc_up,
        auc_down=auc_down,
        pr_auc_up=pr_auc_up,
        pr_auc_down=pr_auc_down,
        precision_at_1pct=p1,
        precision_at_5pct=p5,
        precision_at_10pct=p10,
        expectancy_at_1pct=e1,
        expectancy_at_5pct=e5,
        expectancy_at_10pct=e10,
        conf_matrix_labels=tuple(cls_list),
        conf_matrix=cm,
    )
