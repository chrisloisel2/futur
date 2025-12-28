import pandas as pd

from pipeline.meta_control.scaler import MetaScaler, MetaScalerConfig


def test_meta_scaler_batch_stub():
    scaler = MetaScaler(MetaScalerConfig())
    # simple smoke test
    out = scaler.smooth_scale(0.5, 0.4, dt_seconds=60)
    assert out >= 0.4
