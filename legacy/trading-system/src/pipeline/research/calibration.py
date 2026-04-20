from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel

from common.logging.setup import get_logger

logger = get_logger(__name__)


class CalibrationConfig(BaseModel):
    method: str = "platt"  # platt|isotonic
    n_bins: int = 10


@dataclass
class ReliabilityCurve:
    bin_edges: np.ndarray
    bin_centers: np.ndarray
    empirical: np.ndarray


class Calibrator:
    def __init__(self, config: CalibrationConfig):
        if config.method not in {"platt", "isotonic"}:
            raise ValueError("method must be platt or isotonic")
        self.config = config
        self.regime_models: Dict[str, Callable[[np.ndarray], np.ndarray]] = {}

    def fit(self, df: pd.DataFrame, regime_col: str, pred_col: str, target_col: str) -> None:
        for regime, group in df.groupby(regime_col):
            preds = group[pred_col].to_numpy(dtype=float)
            targets = group[target_col].astype(int).to_numpy()
            if self.config.method == "platt":
                model = self._fit_platt(preds, targets)
            else:
                model = self._fit_isotonic(preds, targets)
            self.regime_models[str(regime)] = model
        logger.info({"msg": "fitted calibrator", "regimes": len(self.regime_models)})

    def predict(self, preds: np.ndarray, regime: str) -> np.ndarray:
        model = self.regime_models.get(str(regime))
        if model is None:
            return preds
        return model(preds)

    def reliability_curve(self, preds: np.ndarray, targets: np.ndarray) -> ReliabilityCurve:
        bins = np.linspace(0.0, 1.0, self.config.n_bins + 1)
        digitized = np.digitize(preds, bins) - 1
        empirical = []
        centers = []
        for i in range(self.config.n_bins):
            mask = digitized == i
            if mask.sum() == 0:
                empirical.append(np.nan)
            else:
                empirical.append(targets[mask].mean())
            centers.append((bins[i] + bins[i + 1]) / 2)
        return ReliabilityCurve(bin_edges=bins, bin_centers=np.array(centers), empirical=np.array(empirical))

    def brier_score(self, preds: np.ndarray, targets: np.ndarray) -> float:
        return float(np.mean((preds - targets) ** 2))

    def expected_calibration_error(self, preds: np.ndarray, targets: np.ndarray) -> float:
        curve = self.reliability_curve(preds, targets)
        valid = ~np.isnan(curve.empirical)
        if not valid.any():
            return math.nan
        weights = np.ones_like(curve.empirical[valid]) / len(curve.empirical[valid])
        return float(np.sum(np.abs(curve.empirical[valid] - curve.bin_centers[valid]) * weights))

    def _fit_platt(self, preds: np.ndarray, targets: np.ndarray) -> Callable[[np.ndarray], np.ndarray]:
        preds = np.clip(preds, 1e-6, 1 - 1e-6)
        a, b = 1.0, 0.0
        lr = 0.05
        for _ in range(200):
            logits = a * preds + b
            prob = 1 / (1 + np.exp(-logits))
            grad_a = np.mean((prob - targets) * preds)
            grad_b = np.mean(prob - targets)
            a -= lr * grad_a
            b -= lr * grad_b
        def model(x: np.ndarray) -> np.ndarray:
            return 1 / (1 + np.exp(-(a * x + b)))
        return model

    def _fit_isotonic(self, preds: np.ndarray, targets: np.ndarray) -> Callable[[np.ndarray], np.ndarray]:
        order = np.argsort(preds)
        sorted_preds = preds[order]
        sorted_targets = targets[order]
        cumulative = np.cumsum(sorted_targets)
        counts = np.arange(1, len(sorted_targets) + 1)
        running_mean = cumulative / counts
        def model(x: np.ndarray) -> np.ndarray:
            idx = np.searchsorted(sorted_preds, x, side="right") - 1
            idx = np.clip(idx, 0, len(running_mean) - 1)
            return running_mean[idx]
        return model
