import pandas as pd

from pipeline.monitoring.drift.pred_drift import PredictionDriftDetector


def test_prediction_drift_shift():
    det = PredictionDriftDetector({})
    df = pd.DataFrame({"symbol": ["BTC"], "p_hit": [0.6], "entropy": [0.5]})
    rep = det.compute(df, pd.DataFrame())
    assert "p_hit_shift" in rep.by_symbol["BTC"]
