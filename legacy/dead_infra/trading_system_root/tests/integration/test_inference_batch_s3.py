import pandas as pd

from pipeline.models.edge.postprocess import enforce_monotonic


def test_postprocess_batch():
    df = pd.DataFrame({"q05": [-0.01], "q50": [0.0], "q95": [-0.02], "p_hit": [0.5]})
    out = enforce_monotonic(df)
    assert out.loc[0, "q05"] <= out.loc[0, "q50"] <= out.loc[0, "q95"]
