import pandas as pd

from pipeline.risk.var_cvar import VaREngine


def test_historical_var():
    returns = pd.Series([0.01, -0.02, 0.0, 0.003])
    var, _ = VaREngine(method="historical").compute(returns, alpha=0.8)
    assert var >= 0
