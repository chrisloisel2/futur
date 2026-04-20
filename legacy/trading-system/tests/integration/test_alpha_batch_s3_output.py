from pipeline.books.book_a_directional import BookADirectional, BookADirectionalConfig


def test_book_a_smoke():
    BookADirectional(BookADirectionalConfig())
    assert True
