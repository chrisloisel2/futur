from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class AdaptiveThresholdsConfig:
    min_confidence: float = 0.6
    max_entropy: float = 1.5
    novelty_soft_cap: float = 0.5
    disagreement_soft_cap: float = 0.5
    cooldown_base_seconds: int = 0
    cooldown_max_seconds: int = 300
    smoothing_alpha: float = 0.2


class AdaptiveThresholds:
    def __init__(self, config: AdaptiveThresholdsConfig):
        self.config = config

    def update_thresholds(self, perf_snapshot: Dict, drift_snapshot: Dict, prev_thresholds: Dict[str, float]) -> Dict[str, float]:
        thresholds = prev_thresholds.copy() if prev_thresholds else {}
        alpha = self.config.smoothing_alpha
        degrade = False
        if perf_snapshot:
            max_dd = max([v.get("max_dd", 0) for v in perf_snapshot.get("by_regime", {}).values()] + [0])
            if max_dd > 0.1:
                degrade = True
        if drift_snapshot:
            if drift_snapshot.get("perf_drift_score", 0) > 0.2:
                degrade = True
        base = {
            "min_confidence": self.config.min_confidence,
            "max_entropy": self.config.max_entropy,
            "novelty_soft_cap": self.config.novelty_soft_cap,
            "disagreement_soft_cap": self.config.disagreement_soft_cap,
        }
        for k, v in base.items():
            target = v + 0.05 if degrade else v
            thresholds[k] = alpha * target + (1 - alpha) * thresholds.get(k, v)
        cooldown = self.config.cooldown_base_seconds + (self.config.cooldown_base_seconds if degrade else 0)
        thresholds["cooldown_seconds"] = min(self.config.cooldown_max_seconds, cooldown)
        return thresholds
