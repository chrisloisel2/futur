"""
src/institutional/models/linear/ridge.py
─────────────────────────────────────────────────────────────────────────────
Modèles linéaires baseline — Ridge, Logistic, Elastic Net.

Ces modèles servent de benchmarks obligatoires.
Aucun modèle complexe n'est accepté s'il ne bat pas ces baselines OOS.

IMPORTANT : le StandardScaler est fit UNIQUEMENT sur les données train.
Jamais sur val ou test.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import (
    ElasticNet, LogisticRegression, Ridge,
    RidgeClassifier,
)
from sklearn.metrics import r2_score, roc_auc_score, log_loss
from sklearn.preprocessing import StandardScaler

from src.institutional.models.base import InstitutionalModel

logger = logging.getLogger(__name__)


class RidgeBaselineRegressor(InstitutionalModel):
    """Ridge regression baseline pour prédiction de forward return."""

    def __init__(
        self,
        version: str = "v1.0",
        asset: str = "unknown",
        target: str = "fwd_ret_24h",
        alpha: float = 1.0,
    ):
        super().__init__(version=version, asset=asset, target=target)
        self.alpha = alpha
        self._scaler = StandardScaler()
        self._model = Ridge(alpha=alpha)

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "RidgeBaselineRegressor":
        self._feature_names = list(X_train.columns)

        # Scaler fit UNIQUEMENT sur train
        X_scaled = self._scaler.fit_transform(X_train.fillna(0))
        self._model.fit(X_scaled, y_train)
        self._fitted = True

        train_pred = self._model.predict(X_scaled)
        train_metrics = {
            "r2": float(r2_score(y_train, train_pred)),
            "ic": float(pd.Series(train_pred).corr(pd.Series(y_train.values))),
        }
        val_metrics = {}
        if X_val is not None and y_val is not None:
            X_val_scaled = self._scaler.transform(X_val[self._feature_names].fillna(0))
            val_pred = self._model.predict(X_val_scaled)
            val_metrics = {
                "r2": float(r2_score(y_val, val_pred)),
                "ic": float(pd.Series(val_pred).corr(pd.Series(y_val.values))),
            }

        self.generate_card(train_metrics, val_metrics, n_train=len(X_train))
        logger.info(f"  [Ridge {self.asset}] IC train={train_metrics['ic']:.4f} "
                    f"val={val_metrics.get('ic', 0):.4f}")
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        self._validate_input(X)
        X_scaled = self._scaler.transform(X[self._feature_names].fillna(0))
        return self._model.predict(X_scaled)

    def _get_params(self) -> Dict[str, Any]:
        return {"alpha": self.alpha}


class LogisticBaselineClassifier(InstitutionalModel):
    """Logistic regression calibrée — baseline classification."""

    def __init__(
        self,
        version: str = "v1.0",
        asset: str = "unknown",
        target: str = "tb_label",
        C: float = 1.0,
        multi_class: str = "ovr",
    ):
        super().__init__(version=version, asset=asset, target=target)
        self.C = C
        self.multi_class = multi_class
        self._scaler = StandardScaler()
        self._model = LogisticRegression(C=C, multi_class=multi_class, max_iter=1000)
        self._classes: Optional[np.ndarray] = None

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "LogisticBaselineClassifier":
        self._feature_names = list(X_train.columns)
        self._classes = np.sort(y_train.unique())

        X_scaled = self._scaler.fit_transform(X_train.fillna(0))
        self._model.fit(X_scaled, y_train)
        self._fitted = True

        train_proba = self._model.predict_proba(X_scaled)
        train_metrics = {
            "auc_ovr": float(roc_auc_score(y_train, train_proba, multi_class="ovr",
                                           labels=self._classes)),
            "logloss": float(log_loss(y_train, train_proba, labels=self._classes)),
        }
        val_metrics = {}
        if X_val is not None and y_val is not None:
            X_val_scaled = self._scaler.transform(X_val[self._feature_names].fillna(0))
            val_proba = self._model.predict_proba(X_val_scaled)
            val_metrics = {
                "auc_ovr": float(roc_auc_score(y_val, val_proba, multi_class="ovr",
                                               labels=self._classes)),
                "logloss": float(log_loss(y_val, val_proba, labels=self._classes)),
            }
            logger.info(f"  [Logistic {self.asset}] val AUC={val_metrics['auc_ovr']:.4f}")

        self.generate_card(train_metrics, val_metrics, n_train=len(X_train))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        self._validate_input(X)
        X_scaled = self._scaler.transform(X[self._feature_names].fillna(0))
        return self._model.predict_proba(X_scaled)

    def _get_params(self) -> Dict[str, Any]:
        return {"C": self.C, "multi_class": self.multi_class}
