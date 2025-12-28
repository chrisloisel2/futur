from domain.state.targets import TargetPosition
from pipeline.risk.order_builder import OrdersPlanBuilder


def test_orders_plan_builder_creates_order():
    builder = OrdersPlanBuilder()
    t = TargetPosition(event_time=None, book="book_a", symbol="BTC", instrument_type="perp", side="LONG", notional_usd=1000, leverage=1.0, entry_style="taker", risk_hints={})
    plan = builder.build([t], {}, run_id="run", risk_state_ref="portfolio")
    assert plan.orders
