import json
import subprocess
import sys
from pathlib import Path

import pytest

from market_physics_v3.coverage import audit_feed_status
from market_physics_v3.schema import BookEvent
from market_physics_v3.state_tape import build_state_tape, concurrent_health_window

NS = 1_000_000_000
VENUES = ("binance", "bybit", "okx", "hyperliquid")


def _health(path, venue, start, stop):
    (path / (venue + ".json")).write_text(json.dumps({
        "venue": venue,
        "started_ns": start,
        "stopped_ns": stop,
        "clean_shutdown": True,
    }))


def test_concurrent_health_window_rejects_sequential_smokes(tmp_path):
    health = tmp_path / "health"
    health.mkdir()
    for i, venue in enumerate(VENUES):
        start = (100 + i * 40) * NS
        _health(health, venue, start, start + 30 * NS)
    with pytest.raises(ValueError, match="no concurrent venue window"):
        concurrent_health_window(str(health), VENUES)


def test_concurrent_health_window_accepts_one_parallel_run(tmp_path):
    health = tmp_path / "health"
    health.mkdir()
    base = 100 * NS
    for i, venue in enumerate(VENUES):
        _health(health, venue, base + i * 100_000_000, base + 10 * NS + i * 50_000_000)
    out = concurrent_health_window(str(health), VENUES, max_start_skew_ms=1000)
    assert out["started_ns"] == base + 300_000_000
    assert out["stopped_ns"] == base + 10 * NS
    assert out["start_skew_ms"] == 300.0


def _snap(venue, symbol, receive_ns, mid):
    stream = {
        "binance": "depth_snapshot_rest",
        "bybit": "orderbook.50",
        "okx": "books",
        "hyperliquid": "l2Book",
    }[venue]
    event_ns = receive_ns - 50_000_000
    return [
        BookEvent(venue, symbol, event_ns, receive_ns, 1, "snapshot", "bid", mid - 1, 10, source_stream=stream),
        BookEvent(venue, symbol, event_ns, receive_ns, 1, "snapshot", "ask", mid + 1, 10, source_stream=stream),
    ]


def test_state_tape_builds_strict_four_venue_state():
    start = 100 * NS
    stop = start + 1500_000_000
    events = []
    mids = {"binance": 100.0, "bybit": 100.1, "okx": 99.9, "hyperliquid": 100.2}
    for i, venue in enumerate(VENUES):
        events.extend(_snap(venue, "BTCUSDT", start + 100_000_000 + i * 10_000_000, mids[venue]))
    frame = build_state_tape(events, start, stop, 500, venues=VENUES, symbols=("BTCUSDT",), max_sync_span_ms=1000)
    assert len(frame) == 3
    assert bool(frame.iloc[0]["ready"])
    assert frame.iloc[0]["venues_used"] == "binance,bybit,okx,hyperliquid"
    assert 99.0 < float(frame.iloc[0]["fair_value"]) < 101.0
    assert "binance__microprice" in frame.columns
    assert "okx__dislocation_bps" in frame.columns


def test_state_tape_is_receive_time_causal():
    start = 200 * NS
    stop = start + 1200_000_000
    events = []
    for i, venue in enumerate(VENUES):
        events.extend(_snap(venue, "BTCUSDT", start + 100_000_000 + i, 100.0))
    # Binance changes market-event time early but qbee receives it after the
    # first 500ms grid point. It must not change the first state.
    events.extend([
        BookEvent("binance", "BTCUSDT", start + 200_000_000, start + 800_000_000, 2, "modify", "bid", 99.0, 100, source_stream="depth"),
        BookEvent("binance", "BTCUSDT", start + 200_000_000, start + 800_000_000, 2, "modify", "ask", 101.0, 1, source_stream="depth"),
    ])
    frame = build_state_tape(events, start, stop, 500, venues=VENUES, symbols=("BTCUSDT",), max_sync_span_ms=1000)
    first = frame.iloc[0]
    second = frame.iloc[1]
    assert abs(float(first["binance__queue_imbalance_l1"])) < 1e-12
    assert float(second["binance__queue_imbalance_l1"]) > 0.9


def test_coverage_exposes_book_research_without_faking_tick_readiness():
    statuses = {
        "l2_book_events": "EVENT_LEVEL",
        "tick_trades": "AGGREGATED_ONLY",
        "bbo": "EVENT_LEVEL",
        "binance": "EVENT_LEVEL",
        "bybit": "EVENT_LEVEL",
        "okx": "EVENT_LEVEL",
        "hyperliquid": "EVENT_LEVEL",
    }
    out = audit_feed_status(statuses)
    assert out["ready_for_synchronized_book_research"]
    assert not out["ready_for_p0_market_research"]


def test_phase4_cli_scripts_bootstrap_repo_root():
    root = Path(__file__).resolve().parents[2]
    for rel in [
        "scripts/build_market_physics_state_tape_v3.py",
        "scripts/promote_market_physics_modalities_v3.py",
    ]:
        p = subprocess.run(
            [sys.executable, str(root / rel), "--help"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert p.returncode == 0, "%s failed: %s" % (rel, p.stderr)
