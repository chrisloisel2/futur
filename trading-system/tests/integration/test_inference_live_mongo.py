import pandas as pd

from pipeline.decision.signal_builder import SignalBuilder
from pipeline.decision.logic import DecisionLogic
from domain.signal.signal import DecisionStatus, SignalDirection, TradeMode


def test_signal_builder_live():
    builder = SignalBuilder(threshold_bps=5.0)
    state = pd.Series({"event_time": pd.Timestamp("2024-01-01"), "symbol": "BTCUSDT", "quality_flags": 0, "feature_set": "v1"})
    gating = {"tradeable": True, "mode": TradeMode.TAKER, "coarse_direction": SignalDirection.FLAT, "veto_reasons": []}
    regime = {"regime_probs": {"impulse": 0.5}, "regime_entropy": 0.1}
    edge = {"q05": -0.001, "q50": 0.002, "q95": 0.004, "p_hit": 0.6, "expected_shortfall": -0.001, "rv_mean": 0.002}
    comp = {"novelty_score": 0.1, "disagreement_score": 0.1}
    sig = builder.build(state, gating, regime, edge, comp, run_id="run", model_version="v1")
    sig = DecisionLogic().apply(sig)
    assert sig.decision_status in {DecisionStatus.CONFIRM, DecisionStatus.DELAY}
