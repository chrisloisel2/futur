from pipeline.execution.maker import MakerExecutor
from domain.orders.order_plan import ExecutionDirective
import pandas as pd


def test_maker_quote_size_capped():
    maker = MakerExecutor({"max_size_usd": 100})
    q = maker.apply_quotes("BTC", 500, pd.Series({"mid_price": 100}), ExecutionDirective())
    assert q.bid_size <= 100 or q.ask_size <= 100
