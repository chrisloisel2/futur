import pandas as pd

from pipeline.books.book_a_directional import BookADirectional, BookADirectionalConfig


def test_book_a_respects_decision():
    book = BookADirectional(BookADirectionalConfig())
    targets = book.propose_targets("BTC", pd.Series(), {"tradeable": False}, {}, {}, {})
    assert targets == []
