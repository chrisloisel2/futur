import pandas as pd

from pipeline.execution.maker import MakerExecutor, QuoteResult
from domain.orders.order_plan import ExecutionDirective


def test_quote_distance_widens_with_spread():
    maker = MakerExecutor({"base_quote_distance_bps": 1.0, "max_quote_distance_bps": 10.0})
    ms = pd.Series({"mid_price": 100, "x_fast_spread_bps": 20})
    q = maker.apply_quotes("BTC", 1.0, ms, ExecutionDirective())
    assert isinstance(q, QuoteResult)
    assert q.ask_price > q.bid_price
