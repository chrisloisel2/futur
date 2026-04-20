from pipeline.risk.var_cvar import fractional_kelly


def test_fractional_kelly_bounds():
    k = fractional_kelly(0.6, 2.0, cap=0.5, shrink=0.5)
    assert 0 <= k <= 0.5
