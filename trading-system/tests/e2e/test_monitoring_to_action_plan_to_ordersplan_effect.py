from pipeline.monitoring.actions import ActionEngine


def test_action_plan_defensive():
    engine = ActionEngine({})
    reports = {"event_time": None, "run_id": "run", "perf_drift": {"by_symbol": {"BTC": {"severity": "CRIT"}}}}
    plan = engine.decide_actions(reports, {})
    assert plan.actions
