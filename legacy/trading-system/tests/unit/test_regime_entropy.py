import pandas as pd

from pipeline.models.regime.classifier import RegimeClassifierModel


def test_regime_entropy_present():
    model = RegimeClassifierModel(["a", "b"])
    out = model.predict(pd.DataFrame({"x": [1, 2]}))
    assert "entropy" in out
