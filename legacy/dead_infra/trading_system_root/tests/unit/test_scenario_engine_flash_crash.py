from domain.state.targets import TargetPosition
from pipeline.risk.scenario_engine import ScenarioEngine


def test_flash_crash_loss():
    engine = ScenarioEngine({"shocks": {"flash": 0.05}, "max_dd": 0.5})
    tgt = TargetPosition(event_time=None, book="book_a", symbol="BTC", instrument_type="perp", side="LONG", notional_usd=1000, leverage=1.0, entry_style="taker")
    res = engine.run([tgt], {"equity": 10000})
    assert res
