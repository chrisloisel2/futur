import pandas as pd

from pipeline.monitoring.drift.perf_drift import PerformanceDriftDetector


def test_perf_drift_slippage():
    det = PerformanceDriftDetector({})
    pnl = pd.DataFrame({"symbol": ["BTC"], "pnl_usd": [1]})
    fills = pd.DataFrame()
    costs = pd.DataFrame({"symbol": ["BTC"], "realized_slippage_bps": [5]})
    rep = det.compute(pnl, fills, costs, pd.DataFrame())
    assert rep.by_symbol["BTC"]["pnl_usd"] == 1
