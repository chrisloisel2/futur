from domain.signal.signal import DecisionStatus, Signal, SignalDirection, TradeMode
from pipeline.decision.logic import DecisionLogic


def test_decision_invalidates_untradeable():
    sig = Signal(
        event_time=None,
        symbol="BTCUSDT",
        tradeable=False,
        mode=TradeMode.OFF,
        direction=SignalDirection.FLAT,
        decision_status=DecisionStatus.CONFIRM,
        coarse_direction=SignalDirection.FLAT,
        regime_probs={},
        regime_entropy=0.0,
        quantiles={"Q05": 0, "Q50": 0, "Q95": 0},
        p_hit=0.5,
        expected_shortfall=0.0,
        rv_fwd={"mean": 0.0},
        confidence_raw=0.5,
        confidence_calibrated=0.5,
        novelty_score=0.0,
        disagreement_score=0.0,
        quality_flags=0,
        reasons=[],
        model_version="v1",
        run_id="run",
    )
    logic = DecisionLogic()
    out = logic.apply(sig)
    assert out.decision_status == DecisionStatus.INVALIDATE
