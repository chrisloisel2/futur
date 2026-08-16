from market_physics_v3.schema import TradeEvent
from alpha_foundry_v4.trade_tape import TradeTapeBuilder, build_trade_tape


def _trade(ts, venue="binance", symbol="BTCUSDT", side="buy", price=100.0, qty=1.0, granularity="individual"):
    return TradeEvent(venue=venue, symbol=symbol, event_ts_ns=ts - 1, receive_ts_ns=ts, trade_id=str(ts), price=price, qty=qty, aggressor=side, granularity=granularity)


def test_trade_tape_builds_clock_and_event_windows_with_modality():
    b = TradeTapeBuilder(venues=("binance",), symbols=("BTCUSDT",), time_windows_ms=(500,), event_windows=(2,))
    b.ingest(_trade(1_000_000_000, side="buy", price=100.0, qty=1.0, granularity="aggregate"))
    b.ingest(_trade(1_100_000_000, side="sell", price=101.0, qty=0.5, granularity="aggregate"))
    row = b.state(1_200_000_000, "BTCUSDT")
    assert row["binance__trade_count_500ms"] == 2.0
    assert row["binance__aggregate_fraction_500ms"] == 1.0
    assert row["binance__individual_fraction_500ms"] == 0.0
    assert "binance__signed_notional_last2" in row


def test_trade_tape_is_receive_time_causal():
    events = iter([_trade(1_100_000_000), _trade(1_300_000_000)])
    rows = list(build_trade_tape(events, start_ns=1_000_000_000, stop_ns=1_400_000_000, cadence_ms=100, venues=("binance",), symbols=("BTCUSDT",)))
    assert rows[0]["binance__trade_count_100ms"] == 1.0
    assert rows[1]["binance__trade_count_100ms"] == 0.0
    assert rows[2]["binance__trade_count_100ms"] == 1.0
