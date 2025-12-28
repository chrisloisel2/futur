from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ScenarioResult:
    scenario_id: str
    severity: str
    loss_estimate_usd: float
    drawdown_estimate: float
    pass_fail: str
    actions_required: List[str] = field(default_factory=list)
