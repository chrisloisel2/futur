import pandas as pd

from pipeline.quality.checks import DuplicateCheck
from domain.state.quality import QualityFlag


def test_duplicates_flagged():
    df = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "venue": ["binance", "binance"],
            "source": ["spot", "spot"],
            "event_type": ["trade", "trade"],
            "seq": [1, 1],
            "event_time_aligned": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        }
    )
    check = DuplicateCheck()
    out = check.apply(df)
    assert out.loc[1, "duplicate"] is True
    assert (out.loc[1, "quality_flags"] & int(QualityFlag.DUPLICATE_EVENT)) > 0
