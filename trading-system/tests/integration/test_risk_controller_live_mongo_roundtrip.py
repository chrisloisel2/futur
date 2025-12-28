from datetime import datetime

import pandas as pd

from pipeline.risk.controller import RiskController
from domain.state.targets import TargetPosition, TargetPositions


def test_risk_controller_roundtrip():
    controller = RiskController({"risk": {}})
    tgt = TargetPosition(event_time=datetime.utcnow(), book="book_a", symbol="BTC", instrument_type="perp", side="LONG", notional_usd=1000, leverage=1.0, entry_style="taker")
    tp = TargetPositions(event_time=datetime.utcnow(), run_id="run", model_stack="v1", feature_set="v1", targets=[tgt], book_summary={}, allocator_reasons=[])
    risk_state, orders_plan = controller.step(tp, {"equity": 10000, "positions": {}}, {}, {}, {})
    assert risk_state
    assert orders_plan
