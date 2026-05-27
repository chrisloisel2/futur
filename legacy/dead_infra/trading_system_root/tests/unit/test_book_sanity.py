import pandas as pd

from pipeline.quality.checks import BookSanityCheck
from domain.state.quality import QualityFlag


def test_book_sanity_spread_anomaly():
    df = pd.DataFrame(
        {
            "spread": [10.0],
            "mid_price": [1.0],
            "bid_px": [[1.0]],
            "ask_px": [[2.0]],
            "bid_sz": [[1.0]],
            "ask_sz": [[1.0]],
        }
    )
    check = BookSanityCheck(max_spread_bps=100.0, min_depth=1)
    out = check.apply(df)
    assert (out.loc[0, "quality_flags"] & int(QualityFlag.SPREAD_ANOMALY)) > 0
    assert (out.loc[0, "quality_flags"] & int(QualityFlag.BOOK_INVALID)) > 0
