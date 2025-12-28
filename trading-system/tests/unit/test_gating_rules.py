import pandas as pd

from pipeline.models.gating.rules import GatingRules
from domain.signal.signal import TradeMode


def test_gating_veto_quality():
    rules = GatingRules({"max_spread_bps": 100})
    state = pd.Series({"quality_flags": 1, "x_fast_spread": 0.01})
    decision = rules.apply(state)
    assert decision.tradeable is False
    assert decision.mode == TradeMode.OFF
