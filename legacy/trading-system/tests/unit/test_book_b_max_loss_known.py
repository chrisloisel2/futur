import pandas as pd

from pipeline.books.book_b_convexity import BookBConvexity, BookBConvexityConfig


def test_book_b_overlay_disabled():
    book = BookBConvexity(BookBConvexityConfig(enabled=False))
    assert book.propose_targets("BTC", pd.Series(), {}, {}, {}, {}) == []
