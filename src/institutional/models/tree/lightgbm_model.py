"""
src/institutional/models/tree/lightgbm_model.py
─────────────────────────────────────────────────────────────────────────────
Modèle LightGBM institutionnel pour classification/régression.

Exigences :
  - Early stopping sur validation temporelle (jamais shuffle)
  - Feature importance SHAP-compatible
  - Calibration probabiliste (Platt scaling)
  - Model card automatique
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, log_loss
from sklearn.preprocessing import LabelEncoder

import lightgbm as lgb

from src.institutional.models.base import InstitutionalModel

logger = logging.getLogger(__name__)


DEFAULT_PARAMS = {
    "objective":        "multiclass",
    "num_class":        3,
    "boosting_type":    "gbdt",    # DART désactivé : incompatible avec early_stopping
    "num_leaves":       31,
    "learning_rate":    0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq":     5,
    "min_child_samples": 30,
    "lambda_l1":        0.1,
    "lambda_l2":        0.1,
    "verbose":          -1,
    "seed":             42,
}

BINARY_PARAMS = {
    "objective": "binary",
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_child_samples": 30,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "verbose": -1,
    "random_state": 42,
}


class LightGBMClassifier(InstitutionalModel):
    """
    Classifieur LightGBM institutionnel.

    Modes :
      - multiclass (3 classes : -1, 0, 1 → encodées 0, 1, 2)
      - binary (2 classes)

    Validation temporelle obligatoire : X_val doit être APRÈS X_train chronologiquement.
    """

    def __init__(
        self,
        version: str = "v1.0",
        asset: str = "unknown",
        target: str = "tb_label",
        task: str = "multiclass",         # "multiclass" | "binary"
        n_estimators: int = 1000,
        early_stopping_rounds: int = 50,
        calibrate: bool = True,
        calibration_method: str = "isotonic",   # "sigmoid" | "isotonic"
        params: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(version=version, asset=asset, target=target)
        self.task = task
        self.n_estimators = n_estimators
        self.early_stopping_rounds = early_stopping_rounds
        self.calibrate = calibrate
        self.calibration_method = calibration_method
        self.params = params or (DEFAULT_PARAMS if task == "multiclass" else BINARY_PARAMS)

        self._booster: Optional[lgb.Booster] = None
        self._label_encoder: Optional[LabelEncoder] = None
        self._classes: Optional[np.ndarray] = None
        self._calibrator: Optional[LogisticRegression] = None
        self._best_iteration: int = 0

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "LightGBMClassifier":
        # Encode labels
        self._label_encoder = LabelEncoder()
        y_enc = self._label_encoder.fit_transform(y_train)
        self._classes = self._label_encoder.classes_
        self._feature_names = list(X_train.columns)

        params = self.params.copy()
        if self.task == "multiclass":
            params["num_class"] = len(self._classes)

        train_data = lgb.Dataset(X_train, label=y_enc)

        callbacks = [
            lgb.log_evaluation(period=100),
        ]
        valid_sets = [train_data]
        valid_names = ["train"]

        if X_val is not None and y_val is not None:
            y_val_enc = self._label_encoder.transform(y_val)
            val_data = lgb.Dataset(X_val, label=y_val_enc, reference=train_data)
            valid_sets.append(val_data)
            valid_names.append("val")
            callbacks.append(lgb.early_stopping(self.early_stopping_rounds, verbose=False))

        self._booster = lgb.train(
            params=params,
            train_set=train_data,
            num_boost_round=self.n_estimators,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
        )

        self._best_iteration = self._booster.best_iteration or self.n_estimators
        self._fitted = True

        # Calibration (Platt scaling sur val si disponible)
        if self.calibrate and X_val is not None and y_val is not None:
            raw_proba_val = self._raw_proba(X_val)
            self._calibrator = LogisticRegression(C=1.0, max_iter=500)
            y_val_enc = self._label_encoder.transform(y_val)
            if self.task == "multiclass":
                self._calibrator.fit(raw_proba_val, y_val_enc)
            else:
                self._calibrator.fit(raw_proba_val.reshape(-1, 1), y_val_enc)

        # Métriques train/val
        train_proba = self.predict_proba(X_train)
        train_pred = self._label_encoder.inverse_transform(train_proba.argmax(axis=1))
        train_metrics = {
            "auc_ovr": float(roc_auc_score(
                y_train, train_proba, multi_class="ovr", labels=self._classes
            )) if self.task == "multiclass" else float(roc_auc_score(y_train, train_proba[:, 1])),
            "logloss": float(log_loss(y_train, train_proba, labels=self._classes)),
            "n_estimators_used": self._best_iteration,
        }

        val_metrics = {}
        if X_val is not None and y_val is not None:
            val_proba = self.predict_proba(X_val)
            val_metrics = {
                "auc_ovr": float(roc_auc_score(
                    y_val, val_proba, multi_class="ovr", labels=self._classes
                )) if self.task == "multiclass" else float(roc_auc_score(y_val, val_proba[:, 1])),
                "logloss": float(log_loss(y_val, val_proba, labels=self._classes)),
            }
            logger.info(f"  [LGB {self.asset}] val AUC={val_metrics['auc_ovr']:.4f}")

        self.generate_card(train_metrics, val_metrics, n_train=len(X_train))
        return self

    def _raw_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Probabilités brutes LightGBM (avant calibration)."""
        preds = self._booster.predict(X[self._feature_names], num_iteration=self._best_iteration)
        if self.task == "binary" and preds.ndim == 1:
            preds = np.column_stack([1 - preds, preds])
        return preds

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        self._validate_input(X)
        raw = self._raw_proba(X)

        if self._calibrator is not None:
            if self.task == "multiclass":
                return self._calibrator.predict_proba(raw)
            else:
                return self._calibrator.predict_proba(raw[:, 1].reshape(-1, 1))

        return raw

    def feature_importance(self) -> Dict[str, float]:
        if self._booster is None:
            return {}
        imp = self._booster.feature_importance(importance_type="gain")
        total = imp.sum() + 1e-9
        return {
            name: float(val / total)
            for name, val in zip(self._feature_names, imp)
        }

    def _get_params(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "n_estimators": self.n_estimators,
            "early_stopping_rounds": self.early_stopping_rounds,
            "calibrate": self.calibrate,
            **self.params,
        }


