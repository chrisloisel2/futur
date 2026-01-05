"""
Robust binary probability calibrator for EdgeForecaster.

Supports:
  - Temperature scaling (for raw logits)
  - Isotonic regression (for uncalibrated probabilities)
  - Automatic input type detection
  - Pickle serialization
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from sklearn.isotonic import IsotonicRegression

from common.logging.setup import get_logger

logger = get_logger(__name__)


class BinaryCalibrator:
    """
    Binary probability calibrator with temperature scaling and isotonic regression.

    Usage:
        # Temperature scaling (on logits)
        calibrator = BinaryCalibrator(method="temperature")
        calibrator.fit(logits, y_true)
        p_calibrated = calibrator.predict_proba(logits)

        # Isotonic regression (on probas)
        calibrator = BinaryCalibrator(method="isotonic")
        calibrator.fit(probas, y_true)
        p_calibrated = calibrator.predict_proba(probas)

        # Save/load
        calibrator.save("calibrator.pkl")
        calibrator = BinaryCalibrator.load("calibrator.pkl")

    CRITICAL: Do NOT fit on validation set used for early stopping.
             Use a separate calibration split (e.g., last 20% of train).
    """

    def __init__(self, method: Literal["temperature", "isotonic"] = "temperature"):
        """
        Initialize calibrator.

        Args:
            method: Calibration method
                - "temperature": Temperature scaling for logits (Platt scaling)
                - "isotonic": Isotonic regression for probabilities
        """
        self.method = method
        self.temperature: Optional[float] = None
        self.isotonic_model: Optional[IsotonicRegression] = None
        self._is_fitted = False

    def fit(self, predictions: np.ndarray, y_true: np.ndarray) -> BinaryCalibrator:
        """
        Fit calibrator on predictions and ground truth.

        Args:
            predictions: (N,) array of predictions
                - If method="temperature": raw logits
                - If method="isotonic": uncalibrated probabilities [0, 1]
            y_true: (N,) array of binary labels {0, 1}

        Returns:
            self (for chaining)

        Raises:
            ValueError: If inputs are invalid or method unsupported
        """
        predictions = np.asarray(predictions, dtype=np.float64).ravel()
        y_true = np.asarray(y_true, dtype=np.float64).ravel()

        if predictions.shape[0] != y_true.shape[0]:
            raise ValueError(
                f"Shape mismatch: predictions {predictions.shape} vs y_true {y_true.shape}"
            )

        if predictions.shape[0] < 10:
            raise ValueError(f"Need at least 10 samples, got {predictions.shape[0]}")

        # Validate labels
        unique_labels = np.unique(y_true)
        if not np.all(np.isin(unique_labels, [0, 1])):
            raise ValueError(f"y_true must be binary {{0, 1}}, got {unique_labels}")

        if len(unique_labels) < 2:
            raise ValueError(f"y_true must contain both classes, got only {unique_labels}")

        if self.method == "temperature":
            self._fit_temperature(predictions, y_true)
        elif self.method == "isotonic":
            self._fit_isotonic(predictions, y_true)
        else:
            raise ValueError(f"Unknown method: {self.method}")

        self._is_fitted = True
        return self

    def _fit_temperature(self, logits: np.ndarray, y_true: np.ndarray) -> None:
        """
        Fit temperature scaling (Platt scaling).

        Finds T that minimizes BCE(sigmoid(logits / T), y_true).
        Uses scipy.optimize.minimize_scalar for robust optimization.
        """
        from scipy.optimize import minimize_scalar
        from scipy.special import expit

        def bce_loss(T: float) -> float:
            """Binary cross-entropy with temperature T."""
            T = max(T, 1e-8)  # Prevent division by zero
            p = expit(logits / T)
            p = np.clip(p, 1e-12, 1 - 1e-12)  # Numerical stability
            bce = -np.mean(y_true * np.log(p) + (1 - y_true) * np.log(1 - p))
            return bce

        # Optimize temperature in range [0.01, 100.0]
        result = minimize_scalar(
            bce_loss,
            bounds=(0.01, 100.0),
            method="bounded",
        )

        if not result.success:
            # Fallback to T=1.0 (no scaling)
            logger.warning({
                "msg": "Temperature optimization failed - using fallback",
                "error": result.message,
                "fallback_temperature": 1.0,
                "impact": "No calibration will be applied (T=1.0)"
            })
            self.temperature = 1.0
        else:
            self.temperature = float(result.x)
            logger.info({
                "msg": "Temperature calibration fitted",
                "temperature": self.temperature,
                "final_loss": result.fun
            })

    def _fit_isotonic(self, probas: np.ndarray, y_true: np.ndarray) -> None:
        """
        Fit isotonic regression calibrator.

        Args:
            probas: (N,) uncalibrated probabilities in [0, 1]
            y_true: (N,) binary labels {0, 1}
        """
        # Validate probabilities
        if np.any((probas < 0) | (probas > 1)):
            raise ValueError(
                f"Probabilities must be in [0, 1] for isotonic method. "
                f"Got range [{probas.min():.3f}, {probas.max():.3f}]"
            )

        self.isotonic_model = IsotonicRegression(
            y_min=0.0,
            y_max=1.0,
            increasing=True,
            out_of_bounds="clip",
        )
        self.isotonic_model.fit(probas, y_true)

    def predict_proba(self, predictions: np.ndarray) -> np.ndarray:
        """
        Apply calibration to predictions.

        Args:
            predictions: (N,) array of predictions
                - If method="temperature": raw logits
                - If method="isotonic": uncalibrated probabilities [0, 1]

        Returns:
            (N,) array of calibrated probabilities in [0, 1]

        Raises:
            RuntimeError: If calibrator not fitted
        """
        if not self._is_fitted:
            raise RuntimeError("Calibrator not fitted. Call fit() first.")

        predictions = np.asarray(predictions, dtype=np.float64).ravel()

        if self.method == "temperature":
            from scipy.special import expit
            p = expit(predictions / self.temperature)
            return np.clip(p, 0.0, 1.0)

        elif self.method == "isotonic":
            # Validate input range
            if np.any((predictions < 0) | (predictions > 1)):
                raise ValueError(
                    f"Isotonic calibrator expects probabilities in [0, 1]. "
                    f"Got range [{predictions.min():.3f}, {predictions.max():.3f}]"
                )
            return self.isotonic_model.predict(predictions)

        else:
            raise RuntimeError(f"Unknown method: {self.method}")

    def save(self, path: str) -> None:
        """
        Save calibrator to pickle file.

        Args:
            path: Output path (.pkl file)
        """
        if not self._is_fitted:
            raise RuntimeError("Cannot save unfitted calibrator")

        Path(path).parent.mkdir(parents=True, exist_ok=True)

        with open(path, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path: str) -> BinaryCalibrator:
        """
        Load calibrator from pickle file with backward compatibility.

        Args:
            path: Input path (.pkl file)

        Returns:
            Loaded BinaryCalibrator instance

        Note:
            Supports legacy formats:
            - sklearn LogisticRegression (converted to temperature scaling)
            - Old EdgeCalibrator (converted to no-op calibrator)
        """
        with open(path, "rb") as f:
            obj = pickle.load(f)

        if isinstance(obj, BinaryCalibrator):
            return obj

        # Legacy format: sklearn LogisticRegression
        # This was used for Platt scaling in old pipeline
        # Extract temperature from coef_: T ≈ 1 / coef_[0][0]
        from sklearn.linear_model import LogisticRegression
        if isinstance(obj, LogisticRegression):
            # Extract temperature approximation from LR coefficient
            # Platt scaling: p = sigmoid(a*x + b) ≈ sigmoid(x/T) where T ≈ 1/a
            coef = float(obj.coef_[0][0])

            if abs(coef) > 1e-6:
                # Temperature is approximately 1 / coefficient
                temperature = 1.0 / coef
                # Clamp to reasonable range
                temperature = float(np.clip(temperature, 0.01, 100.0))
            else:
                temperature = 1.0

            logger.warning({
                "msg": "Legacy calibrator detected (LogisticRegression)",
                "path": path,
                "lr_coef": coef,
                "extracted_temperature": temperature,
                "action": f"Converting to BinaryCalibrator with T={temperature:.4f}",
                "recommendation": "Retrain calibrator using BinaryCalibrator for better control"
            })

            calibrator = BinaryCalibrator(method="temperature")
            calibrator.temperature = temperature
            calibrator._is_fitted = True
            return calibrator

        # Legacy format: old EdgeCalibrator with bias
        if hasattr(obj, 'bias') and hasattr(obj, 'predict'):
            logger.warning({
                "msg": "Legacy EdgeCalibrator detected",
                "path": path,
                "action": "Converting to BinaryCalibrator with T=1.0 (no calibration)",
                "recommendation": "Retrain calibrator using BinaryCalibrator"
            })
            calibrator = BinaryCalibrator(method="temperature")
            calibrator.temperature = 1.0
            calibrator._is_fitted = True
            return calibrator

        raise RuntimeError(
            f"Loaded object is not a BinaryCalibrator or recognized legacy format: {type(obj)}"
        )

    def __repr__(self) -> str:
        if not self._is_fitted:
            return f"BinaryCalibrator(method={self.method}, fitted=False)"

        if self.method == "temperature":
            return f"BinaryCalibrator(method=temperature, T={self.temperature:.4f})"
        else:
            return f"BinaryCalibrator(method=isotonic, fitted=True)"
