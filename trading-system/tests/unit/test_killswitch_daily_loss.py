from pipeline.risk.controller import RiskController


def test_killswitch_daily_loss():
    controller = RiskController({"killswitch": {"daily_loss_limit_usd": -100}})
    assert controller._killswitch({"daily_loss": -200, "drawdown": 0}) is True