class LightGBMRegressor(InstitutionalModel):
    """
    Régresseur LightGBM pour prédiction de rendement continu.
    """

    def __init__(
        self,
        version: str = "v1.0",
        asset: str = "unknown",
        target: str = "fwd_ret_24h",
        n_estimators: int = 1000,
        early_stopping_rounds: int = 50,
        params: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(version=version, asset=asset, target=target)
        self.n_estimators = n_estimators
        self.early_stopping_rounds = early_stopping_rounds
        self.params = params or {
            "objective": "regression",
            "boosting_type": "gbdt",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "min_child_samples": 30,
            "verbose": -1,
        }
        self._booster: Optional[lgb.Booster] = None
        self._best_iteration: int = 0

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "LightGBMRegressor":
        self._feature_names = list(X_train.columns)
        train_data = lgb.Dataset(X_train, label=y_train)

        callbacks = [lgb.log_evaluation(period=100)]
        valid_sets = [train_data]

        if X_val is not None and y_val is not None:
            val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
            valid_sets.append(val_data)
            callbacks.append(lgb.early_stopping(self.early_stopping_rounds, verbose=False))

        self._booster = lgb.train(
            params=self.params,
            train_set=train_data,
            num_boost_round=self.n_estimators,
            valid_sets=valid_sets,
            callbacks=callbacks,
        )
        self._best_iteration = self._booster.best_iteration or self.n_estimators
        self._fitted = True

        train_pred = self._booster.predict(X_train, num_iteration=self._best_iteration)
        from sklearn.metrics import r2_score, mean_squared_error
        train_metrics = {
            "r2": float(r2_score(y_train, train_pred)),
            "rmse": float(np.sqrt(mean_squared_error(y_train, train_pred))),
        }
        val_metrics = {}
        if X_val is not None and y_val is not None:
            val_pred = self._booster.predict(X_val, num_iteration=self._best_iteration)
            val_metrics = {
                "r2": float(r2_score(y_val, val_pred)),
                "rmse": float(np.sqrt(mean_squared_error(y_val, val_pred))),
                "ic": float(pd.Series(val_pred).corr(pd.Series(y_val.values))),
            }
            logger.info(f"  [LGB {self.asset}] val IC={val_metrics['ic']:.4f}")

        self.generate_card(train_metrics, val_metrics, n_train=len(X_train))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.predict_raw(X)

    def predict_raw(self, X: pd.DataFrame) -> np.ndarray:
        self._validate_input(X)
        return self._booster.predict(X[self._feature_names], num_iteration=self._best_iteration)

    def feature_importance(self) -> Dict[str, float]:
        if self._booster is None:
            return {}
        imp = self._booster.feature_importance(importance_type="gain")
        total = imp.sum() + 1e-9
        return {name: float(val / total) for name, val in zip(self._feature_names, imp)}

    def _get_params(self) -> Dict[str, Any]:
        return {"n_estimators": self.n_estimators, **self.params}
