import pandas as pd

from pipeline.books.book_a_directional import BookADirectional, BookADirectionalConfig


def test_book_a_sizes_notional():
    book = BookADirectional(BookADirectionalConfig())
    state = pd.Series({"equity": 100000})
    signal = {"tradeable": True, "decision_status": "CONFIRM", "direction": "LONG", "expected_shortfall": 0.001, "quantiles": {"Q50": 0.002}}
    alloc = {"scale": 0.5, "asset_weights": {"BTC": 0.5}, "equity": 100000, "trade_mode": "TAKER"}
    targets = book.propose_targets("BTC", state, signal, alloc, {}, {})
    assert targets
    assert targets[0].notional_usd > 0
