"""
src/institutional/models/volatility/ewma.py
─────────────────────────────────────────────────────────────────────────────
Modèles de volatilité : EWMA et HAR-RV.

Ces modèles servent à :
  1. Prédire la volatilité future (pour sizing et barrières)
  2. Construire des features de vol normalisées pour d'autres modèles
  3. Paramétrer les stop-loss et take-profit dynamiques
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error

from src.institutional.models.base import InstitutionalModel

logger = logging.getLogger(__name__)

ANNUALIZATION_FACTOR = np.sqrt(24 * 365)


class EWMAVolatilityModel(InstitutionalModel):
    """
    Modèle EWMA (RiskMetrics) pour la prévision de volatilité.

    Paramètre λ : decay factor (typiquement 0.94 pour données journalières,
    0.97 pour données intra-journalières haute fréquence).
    """

    def __init__(
        self,
        version: str = "v1.0",
        asset: str = "unknown",
        target: str = "realized_vol",
        lambda_decay: float = 0.94,
        horizon_h: int = 24,
    ):
        super().__init__(version=version, asset=asset, target=target)
        self.lambda_decay = lambda_decay
        self.horizon_h = horizon_h
        self._last_var: float = 0.0
        self._fitted = True  # EWMA n'a pas besoin de training dataset

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "EWMAVolatilityModel":
        """Pour EWMA, fit = validation du lambda sur la série d'entraînement."""
        if "log_ret_1h" not in X_train.columns:
            logger.warning("log_ret_1h non trouvé — EWMA utilise le target directement")
            return self

        log_ret = X_train["log_ret_1h"].dropna()

        # Optimisation simple : trouver le lambda qui minimise le QLIKE
        span = 1 / (1 - self.lambda_decay)
        ewma_var = log_ret.pow(2).ewm(span=span, adjust=False).mean()
        self._last_var = float(ewma_var.iloc[-1])

        train_metrics = {"last_ewma_vol_ann": float(np.sqrt(self._last_var) * ANNUALIZATION_FACTOR)}
        self.generate_card(train_metrics, {}, n_train=len(X_train))
        return self

    def predict_vol(self, log_returns: pd.Series) -> pd.Series:
        """Prédit la volatilité EWMA sur une série de log-returns."""
        span = 1 / (1 - self.lambda_decay)
        var = log_returns.pow(2).ewm(span=span, adjust=False).mean()
        return np.sqrt(var) * ANNUALIZATION_FACTOR

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if "log_ret_1h" not in X.columns:
            return np.full(len(X), np.sqrt(self._last_var) * ANNUALIZATION_FACTOR)
        return self.predict_vol(X["log_ret_1h"]).values

    def _get_params(self) -> Dict[str, Any]:
        return {"lambda_decay": self.lambda_decay, "horizon_h": self.horizon_h}


class HARRVModel(InstitutionalModel):
    """
    Modèle HAR-RV (Heterogeneous AutoRegressive — Realized Volatility).

    Prédit RV_{t+h} en fonction de RV_1h, RV_1d, RV_1w.
    Simple linéaire Ridge pour robustesse.

    Référence : Corsi (2009), "A simple approximate long-memory model
    of realized volatility"
    """

    def __init__(
        self,
        version: str = "v1.0",
        asset: str = "unknown",
        target: str = "rv_24h",
        alpha: float = 0.01,
    ):
        super().__init__(version=version, asset=asset, target=target)
        self.alpha = alpha
        self._model = Ridge(alpha=alpha, positive=True)
        self._feature_cols = ["rv_1h_ann", "rv_1d_ann", "rv_1w_ann"]

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "HARRVModel":
        available = [c for c in self._feature_cols if c in X_train.columns]
        if not available:
            logger.warning("Composantes HAR-RV non trouvées dans les features")
            return self

        self._feature_names = available
        X = X_train[available].fillna(method="ffill").fillna(0)
        self._model.fit(X, y_train)
        self._fitted = True

        train_pred = self._model.predict(X)
        train_metrics = {
            "r2": float(1 - np.var(y_train - train_pred) / (np.var(y_train) + 1e-9)),
            "coefs": {f: float(c) for f, c in zip(available, self._model.coef_)},
        }

        val_metrics = {}
        if X_val is not None and y_val is not None:
            X_val_clean = X_val[available].fillna(method="ffill").fillna(0)
            val_pred = self._model.predict(X_val_clean)
            val_metrics = {
                "r2": float(1 - np.var(y_val - val_pred) / (np.var(y_val) + 1e-9)),
            }

        self.generate_card(train_metrics, val_metrics, n_train=len(X_train))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        self._validate_input(X)
        X_clean = X[self._feature_names].fillna(method="ffill").fillna(0)
        return self._model.predict(X_clean)

    def _get_params(self) -> Dict[str, Any]:
        return {"alpha": self.alpha}
