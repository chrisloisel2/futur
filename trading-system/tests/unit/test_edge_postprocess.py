import pandas as pd

from pipeline.models.edge.postprocess import enforce_monotonic


def test_monotonic_quantiles():
    df = pd.DataFrame({"q05": [0.1], "q50": [0.0], "q95": [-0.1], "p_hit": [1.2]})
    out = enforce_monotonic(df)
    assert out.loc[0, "q05"] <= out.loc[0, "q50"] <= out.loc[0, "q95"]
    assert out.loc[0, "p_hit"] <= 1.0
