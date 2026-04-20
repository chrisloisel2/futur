import pandas as pd

from pipeline.data.normalization import Normalizer


def test_symbol_and_side_normalization_and_book_mid():
    norm = Normalizer(depth=2)
    df = pd.DataFrame(
        {
            "symbol": ["btc/usdt"],
            "side": ["Buy"],
            "price": [100.0],
            "qty": [1.0],
            "bid_px": [[100.0, 99.5]],
            "bid_sz": [[1.0, 1.2]],
            "ask_px": [[100.5, 101.0]],
            "ask_sz": [[1.1, 1.3]],
        }
    )
    out = norm.normalize_events(df)
    assert out.loc[0, "symbol"] == "BTCUSDT"
    assert out.loc[0, "side"] == "buy"
    assert out.loc[0, "mid_price"] == (100.0 + 100.5) / 2
