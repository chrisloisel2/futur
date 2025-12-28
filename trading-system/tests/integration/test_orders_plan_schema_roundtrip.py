import pandas as pd

from domain.orders.order_plan import OrdersPlan


def test_orders_plan_dataframe():
    plan = OrdersPlan(event_time=pd.Timestamp("2024-01-01"), run_id="run", orders=[], stops=[], time_stops=[], risk_state_ref="portfolio")
    df = pd.DataFrame([plan.__dict__])
    assert "orders" in df.columns
