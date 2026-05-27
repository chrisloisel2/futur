from pipeline.risk.scenario_engine import ScenarioEngine


def test_scenario_fail_triggers_action():
    engine = ScenarioEngine({"shocks": {"flash": 0.2}, "max_dd": 0.1})
    res = engine.run([], {"equity": 10000})
    assert any(r.actions_required for r in res)
