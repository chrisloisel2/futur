from domain.orders.order_plan import ExecutionDirective, OrderIntent
from domain.orders.order_types import OrderSide, OrderType
from pipeline.execution.taker import TakerExecutor


def test_taker_split_count():
    executor = TakerExecutor({"split_count": 2, "limit_to_market_offset_bps": 1.0})
    intent = OrderIntent(symbol="BTC", order_type=OrderType.LIMIT_TO_MARKET, side=OrderSide.BUY, qty=10.0)
    events = executor.execute(intent, ref_price=100.0)
    assert len(events) == 2
