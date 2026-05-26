import pandas as pd

from data_pipeline.joins import point_in_time_join


def test_point_in_time_join_is_backward_only():
    base = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2024-01-01 01:00:00Z",
            "2024-01-01 02:00:00Z",
        ]),
        "close": [100.0, 101.0],
    })
    context = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2024-01-01 00:30:00Z",
            "2024-01-01 01:30:00Z",
            "2024-01-01 02:30:00Z",
        ]),
        "value": [1.0, 2.0, 99.0],
    })

    joined = point_in_time_join(
        base,
        [("ctx", context, pd.Timedelta("45m"))],
    )

    assert joined["ctx_value"].tolist() == [1.0, 2.0]

