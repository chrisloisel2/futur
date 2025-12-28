from pipeline.monitoring.actions import ActionEngine


def test_action_engine_freeze_on_crit():
    engine = ActionEngine({"freeze_ttl_seconds": 60})
    reports = {"event_time": None, "run_id": "run", "perf_drift": {"by_symbol": {"BTC": {"severity": "CRIT"}}}}
    plan = engine.decide_actions(reports, {})
    assert plan.actions
