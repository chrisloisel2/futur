from __future__ import annotations

import numpy as np

from pipeline.models.base import BaseCalibrator


class EdgeCalibrator(BaseCalibrator):
    def __init__(self):
        self.bias = 0.0

    def fit(self, y_true, y_prob) -> None:
        return None

    def predict(self, p):
        return np.clip(p + self.bias, 0.0, 1.0)
