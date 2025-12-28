import pandas as pd

from pipeline.quality.checks import CrossSourceConsistencyCheck
from domain.state.quality import QualityFlag


def test_cross_source_flag():
    df = pd.DataFrame(
        {
            "event_time_aligned": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "mid_price": [100.0, 120.0],
            "source": ["spot", "futures"],
            "quality_flags": [0, 0],
        }
    )
    check = CrossSourceConsistencyCheck(max_premium_bps=10.0)
    out = check.apply(df)
    assert (out["quality_flags"] & int(QualityFlag.CROSS_SOURCE_MISMATCH)).any()
