import pandas as pd

from pipeline.monitoring.drift.data_drift import DataDriftDetector


def test_missingness_computed():
    det = DataDriftDetector({"features": {"x_fast": ["a"]}})
    df = pd.DataFrame({"symbol": ["BTC"], "a": [None]})
    report = det.compute(df, pd.DataFrame())
    assert report.by_symbol["BTC"]["a"].missing_rate == 1.0
