import json

import numpy as np
import pandas as pd

from alpha_foundry_v5.data_planes.common import infer_run_window, iter_causal_records
from alpha_foundry_v5.data_planes.derivatives import DerivativesPlaneState
from alpha_foundry_v5.data_planes.event_microstructure import EventMicrostructureState
from alpha_foundry_v5.labs.registry import LabRegistry


def test_event_state_recovers_removed_notional_and_trade_flow():
    state = EventMicrostructureState(["binance"], ["BTCUSDT"], [1000])
    state.ingest_book({"venue": "binance", "symbol": "BTCUSDT", "receive_ts_ns": 100, "source_stream": "depth", "side": "bid", "price": 100.0, "qty": 5.0, "event_type": "snapshot"})
    state.ingest_book({"venue": "binance", "symbol": "BTCUSDT", "receive_ts_ns": 200, "source_stream": "depth", "side": "bid", "price": 100.0, "qty": 0.0, "event_type": "remove"})
    state.ingest_trade({"venue": "binance", "symbol": "BTCUSDT", "receive_ts_ns": 300, "price": 101.0, "qty": 2.0, "aggressor": "buy", "granularity": "aggregate"})
    row = state.row(500, "BTCUSDT")
    assert row["binance__remove_bid_notional_1000ms"] == 500.0
    assert row["binance__remove_count_1000ms"] == 1.0
    assert row["binance__signed_notional_1000ms"] == 202.0
    assert row["binance__aggregate_fraction_1000ms"] == 1.0
    assert row["binance__book_event_available_ts_ns"] == 200
    assert row["binance__trade_available_ts_ns"] == 300


def test_bbo_snapshots_do_not_fabricate_queue_events():
    state = EventMicrostructureState(["binance"], ["BTCUSDT"], [1000])
    state.ingest_book({"venue": "binance", "symbol": "BTCUSDT", "receive_ts_ns": 100, "source_stream": "bookTicker", "side": "bid", "price": 100.0, "qty": 5.0, "event_type": "snapshot"})
    row = state.row(500, "BTCUSDT")
    assert row["binance__remove_count_1000ms"] == 0.0
    assert np.isnan(row["binance__book_event_available_ts_ns"])


def test_derivatives_keep_venue_specific_state_and_liquidation_windows():
    state = DerivativesPlaneState(["bybit"], ["BTCUSDT"], [10000])
    state.ingest({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 100, "kind": "open_interest", "value": 1000.0})
    state.ingest({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 200, "kind": "open_interest", "value": 1100.0})
    state.ingest({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 210, "kind": "funding", "value": 0.0001})
    state.ingest({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 220, "kind": "mark", "value": 101.0})
    state.ingest({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 230, "kind": "index", "value": 100.0})
    state.ingest({"venue": "bybit", "symbol": "BTCUSDT", "receive_ts_ns": 240, "kind": "liquidation", "value": 50000.0, "side": "long"})
    row = state.row(500, "BTCUSDT")
    assert abs(row["bybit__open_interest_change_pct"] - 0.1) < 1e-12
    assert abs(row["bybit__basis_bps"] - 100.0) < 1e-12
    assert row["bybit__liquidation_long_usd_10000ms"] == 50000.0
    assert row["bybit__funding_available_ts_ns"] == 210
    assert row["deriv__available_ts_ns"] == 240


def test_generic_reader_repairs_receive_time_inversion(tmp_path):
    path = tmp_path / "raw" / "trades" / "venue=x" / "symbol=BTCUSDT" / "date=2026-01-01"
    path.mkdir(parents=True)
    rows = [
        {"receive_ts_ns": 200, "venue": "x", "symbol": "BTCUSDT"},
        {"receive_ts_ns": 100, "venue": "x", "symbol": "BTCUSDT"},
        {"receive_ts_ns": 300, "venue": "x", "symbol": "BTCUSDT"},
    ]
    (path / "events.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    got = list(iter_causal_records(str(tmp_path), "trades", 1, 400, ["x"], ["BTCUSDT"]))
    assert [x["receive_ts_ns"] for x in got] == [100, 200, 300]


def test_cross_asset_lab_stays_blocked_on_three_symbol_universe():
    registry = LabRegistry()
    rows = []
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        for i in range(10):
            rows.append({"asof_ns": i + 1, "symbol": symbol, "cross_asset__leader_innovation": 1.0, "cross_asset__residual": 0.1, "cross_asset__beta": 1.0})
    frame = pd.DataFrame(rows)
    a12 = registry.readiness("A12", frame)
    a13 = registry.readiness("A13", frame)
    assert a12["data_ready"] is True and a12["symbol_ready"] is False and a12["ready"] is False
    assert a12["symbol_count"] == 3 and a12["min_symbols"] == 8
    assert a13["data_ready"] is True and a13["symbol_ready"] is False and a13["ready"] is False
    assert a13["min_symbols"] == 12


def test_run_window_parse():
    assert infer_run_window("/x/run=100-200/cadence=100ms") == (100, 200)
