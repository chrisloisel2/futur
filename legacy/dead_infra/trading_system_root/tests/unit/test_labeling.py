import pandas as pd

from pipeline.research.labeling import EventDrivenLabeler, LabelingConfig


def test_labeling_triple_barrier_tp_hit():
    df = pd.DataFrame(
        {
            "event_time": pd.date_range("2024-01-01", periods=5, freq="1min"),
            "mid_price": [100, 101, 102, 103, 104],
            "symbol": "BTCUSDT",
        }
    )
    labeler = EventDrivenLabeler(LabelingConfig(horizons_s=[180], tp_bps=50, sl_bps=50))
    labels = labeler.label(df)
    assert len(labels) == 5
    assert labels.iloc[0].tp_hit is True
    assert labels.iloc[0].barrier_hit == "tp"
