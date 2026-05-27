from domain.orders.order_plan import OrdersPlan, OrderIntent
from domain.orders.order_types import OrderSide, OrderType
from pipeline.execution.engine import ExecutionEngine


def test_ordersplan_to_fills_e2e():
    engine = ExecutionEngine({})
    intent = OrderIntent(symbol="BTC", order_type=OrderType.LIMIT_TO_MARKET, side=OrderSide.BUY, qty=1.0)
    plan = OrdersPlan(event_time=None, run_id="run", orders=[intent], stops=[], time_stops=[], risk_state_ref="portfolio")
    executed, _, _, _ = engine.step(plan, {}, {"BTC": {"mid_price": 100}})
    assert len(executed.fills) >= 1
