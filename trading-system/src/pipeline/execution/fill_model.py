from __future__ import annotations

import math


class FillModel:
    def estimate_fill_prob(self, distance_bps: float, spread_bps: float, imbalance: float, rv: float) -> float:
        z = -(distance_bps / max(spread_bps, 1.0)) + imbalance - rv * 100
        prob = 1 / (1 + math.exp(-z))
        return max(0.0, min(1.0, prob))
