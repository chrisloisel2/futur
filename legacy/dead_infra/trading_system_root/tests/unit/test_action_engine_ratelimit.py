from pipeline.monitoring.actions import ActionEngine


def test_action_engine_no_actions_when_ok():
    engine = ActionEngine({})
    plan = engine.decide_actions({"perf_drift": {"by_symbol": {}}}, {})
    assert plan.actions == []
