from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np

from domain.signal.signal import DecisionStatus, Signal


@dataclass
class MetaScalerConfig:
    scale_smoothing_alpha: float = 0.2
    scale_rate_limit_up_per_min: float = 0.2
    scale_drop_fast: bool = True
    base_scale: float = 0.0


class MetaScaler:
    def __init__(self, config: MetaScalerConfig):
        self.config = config

    def compute_scale(self, signal: Signal, state_row, meta_inputs: Dict[str, float]) -> float:
        if signal.decision_status != DecisionStatus.CONFIRM or not signal.tradeable:
            return 0.0
        penalties = []
        spread = float(state_row.get("x_fast_spread_bps", state_row.get("spread", 0) or 0))
        penalties.append(min(1.0, spread / 1000))
        depth = float(state_row.get("x_fast_depth_usd", 0) or 0)
        depth_penalty = 0.0 if depth > 100_000 else 0.5
        penalties.append(depth_penalty)
        novelty = float(signal.novelty_score)
        disagreement = float(signal.disagreement_score)
        entropy = float(signal.regime_entropy)
        penalties.append(min(1.0, novelty / 5))
        penalties.append(min(1.0, disagreement))
        penalties.append(min(1.0, entropy))
        bonus = float(signal.confidence_calibrated)
        scale = max(0.0, bonus - sum(penalties))
        return float(np.clip(scale, 0.0, 1.0))

    def smooth_scale(self, scale_raw: float, prev_scale: float, dt_seconds: float) -> float:
        if scale_raw < prev_scale and self.config.scale_drop_fast:
            return scale_raw
        rate_limit = self.config.scale_rate_limit_up_per_min * dt_seconds / 60.0
        allowed = prev_scale + rate_limit
        target = min(scale_raw, allowed)
        alpha = self.config.scale_smoothing_alpha
        return float(alpha * target + (1 - alpha) * prev_scale)
