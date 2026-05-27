import pandas as pd

from domain.state.alloc import Alloc


def test_alloc_to_dataframe_roundtrip():
    alloc = Alloc(event_time=pd.Timestamp("2024-01-01"), run_id="run", model_stack="v1", feature_set="v1", scale=0.5, leverage_target=1.0)
    df = pd.DataFrame([alloc.__dict__])
    assert "scale" in df.columns
