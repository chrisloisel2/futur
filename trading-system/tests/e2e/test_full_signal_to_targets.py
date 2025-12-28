import pandas as pd

from pipeline.books import MultiBookAlphaEngine


def test_full_signal_to_targets_e2e():
    engine = MultiBookAlphaEngine({})
    states = {"BTC": pd.Series({"event_time": pd.Timestamp("2024-01-01"), "s_slow_funding_z": 0})}
    signals = {"BTC": {"tradeable": True, "decision_status": "CONFIRM", "direction": "LONG", "quantiles": {"Q50": 0.001}}}
    alloc = {"scale": 0.5, "asset_weights": {"BTC": 0.5}, "trade_mode": "TAKER", "equity": 100000}
    tgt, books_state, dec = engine.step(states, signals, alloc, {}, {}, None, {}, {"BTC": "default"}, "run", "v1", "v1")
    assert tgt.targets
