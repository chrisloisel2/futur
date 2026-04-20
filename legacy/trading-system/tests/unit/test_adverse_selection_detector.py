import pandas as pd

from pipeline.execution.adverse_selection import AdverseSelectionDetector


def test_adverse_score_nonnegative():
    det = AdverseSelectionDetector()
    score = det.score({}, pd.Series({"x_fast_spread_bps": 10, "x_mid_rv_5m": 0.01}), None)
    assert score >= 0
