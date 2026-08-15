import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from market_physics_v3.schema import BookEvent
from market_physics_v3.state_tape_stream import build_streaming_state_tape, iter_merged_book_events

NS = 1_000_000_000
VENUES = ("binance", "bybit", "okx", "hyperliquid")


def _write(root: Path, event: BookEvent):
    path = (
        root / "raw" / "book_events" / ("venue=" + event.venue)
        / ("symbol=" + event.symbol) / "date=2026-08-15" / "events.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(event), sort_keys=True) + "\n")


def _snapshot(venue, receive_ns, mid):
    stream = {
        "binance": "depth_snapshot_rest",
        "bybit": "orderbook.50",
        "okx": "books",
        "hyperliquid": "l2Book",
    }[venue]
    event_ns = receive_ns - 10_000_000
    return [
        BookEvent(venue, "BTCUSDT", event_ns, receive_ns, 1, "snapshot", "bid", mid - 1, 10, source_stream=stream),
        BookEvent(venue, "BTCUSDT", event_ns, receive_ns, 1, "snapshot", "ask", mid + 1, 10, source_stream=stream),
    ]


def test_iter_merged_book_events_is_receive_time_ordered(tmp_path):
    root = tmp_path / "data"
    base = 100 * NS
    for i, venue in enumerate(reversed(VENUES)):
        for event in _snapshot(venue, base + (4 - i) * 10_000_000, 100.0):
            _write(root, event)
    events = list(iter_merged_book_events(str(root), base, base + NS, VENUES, ("BTCUSDT",)))
    receives = [x.receive_ts_ns for x in events]
    assert receives == sorted(receives)
    assert len(events) == 8


def test_streaming_builder_emits_chunked_causal_tape(tmp_path):
    root = tmp_path / "data"
    base = 200 * NS
    for i, venue in enumerate(VENUES):
        for event in _snapshot(venue, base + 100_000_000 + i * 10_000_000, 100.0 + i * 0.1):
            _write(root, event)
    events = iter_merged_book_events(str(root), base, base + 1500_000_000, VENUES, ("BTCUSDT",))
    out = tmp_path / "tape"
    summary = build_streaming_state_tape(
        events,
        base,
        base + 1500_000_000,
        500,
        str(out),
        venues=VENUES,
        symbols=("BTCUSDT",),
        chunk_rows=2,
    )
    parts = sorted(out.glob("part-*.parquet"))
    frame = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    assert summary["rows"] == 3
    assert summary["parts"] == 2
    assert summary["book_events_consumed"] == 8
    assert summary["strict_ready_fraction"] == 1.0
    assert summary["price_ready_fraction"] == 1.0
    assert len(frame) == 3
    assert frame["price_ready"].all()
    assert frame["strict_ready"].all()
    assert (out / "_SUCCESS").exists()
