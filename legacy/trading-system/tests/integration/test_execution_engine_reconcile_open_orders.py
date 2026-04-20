from pipeline.execution.engine import ExecutionEngine
from domain.orders.order_plan import OrdersPlan


def test_engine_step_handles_empty():
    engine = ExecutionEngine({})
    plan = OrdersPlan(event_time=None, run_id="run", orders=[], stops=[], time_stops=[], risk_state_ref="portfolio")
    executed, state, events, costs = engine.step(plan, {}, {})
    assert executed
