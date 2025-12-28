from __future__ import annotations

from dataclasses import dataclass

from domain.risk.budgets import RiskBudgets
from domain.risk.scenarios import ScenarioState


@dataclass
class LeverageEngineConfig:
    max_step_per_min: float = 0.2


class LeverageEngine:
    def __init__(self, config: LeverageEngineConfig):
        self.config = config

    def compute_leverage(self, scale: float, risk_state: dict, state_row, signal) -> float:
        base = scale * max(0.5, 1 - risk_state.get("dd", 0))
        vol = float(state_row.get("x_mid_rv_5m", 0) or 0)
        if vol > 0.01:
            base *= 0.5
        return max(0.0, base)

    def apply_caps(self, leverage_target: float, budgets: RiskBudgets, scenario: ScenarioState) -> float:
        cap = budgets.max_leverage
        for reg, lv in budgets.meta_leverage_caps_by_regime.items():
            cap = min(cap, lv)
        if any(scenario.scenario_flags.values()):
            cap = min(cap, 0.5)
        return min(leverage_target, cap)

    def rate_limit(self, leverage_prev: float, leverage_target: float, dt_seconds: float) -> float:
        if leverage_target < leverage_prev:
            return leverage_target
        step = self.config.max_step_per_min * dt_seconds / 60.0
        return min(leverage_target, leverage_prev + step)
