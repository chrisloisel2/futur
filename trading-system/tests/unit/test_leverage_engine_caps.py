from domain.risk.budgets import RiskBudgets
from domain.risk.scenarios import ScenarioState
from pipeline.meta_control.leverage_engine import LeverageEngine, LeverageEngineConfig


def test_leverage_cap_applied():
    engine = LeverageEngine(LeverageEngineConfig(max_step_per_min=1.0))
    target = engine.apply_caps(2.0, RiskBudgets(max_leverage=1.0), ScenarioState({"flash_crash": True}))
    assert target <= 0.5
