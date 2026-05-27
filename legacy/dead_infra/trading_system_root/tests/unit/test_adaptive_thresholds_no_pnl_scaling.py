from pipeline.meta_control.adaptive_thresholds import AdaptiveThresholds, AdaptiveThresholdsConfig


def test_thresholds_increase_on_degradation():
    cfg = AdaptiveThresholdsConfig(min_confidence=0.6)
    module = AdaptiveThresholds(cfg)
    perf = {"by_regime": {"impulse": {"max_dd": 0.2}}}
    out = module.update_thresholds(perf, {}, {})
    assert out["min_confidence"] >= cfg.min_confidence
