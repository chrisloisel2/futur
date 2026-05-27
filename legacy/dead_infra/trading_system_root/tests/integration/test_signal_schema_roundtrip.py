import json
import pandas as pd

from domain.signal.signal import DecisionStatus, Signal, SignalDirection, TradeMode


def test_signal_to_dict_flatten():
    sig = Signal(
        event_time=pd.Timestamp("2024-01-01"),
        symbol="BTCUSDT",
        tradeable=True,
        mode=TradeMode.TAKER,
        direction=SignalDirection.LONG,
        decision_status=DecisionStatus.CONFIRM,
        coarse_direction=SignalDirection.LONG,
        regime_probs={"impulse": 0.6, "reversal": 0.4},
        regime_entropy=0.5,
        quantiles={"Q05": -0.001, "Q50": 0.002, "Q95": 0.005},
        p_hit=0.6,
        expected_shortfall=-0.001,
        rv_fwd={"mean": 0.001},
        confidence_raw=0.6,
        confidence_calibrated=0.65,
        novelty_score=0.1,
        disagreement_score=0.2,
        quality_flags=0,
        reasons=[],
        model_version="v1",
        run_id="run",
    )
    d = sig.to_dict()
    assert d["regime_prob_impulse"] == 0.6
    assert d["q50"] == 0.002
