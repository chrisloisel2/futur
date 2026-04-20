from pipeline.risk.controller import RiskController


def test_killswitch_drawdown():
    controller = RiskController({"killswitch": {"max_drawdown": 0.1}})
    assert controller._killswitch({"drawdown": 0.2}) is True
