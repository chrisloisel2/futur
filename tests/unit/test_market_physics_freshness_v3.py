import json

import pytest

from market_physics_v3.collectors.qualification import qualify_venue
from market_physics_v3.collectors.runtime import CollectorHealth
from market_physics_v3.microstructure import BookSnapshot
from market_physics_v3.pipeline import MarketPhysicsStateBuilder, VenueWindow
from market_physics_v3.schema import BookLevel, TradeEvent


def _trade(event_ns, receive_ns, trade_id="t"):
    return TradeEvent(
        venue="hyperliquid",
        symbol="BTCUSDT",
        event_ts_ns=event_ns,
        receive_ts_ns=receive_ns,
        trade_id=trade_id,
        price=100.0,
        qty=1.0,
        aggressor="buy",
    )


def _snapshot(event_ns, receive_ns):
    return BookSnapshot(
        event_ts_ns=event_ns,
        bids=(BookLevel(99.0, 10.0),),
        asks=(BookLevel(101.0, 10.0),),
        receive_ts_ns=receive_ns,
    )


def test_collector_health_keeps_stale_backfill_but_marks_it_not_fresh():
    h = CollectorHealth("hyperliquid", fresh_event_max_lag_ms=5000.0)
    fresh = _trade(10_000_000_000, 10_100_000_000, "fresh")
    stale = _trade(20_000_000_000, 63_000_000_000, "backfill")
    h.observe_event(fresh)
    h.observe_event(stale)
    assert h.trade_events == 2
    assert h.fresh_trade_events == 1
    assert h.stale_trade_events == 1
    assert h.fresh_events == 1
    assert h.stale_events == 1
    assert h.max_trade_lag_ms == 43000.0


def test_hyperliquid_qualification_requires_freshness_telemetry(tmp_path):
    root = tmp_path / "data"
    health_dir = tmp_path / "health"
    health_dir.mkdir(parents=True)
    for kind in ("book_events", "trades", "derivatives"):
        p = root / "raw" / kind / "venue=hyperliquid" / "symbol=BTCUSDT" / "date=2026-08-15"
        p.mkdir(parents=True)
        (p / "events.jsonl").write_text('{"receive_ts_ns":2000}\n')
    rw = root / "raw_wire" / "venue=hyperliquid" / "date=2026-08-15"
    rw.mkdir(parents=True)
    (rw / "messages.jsonl").write_text('{"receive_ts_ns":2000}\n')
    health = {
        "venue": "hyperliquid",
        "messages": 200,
        "events": 200,
        "book_events": 100,
        "trade_events": 50,
        "derivative_events": 50,
        "parse_errors": 0,
        "sequence_gaps": 0,
        "subscription_errors": 0,
        "subscription_acks": 12,
        "reconnects": 0,
        "last_exception": None,
        "idle_ms": 10.0,
        "last_receive_ns": 2000,
        "last_event_ns": 1900,
        "clean_shutdown": True,
        "started_ns": 1000,
        "stopped_ns": 3000,
    }
    (health_dir / "hyperliquid.json").write_text(json.dumps(health))
    report = qualify_venue("hyperliquid", str(root), str(health_dir))
    assert not report["qualified"]
    assert "missing_freshness_telemetry" in report["reasons"]


def test_hyperliquid_qualification_allows_bootstrap_backfill_if_live_flow_is_fresh(tmp_path):
    root = tmp_path / "data"
    health_dir = tmp_path / "health"
    health_dir.mkdir(parents=True)
    start_ns, stop_ns = 1_000, 10_000
    for kind in ("book_events", "trades", "derivatives"):
        p = root / "raw" / kind / "venue=hyperliquid" / "symbol=BTCUSDT" / "date=2026-08-15"
        p.mkdir(parents=True)
        (p / "events.jsonl").write_text('{"receive_ts_ns":5000}\n')
    rw = root / "raw_wire" / "venue=hyperliquid" / "date=2026-08-15"
    rw.mkdir(parents=True)
    (rw / "messages.jsonl").write_text('{"receive_ts_ns":5000}\n')
    health = {
        "venue": "hyperliquid",
        "fresh_event_max_lag_ms": 5000.0,
        "messages": 250,
        "events": 250,
        "book_events": 150,
        "trade_events": 50,
        "derivative_events": 50,
        "fresh_events": 220,
        "stale_events": 30,
        "fresh_book_events": 150,
        "fresh_trade_events": 20,
        "fresh_derivative_events": 50,
        "stale_book_events": 0,
        "stale_trade_events": 30,
        "stale_derivative_events": 0,
        "max_book_lag_ms": 500.0,
        "max_trade_lag_ms": 43000.0,
        "max_derivative_lag_ms": 0.0,
        "parse_errors": 0,
        "sequence_gaps": 0,
        "subscription_errors": 0,
        "subscription_acks": 12,
        "reconnects": 0,
        "last_exception": None,
        "idle_ms": 10.0,
        "last_receive_ns": 9000,
        "last_event_ns": 8900,
        "clean_shutdown": True,
        "started_ns": start_ns,
        "stopped_ns": stop_ns,
    }
    (health_dir / "hyperliquid.json").write_text(json.dumps(health))
    report = qualify_venue("hyperliquid", str(root), str(health_dir))
    assert report["qualified"]
    assert report["freshness"]["fresh_by_type"]["trades"] == 20
    assert report["freshness"]["stale_by_type"]["trades"] == 30


def test_state_builder_uses_receive_time_not_market_event_time():
    start = _snapshot(1_000_000_000, 1_100_000_000)
    end = _snapshot(2_000_000_000, 2_100_000_000)
    available = _trade(1_500_000_000, 1_600_000_000, "available")
    delayed = _trade(1_700_000_000, 4_000_000_000, "delayed")
    window = VenueWindow(
        venue="hyperliquid",
        snapshot_start=start,
        snapshot_end=end,
        trades=[available, delayed],
    )
    state = MarketPhysicsStateBuilder().build(
        "BTCUSDT",
        asof_ns=3_000_000_000,
        venue_windows=[window],
    )
    assert state["hyperliquid__trade_count"] == 1.0


def test_state_builder_rejects_snapshot_not_yet_received():
    start = _snapshot(1_000_000_000, 1_100_000_000)
    end = _snapshot(2_000_000_000, 4_000_000_000)
    with pytest.raises(ValueError, match="snapshot_end was not yet received"):
        MarketPhysicsStateBuilder().build(
            "BTCUSDT",
            asof_ns=3_000_000_000,
            venue_windows=[VenueWindow("hyperliquid", start, end)],
        )
