import pandas as pd

from pipeline.books.book_c_structural import BookCStructural, BookCStructuralConfig


def test_book_c_funding_trigger():
    book = BookCStructural(BookCStructuralConfig(funding_z_trigger=1.0))
    state = pd.Series({"s_slow_funding_z": 2.0, "event_time": pd.Timestamp("2024-01-01")})
    alloc = {"scale": 0.5, "asset_weights": {"BTC": 0.5}, "equity": 100000}
    targets = book.propose_targets("BTC", state, alloc, {}, {})
    assert targets
