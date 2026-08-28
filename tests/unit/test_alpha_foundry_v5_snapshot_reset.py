from alpha_foundry_v5.data_planes.event_microstructure import EventMicrostructureState


def test_new_deep_snapshot_drops_ghost_levels_before_future_remove():
    state = EventMicrostructureState(["bybit"], ["BTCUSDT"], [1000])
    # First snapshot contains a bid at 100.
    state.ingest_book({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 100, "sequence_id": 1, "source_stream": "orderbook.50", "side": "bid", "price": 100.0, "qty": 5.0, "event_type": "snapshot"})
    state.ingest_book({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 100, "sequence_id": 1, "source_stream": "orderbook.50", "side": "ask", "price": 101.0, "qty": 4.0, "event_type": "snapshot"})
    # Reconnect snapshot no longer contains bid 100. One reset must happen for
    # the whole batch, not once per level.
    state.ingest_book({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 200, "sequence_id": 2, "source_stream": "orderbook.50", "side": "bid", "price": 99.0, "qty": 6.0, "event_type": "snapshot"})
    state.ingest_book({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 200, "sequence_id": 2, "source_stream": "orderbook.50", "side": "ask", "price": 102.0, "qty": 3.0, "event_type": "snapshot"})
    # A later zero update for the old level must not fabricate 5*100 notional.
    state.ingest_book({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 300, "sequence_id": 3, "source_stream": "orderbook.50", "side": "bid", "price": 100.0, "qty": 0.0, "event_type": "remove"})
    row = state.row(500, "BTCUSDT")
    assert row["bybit__remove_bid_notional_1000ms"] == 0.0
    assert row["bybit__remove_count_1000ms"] == 1.0
