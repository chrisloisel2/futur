import pandas as pd

from pipeline.risk.var_cvar import VaREngine


def test_parametric_var_positive():
    returns = pd.Series([0.01, -0.02, 0.0, 0.003])
    var, cvar = VaREngine(method="parametric").compute(returns)
    assert var >= 0
    assert cvar >= 0
