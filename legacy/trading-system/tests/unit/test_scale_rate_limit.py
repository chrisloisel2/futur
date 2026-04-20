from pipeline.meta_control.scaler import MetaScaler, MetaScalerConfig


def test_scale_rate_limit_up():
    scaler = MetaScaler(MetaScalerConfig(scale_rate_limit_up_per_min=0.1))
    out = scaler.smooth_scale(1.0, prev_scale=0.0, dt_seconds=60)
    assert out <= 0.1
