from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class MetaControlState:
    coherence_score: float
    scale_raw: float
    scale_smooth: float
    leverage_target: float
    leverage_cap_effective: float
    cooldown_seconds: int
    thresholds_active: Dict[str, float]
    router_selected_assets: List[str]
    router_weights: Dict[str, float]
    reasons: List[str]
