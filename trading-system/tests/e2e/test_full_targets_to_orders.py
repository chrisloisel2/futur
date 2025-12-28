from datetime import datetime

from domain.state.targets import TargetPosition, TargetPositions
from pipeline.risk.controller import RiskController


def test_full_targets_to_orders_e2e():
    controller = RiskController({})
    tgt = TargetPosition(event_time=datetime.utcnow(), book="book_a", symbol="BTC", instrument_type="perp", side="LONG", notional_usd=1000, leverage=1.0, entry_style="taker")
    tp = TargetPositions(event_time=datetime.utcnow(), run_id="run", model_stack="v1", feature_set="v1", targets=[tgt], book_summary={}, allocator_reasons=[])
    risk_state, orders_plan = controller.step(tp, {"equity": 10000, "positions": {}}, {}, {}, {})
    assert orders_plan.orders
