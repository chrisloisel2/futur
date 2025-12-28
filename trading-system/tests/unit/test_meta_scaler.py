import pandas as pd

from domain.signal.signal import DecisionStatus, Signal, SignalDirection, TradeMode
from pipeline.meta_control.scaler import MetaScaler, MetaScalerConfig


def test_meta_scaler_zero_when_not_confirm():
    scaler = MetaScaler(MetaScalerConfig())
    sig = Signal(
        event_time=pd.Timestamp("2024-01-01"),
        symbol="BTCUSDT",
        tradeable=False,
        mode=TradeMode.OFF,
        direction=SignalDirection.FLAT,
        decision_status=DecisionStatus.INVALIDATE,
        coarse_direction=SignalDirection.FLAT,
        regime_probs={},
        regime_entropy=0.5,
        quantiles={"Q50": 0},
        p_hit=0.5,
        expected_shortfall=0.0,
        rv_fwd={},
        confidence_raw=0.5,
        confidence_calibrated=0.5,
        novelty_score=0.0,
        disagreement_score=0.0,
        quality_flags=0,
        reasons=[],
        model_version="v1",
        run_id="run",
    )
    scale = scaler.compute_scale(sig, pd.Series(), {})
    assert scale == 0.0
