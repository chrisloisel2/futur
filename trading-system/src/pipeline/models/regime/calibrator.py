from __future__ import annotations

import numpy as np

from pipeline.models.base import BaseCalibrator


class RegimeCalibrator(BaseCalibrator):
    def __init__(self):
        self.a = 1.0
        self.b = 0.0

    def fit(self, y_true, y_prob) -> None:
        # placeholder platt fit
        return None

    def predict(self, p):
        p = np.clip(p, 1e-6, 1 - 1e-6)
        logits = self.a * p + self.b
        return 1 / (1 + np.exp(-logits))
