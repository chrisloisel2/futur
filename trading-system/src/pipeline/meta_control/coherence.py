from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np


@dataclass
class CoherenceConfig:
    min_score: float = 0.0


def compute_coherence(signal_probs: Dict[str, float], quantiles: Dict[str, float], stress_flags: Dict[str, bool]) -> float:
    probs = np.array(list(signal_probs.values())) if signal_probs else np.array([0.0])
    entropy = -np.nansum(probs * np.log(probs + 1e-9))
    variance = float(np.nanstd(list(quantiles.values()))) if quantiles else 0.0
    stress_penalty = 0.2 if any(stress_flags.values()) else 0.0
    score = max(0.0, 1.0 - entropy - variance - stress_penalty)
    return float(np.clip(score, 0.0, 1.0))
