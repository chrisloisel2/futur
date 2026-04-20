from __future__ import annotations

from typing import Dict, List

from domain.risk.scenario_result import ScenarioResult
from domain.state.targets import TargetPosition


class ScenarioEngine:
    def __init__(self, config: Dict):
        self.config = config

    def run(self, targets: List[TargetPosition], portfolio: Dict) -> List[ScenarioResult]:
        results: List[ScenarioResult] = []
        shocks = self.config.get("shocks", {"flash_crash": 0.05, "spread_x10": 0.0})
        for scenario_id, shock in shocks.items():
            loss = sum(abs(t.notional_usd) * shock for t in targets)
            drawdown = loss / max(portfolio.get("equity", 1.0), 1.0)
            pass_fail = "PASS" if drawdown < self.config.get("max_dd", 0.1) else "FAIL"
            actions = []
            if pass_fail == "FAIL":
                actions.append("delever")
            results.append(ScenarioResult(scenario_id=scenario_id, severity="high", loss_estimate_usd=loss, drawdown_estimate=drawdown, pass_fail=pass_fail, actions_required=actions))
        return results
