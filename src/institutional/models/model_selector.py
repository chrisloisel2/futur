"""
src/institutional/models/model_selector.py
─────────────────────────────────────────────────────────────────────────────
Sélection automatique du meilleur modèle par fold.

Pour chaque fold walk-forward :
  1. Entraîner tous les candidats (Logistic + LightGBM)
  2. Évaluer sur val uniquement (jamais sur test)
  3. Choisir le meilleur selon la métrique primaire
  4. Produire un rapport de comparaison

Métrique primaire :
  - labels équilibrés (trend) : AUC OVR
  - labels rares (event)       : PR-AUC UP  (plus pertinent que AUC pour 5% prévalence)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score, roc_auc_score, log_loss,
)

from src.institutional.models.tree.lightgbm_model import LightGBMClassifier
from src.institutional.models.linear.ridge import LogisticBaselineClassifier
from src.institutional.models.base import InstitutionalModel

logger = logging.getLogger(__name__)


# ─── Résultat de sélection ────────────────────────────────────────────────────

@dataclass
class CandidateResult:
    model_name: str
    model: InstitutionalModel
    val_metrics: Dict[str, float]
    selection_score: float   # métrique primaire sur val


@dataclass
class ModelSelectionResult:
    fold_id:              str
    selected_model_name:  str
    selected_model:       InstitutionalModel
    selection_metric:     str
    candidates:           List[CandidateResult]
    winner_val_score:     float

    def summary_row(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "fold":             self.fold_id,
            "selected":         self.selected_model_name,
            "metric":           self.selection_metric,
            "winner_val_score": round(self.winner_val_score, 4),
        }
        for c in self.candidates:
            row[f"{c.model_name}_val"] = round(c.selection_score, 4)
        return row

    def print_table(self) -> None:
        logger.info(f"  ModelSelector fold={self.fold_id} ({self.selection_metric})")
        for c in sorted(self.candidates, key=lambda x: -x.selection_score):
            marker = "★" if c.model_name == self.selected_model_name else " "
            logger.info(f"    {marker} {c.model_name:20s} : {c.selection_score:.4f}")


# ─── Évaluation ───────────────────────────────────────────────────────────────

def _eval_val(
    model: InstitutionalModel,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    metric: str,
) -> Dict[str, float]:
    """Évalue un modèle sur le val set. Retourne un dict de métriques."""
    proba = model.predict_proba(X_val)
    classes = model._classes if hasattr(model, "_classes") and model._classes is not None else np.unique(y_val)

    out: Dict[str, float] = {}

    # AUC OVR
    try:
        out["auc_ovr"] = float(roc_auc_score(
            y_val, proba, multi_class="ovr", labels=classes
        ))
    except Exception:
        out["auc_ovr"] = 0.5

    # Log-loss
    try:
        out["logloss"] = float(log_loss(y_val, proba, labels=classes))
    except Exception:
        out["logloss"] = 9.99

    # PR-AUC pour chaque classe (utile pour labels rares)
    for cls_val in [-1, 1]:
        if cls_val in classes and proba.ndim == 2:
            cls_idx = list(classes).index(cls_val)
            try:
                binary_y = (y_val == cls_val).astype(int)
                out[f"pr_auc_{cls_val}"] = float(
                    average_precision_score(binary_y, proba[:, cls_idx])
                )
            except Exception:
                out[f"pr_auc_{cls_val}"] = 0.0

    return out


def _selection_score(metrics: Dict[str, float], metric: str) -> float:
    """Extrait le score de sélection depuis le dict de métriques."""
    if metric == "pr_auc_up":
        return metrics.get("pr_auc_1", metrics.get("auc_ovr", 0.5))
    elif metric == "pr_auc_down":
        return metrics.get("pr_auc_-1", metrics.get("auc_ovr", 0.5))
    return metrics.get(metric, 0.5)


# ─── ModelSelector ────────────────────────────────────────────────────────────

class ModelSelector:
    """
    Sélectionne le meilleur modèle sur validation pour un fold donné.

    Candidats par défaut :
      - Logistic (baseline robuste, souvent compétitif)
      - LightGBM GBDT (modèle principal)

    Usage :
        selector = ModelSelector(primary_metric="auc_ovr", asset="BTCUSDT")
        result   = selector.select(X_tr, y_tr, X_va, y_va, fold_id="2024")
        best_model = result.selected_model
    """

    def __init__(
        self,
        primary_metric: str = "auc_ovr",
        asset: str = "unknown",
        target: str = "unknown",
        n_estimators: int = 500,
        early_stopping_rounds: int = 50,
    ):
        self.primary_metric     = primary_metric
        self.asset              = asset
        self.target             = target
        self.n_estimators       = n_estimators
        self.early_stopping_rounds = early_stopping_rounds

    def _make_candidates(self) -> List[InstitutionalModel]:
        return [
            LogisticBaselineClassifier(asset=self.asset, target=self.target),
            LightGBMClassifier(
                asset=self.asset,
                target=self.target,
                n_estimators=self.n_estimators,
                early_stopping_rounds=self.early_stopping_rounds,
            ),
        ]

    def select(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val:   pd.DataFrame,
        y_val:   pd.Series,
        fold_id: str = "unknown",
    ) -> ModelSelectionResult:
        """
        Entraîne tous les candidats et sélectionne le meilleur sur val.

        GARANTIE : X_val n'est utilisé que pour la sélection — jamais pour le fit.
        """
        candidates: List[CandidateResult] = []

        for model in self._make_candidates():
            name = model.__class__.__name__
            try:
                model.fit(X_train, y_train, X_val=X_val, y_val=y_val)
                val_metrics   = _eval_val(model, X_val, y_val, self.primary_metric)
                sel_score     = _selection_score(val_metrics, self.primary_metric)
                candidates.append(CandidateResult(
                    model_name=name, model=model,
                    val_metrics=val_metrics, selection_score=sel_score,
                ))
                logger.info(f"  {name:25s} val {self.primary_metric}={sel_score:.4f}")
            except Exception as e:
                logger.warning(f"  {name}: FAILED ({e})")

        if not candidates:
            raise RuntimeError(f"Aucun modèle n'a pu être entraîné pour fold={fold_id}")

        best = max(candidates, key=lambda c: c.selection_score)

        result = ModelSelectionResult(
            fold_id=fold_id,
            selected_model_name=best.model_name,
            selected_model=best.model,
            selection_metric=self.primary_metric,
            candidates=candidates,
            winner_val_score=best.selection_score,
        )
        result.print_table()
        return result
