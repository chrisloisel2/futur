import pandas as pd

from pipeline.monitoring.drift.regime_drift import RegimeDriftDetector


def test_regime_drift_counts():
    det = RegimeDriftDetector({})
    df = pd.DataFrame({"regime": ["a", "b", "a"]})
    rep = det.compute(df, pd.DataFrame())
    assert "transition_rate" in rep.global_stats
