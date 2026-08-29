from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from build_a8_leverage_panel import _binance_oi_row_to_events, _liquidation_row_to_event


def test_binance_oi_row_to_events_emits_four_kinds():
    row = {
        "symbol": "BTCUSDT",
        "recv_time": 1_700_000_000_000,
        "open_interest": 12345.6,
        "mark_price": 60000.0,
        "index_price": 59990.0,
        "funding_rate": 0.0001,
    }
    events = _binance_oi_row_to_events(row)
    assert {e["kind"] for e in events} == {"open_interest", "mark", "index", "funding"}
    assert all(e["venue"] == "binance" and e["symbol"] == "BTCUSDT" for e in events)
    assert all(e["receive_ts_ns"] == 1_700_000_000_000 * 1_000_000 for e in events)
    oi = next(e for e in events if e["kind"] == "open_interest")
    assert oi["value"] == 12345.6


def test_binance_oi_row_to_events_skips_non_finite_fields():
    row = {"symbol": "BTCUSDT", "recv_time": 1, "open_interest": None, "mark_price": float("nan"), "index_price": 1.0, "funding_rate": 0.0}
    events = _binance_oi_row_to_events(row)
    assert {e["kind"] for e in events} == {"index", "funding"}


def test_liquidation_row_to_event_lowercases_side_raw():
    row = {"venue": "bybit", "symbol": "BTCUSDT", "recv_time": 5, "usd": 1000.0, "side_raw": "Sell"}
    event = _liquidation_row_to_event(row)
    assert event == {
        "venue": "bybit",
        "symbol": "BTCUSDT",
        "kind": "liquidation",
        "value": 1000.0,
        "side": "sell",
        "receive_ts_ns": 5 * 1_000_000,
    }
