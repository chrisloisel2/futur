"""
ai/meta/ensemble_disagreement.py — Ensemble Disagreement

Mesure la variance des prédictions entre plusieurs modèles sur la même barre.
Grande variance → le signal est peu fiable → suppresser le trade.

Usage:
  ens = EnsembleDisagreement()
  ens.register_model("lr",  lr_model,  lr_scaler)
  ens.register_model("xgb", xgb_model, xgb_scaler)
  ens.register_model("perturbed", lr_model_v2, lr_scaler)

  preds = ens.predict_all(X_bar)          # {name: prob}
  disag = ens.disagreement(X_bar)         # float std [0, 0.5]
  sup   = ens.should_suppress(X_bar)      # bool
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


@dataclass
class PredictionBundle:
    predictions: dict[str, float]    # {model_name: probability}
    mean:        float
    std:         float               # disagreement metric
    min_pred:    float
    max_pred:    float
    n_models:    int

    def disagreement(self) -> float:
        return self.std

    def is_reliable(self, threshold: float = 0.12) -> bool:
        return self.std <= threshold


class EnsembleDisagreement:
    """
    Mesure le désaccord entre un ensemble de modèles de classification binaire.

    Le désaccord est mesuré par l'écart-type des probabilités prédites.
    Threshold par défaut: std > 0.12 → signal peu fiable.
    """

    def __init__(self, disagree_threshold: float = 0.12):
        self._models:    list[dict] = []   # [{name, model, scaler}]
        self._threshold  = disagree_threshold

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_model(
        self,
        name:   str,
        model:  Any,
        scaler: Optional[Any] = None,
        weight: float = 1.0,
    ) -> None:
        self._models.append({
            "name":   name,
            "model":  model,
            "scaler": scaler,
            "weight": weight,
        })

    def n_models(self) -> int:
        return len(self._models)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict_all(self, X: np.ndarray) -> dict[str, float]:
        """Prédictions de tous les modèles sur X."""
        X_flat = X.reshape(1, -1) if X.ndim == 1 else X
        results = {}
        for m in self._models:
            try:
                X_in = m["scaler"].transform(X_flat) if m["scaler"] else X_flat
                prob = float(m["model"].predict_proba(X_in)[0, 1])
                results[m["name"]] = prob
            except Exception:
                pass
        return results

    def disagreement(self, X: np.ndarray) -> float:
        preds = self.predict_all(X)
        if len(preds) < 2:
            return 0.0
        return float(np.std(list(preds.values())))

    def bundle(self, X: np.ndarray) -> PredictionBundle:
        preds = self.predict_all(X)
        vals  = list(preds.values())
        if not vals:
            return PredictionBundle({}, 0.5, 0.5, 0.0, 1.0, 0)
        return PredictionBundle(
            predictions = preds,
            mean        = float(np.mean(vals)),
            std         = float(np.std(vals)),
            min_pred    = float(np.min(vals)),
            max_pred    = float(np.max(vals)),
            n_models    = len(vals),
        )

    def should_suppress(
        self,
        X: np.ndarray,
        threshold: Optional[float] = None,
    ) -> bool:
        thr = threshold if threshold is not None else self._threshold
        if len(self._models) < 2:
            return False  # Pas assez de modèles pour mesurer le désaccord
        return self.disagreement(X) > thr

    def weighted_mean(self, X: np.ndarray) -> float:
        """Moyenne pondérée des prédictions (pour remplacer la moyenne simple)."""
        X_flat = X.reshape(1, -1) if X.ndim == 1 else X
        total_w, total_pred = 0.0, 0.0
        for m in self._models:
            try:
                X_in = m["scaler"].transform(X_flat) if m["scaler"] else X_flat
                prob = float(m["model"].predict_proba(X_in)[0, 1])
                total_pred += prob * m["weight"]
                total_w    += m["weight"]
            except Exception:
                pass
        return total_pred / max(total_w, 1e-6)
