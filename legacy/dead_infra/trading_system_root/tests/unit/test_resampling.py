import pandas as pd

from pipeline.data.resampling import BarBuilder


def test_build_ohlcv_and_returns():
    df = pd.DataFrame(
        {
            "event_time": pd.date_range("2024-01-01", periods=5, freq="1min"),
            "price": [1, 2, 3, 4, 5],
            "qty": [1, 1, 1, 1, 1],
        }
    )
    builder = BarBuilder(freq="2min")
    ohlcv = builder.build_ohlcv(df, freq="2min")
    assert not ohlcv.empty
    rets = builder.build_returns(ohlcv)
    assert "return" in rets.columns
