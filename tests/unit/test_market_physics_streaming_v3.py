import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from market_physics_v3.schema import BookEvent
from market_physics_v3.state_tape_stream import build_streaming_state_tape, iter_merged_book_events

NS = 1_000_000_000
VENUES = ("binance", "bybit", "okx", "hyperliquid")


def _write_date(root: Path, event: BookEvent, date: str):
    path = (
        root / "raw" / "book_events" / ("venue=" + event.venue)
        / ("symbol=" + event.symbol) / ("date=" + date) / "events.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(event), sort_keys=True) + "\n")


def _write(root: Path, event: BookEvent):
    _write_date(root, event, "2026-08-15")


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


def test_iter_merged_book_events_repairs_physical_receive_time_inversion(tmp_path):
    root = tmp_path / "data"
    base = 120 * NS
    late = BookEvent(
        "binance", "BTCUSDT", base + 290_000_000, base + 300_000_000,
        2, "modify", "bid", 99.0, 12, source_stream="depth",
    )
    early = BookEvent(
        "binance", "BTCUSDT", base + 90_000_000, base + 100_000_000,
        1, "modify", "bid", 98.0, 11, source_stream="depth",
    )
    _write(root, late)
    _write(root, early)

    events = list(iter_merged_book_events(
        str(root), base, base + NS, ("binance",), ("BTCUSDT",)
    ))
    assert [x.receive_ts_ns for x in events] == [
        base + 100_000_000,
        base + 300_000_000,
    ]


def test_iter_merges_event_date_files_by_receive_time(tmp_path):
    root = tmp_path / "data"
    base = 130 * NS
    # Event-date partitioning can differ from receive-time ordering around UTC
    # midnight. The earlier date file may contain a later-received delayed row.
    received_later_but_old_event_date = BookEvent(
        "hyperliquid", "BTCUSDT", base + 10_000_000, base + 400_000_000,
        2, "modify", "bid", 99.0, 12, source_stream="l2Book",
    )
    received_earlier_new_event_date = BookEvent(
        "hyperliquid", "BTCUSDT", base + 190_000_000, base + 200_000_000,
        1, "modify", "bid", 98.0, 11, source_stream="l2Book",
    )
    _write_date(root, received_later_but_old_event_date, "2026-08-15")
    _write_date(root, received_earlier_new_event_date, "2026-08-16")

    events = list(iter_merged_book_events(
        str(root), base, base + NS, ("hyperliquid",), ("BTCUSDT",)
    ))
    assert [x.receive_ts_ns for x in events] == [
        base + 200_000_000,
        base + 400_000_000,
    ]


def test_iter_does_not_stop_at_out_of_window_row_when_partition_is_disordered(tmp_path):
    root = tmp_path / "data"
    base = 140 * NS
    outside = BookEvent(
        "okx", "BTCUSDT", base + 2 * NS, base + 2 * NS,
        2, "modify", "bid", 99.0, 12, source_stream="books",
    )
    inside = BookEvent(
        "okx", "BTCUSDT", base + 90_000_000, base + 100_000_000,
        1, "modify", "bid", 98.0, 11, source_stream="books",
    )
    _write(root, outside)
    _write(root, inside)

    events = list(iter_merged_book_events(
        str(root), base, base + NS, ("okx",), ("BTCUSDT",)
    ))
    assert len(events) == 1
    assert events[0].receive_ts_ns == base + 100_000_000


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
