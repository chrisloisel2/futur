import pandas as pd

from domain.state.targets import TargetPosition


def test_target_position_to_df():
    t = TargetPosition(event_time=pd.Timestamp("2024-01-01"), book="book_a", symbol="BTC", instrument_type="perp", side="LONG", notional_usd=1.0, leverage=1.0, entry_style="taker")
    df = pd.DataFrame([t.__dict__])
    assert "notional_usd" in df.columns
