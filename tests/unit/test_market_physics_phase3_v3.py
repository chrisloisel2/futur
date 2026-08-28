import json

from market_physics_v3.collectors.normalize import BookDeltaState, parse_binance
from market_physics_v3.cross_venue import VenueQuote, fair_value
from market_physics_v3.modality import audit_modality_matrix
from market_physics_v3.orderbook import LocalOrderBook
from market_physics_v3.schema import BookEvent
from market_physics_v3.synchronized import SynchronizedBookEngine

NS = 1_000_000_000
R = 2_000_000_000_000_000_000


def _snapshot_rows(venue, symbol, receive_ns, source_stream="book", sequence=1, mid=100.0, order_count=None):
    event_ns = receive_ns - 100_000_000
    return [
        BookEvent(venue, symbol, event_ns, receive_ns, sequence, "snapshot", "bid", mid - 1, 10, order_count=order_count, source_stream=source_stream),
        BookEvent(venue, symbol, event_ns, receive_ns, sequence, "snapshot", "ask", mid + 1, 10, order_count=order_count, source_stream=source_stream),
    ]


def test_binance_normalizer_preserves_stream_and_update_range():
    s = BookDeltaState()
    rows = parse_binance({
        "e": "depthUpdate", "E": 1000, "T": 1000, "s": "BTCUSDT",
        "U": 10, "u": 12, "pu": 9,
        "b": [["100", "2"]], "a": [["101", "3"]],
    }, R, s)
    assert rows[0].source_stream == "depth"
    assert rows[0].first_sequence_id == 10
    assert rows[0].previous_sequence_id == 9
    bbo = parse_binance({
        "e": "bookTicker", "E": 1001, "s": "BTCUSDT", "u": 13,
        "b": "100", "B": "2", "a": "101", "A": "3",
    }, R, s)
    assert {x.source_stream for x in bbo} == {"bookTicker"}


def test_binance_aggtrade_is_explicitly_aggregate_not_individual_tick():
    s = BookDeltaState()
    event = parse_binance({
        "e":"aggTrade","E":1000,"T":1000,"s":"BTCUSDT","a":7,
        "p":"100","q":"2","m":False,
    }, R, s)[0]
    assert event.source_stream == "aggTrade"
    assert event.granularity == "aggregate"


def test_local_book_refuses_unbootstrapped_deep_delta_but_keeps_bbo():
    s = BookDeltaState()
    deep = parse_binance({
        "e": "depthUpdate", "E": 1000, "s": "BTCUSDT", "U": 1, "u": 2,
        "b": [["99", "3"]], "a": [["102", "4"]],
    }, R, s)
    bbo = parse_binance({
        "e": "bookTicker", "E": 1001, "s": "BTCUSDT", "u": 3,
        "b": "100", "B": "2", "a": "101", "A": "3",
    }, R + 1_000_000, s)
    book = LocalOrderBook("binance", "BTCUSDT")
    book.apply_many(deep + bbo)
    ready = book.readiness()
    assert not ready.deep_ready
    assert ready.bbo_ready
    assert ready.ignored_unbootstrapped == len(deep)
    assert book.snapshot(prefer_deep=False).mid == 100.5


def test_deep_snapshot_bootstraps_and_bbo_never_wipes_it():
    book = LocalOrderBook("bybit", "BTCUSDT")
    book.apply_many(_snapshot_rows("bybit", "BTCUSDT", 10 * NS, source_stream="orderbook.50"))
    assert book.readiness().deep_ready
    before = book.readiness().deep_levels
    book.apply(BookEvent("bybit", "BTCUSDT", 10 * NS, 10 * NS + 1, 2, "snapshot", "bid", 99.5, 1, source_stream="bbo"))
    book.apply(BookEvent("bybit", "BTCUSDT", 10 * NS, 10 * NS + 1, 2, "snapshot", "ask", 100.5, 1, source_stream="bbo"))
    assert book.readiness().deep_levels == before
    assert book.readiness().bbo_ready


def test_order_count_survives_reconstruction_and_fragmentation():
    book = LocalOrderBook("hyperliquid", "BTCUSDT")
    rows = _snapshot_rows("hyperliquid", "BTCUSDT", 10 * NS, source_stream="l2Book", order_count=5)
    book.apply_many(rows)
    snap = book.snapshot()
    assert snap.best_bid.order_count == 5
    f = book.fragmentation_features()
    assert f["bid_order_count_l10"] == 5.0
    assert f["ask_order_count_l10"] == 5.0
    assert f["bid_qty_per_order_l10"] == 2.0


def test_fair_value_penalizes_transport_lag_separately_from_receive_age():
    asof = 20 * NS
    fresh = VenueQuote("fresh", asof - 100_000_000, 100, 1, 1_000_000, receive_ts_ns=asof)
    delayed = VenueQuote("delayed", asof - 4 * NS, 110, 1, 1_000_000, receive_ts_ns=asof)
    out = fair_value([fresh, delayed], asof, half_life_ms=1000, transport_half_life_ms=500)
    assert out["weights"]["fresh"] > out["weights"]["delayed"]
    assert out["transport_lag_ms"]["delayed"] > 3000
    assert out["fair_value"] < 102


def test_synchronized_engine_fails_closed_when_one_deep_book_missing():
    engine = SynchronizedBookEngine()
    receive = 20 * NS
    for venue in ["bybit", "okx", "hyperliquid"]:
        stream = "books" if venue == "okx" else ("l2Book" if venue == "hyperliquid" else "orderbook.50")
        for row in _snapshot_rows(venue, "BTCUSDT", receive, source_stream=stream):
            engine.ingest(row)
    for row in [
        BookEvent("binance", "BTCUSDT", receive - 1, receive, 1, "snapshot", "bid", 99, 10, source_stream="bookTicker"),
        BookEvent("binance", "BTCUSDT", receive - 1, receive, 1, "snapshot", "ask", 101, 10, source_stream="bookTicker"),
    ]:
        engine.ingest(row)
    state = engine.state("BTCUSDT", receive + 1, max_sync_span_ms=10_000)
    assert not state.ready
    assert "binance" in state.venues_missing
    assert "binance:deep_not_ready" in state.reasons


def test_synchronized_engine_rejects_transport_stale_venue():
    engine = SynchronizedBookEngine()
    asof = 30 * NS
    for row in _snapshot_rows("bybit", "BTCUSDT", asof, source_stream="orderbook.50"):
        engine.ingest(row)
    old_event = asof - 10 * NS
    for side, px in [("bid", 99), ("ask", 101)]:
        engine.ingest(BookEvent("okx", "BTCUSDT", old_event, asof, 1, "snapshot", side, px, 10, source_stream="books"))
    state = engine.state(
        "BTCUSDT", asof + 1,
        required_venues=("bybit", "okx"),
        max_transport_lag_ms=5000,
        max_sync_span_ms=1000,
    )
    assert not state.ready
    assert "okx:transport_stale" in state.reasons


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in rows))


def test_modality_matrix_identifies_binance_snapshot_and_tick_blockers(tmp_path):
    root = tmp_path / "data"
    health = tmp_path / "health"
    health.mkdir()
    start = 1000 * NS
    stop = start + NS
    (health / "binance.json").write_text(json.dumps({"started_ns": start, "stopped_ns": stop}))
    base = {"venue":"binance","symbol":"BTCUSDT","event_ts_ns":start,"receive_ts_ns":start+1,"sequence_id":1,"side":"bid","price":100,"qty":1,"order_count":None,"first_sequence_id":1,"previous_sequence_id":None,"_record_type":"BookEvent"}
    deep = dict(base); deep.update({"event_type":"update","source_stream":"depth"})
    bbo_bid = dict(base); bbo_bid.update({"event_type":"snapshot","source_stream":"bookTicker"})
    bbo_ask = dict(bbo_bid); bbo_ask.update({"side":"ask","price":101})
    _write_jsonl(root / "raw/book_events/venue=binance/symbol=BTCUSDT/date=2026-08-15/events.jsonl", [deep,bbo_bid,bbo_ask])
    trade = {"venue":"binance","symbol":"BTCUSDT","event_ts_ns":start,"receive_ts_ns":start+1,"trade_id":"1","price":100,"qty":1,"aggressor":"buy","source_stream":"aggTrade","granularity":"aggregate","_record_type":"TradeEvent"}
    _write_jsonl(root / "raw/trades/venue=binance/symbol=BTCUSDT/date=2026-08-15/events.jsonl", [trade])
    report = audit_modality_matrix(str(root), str(health), venues=("binance",), symbols=("BTCUSDT",))
    cell = report["cells"]["binance:BTCUSDT"]
    assert not cell["book"]["deep_ready"]
    assert cell["book"]["bbo_ready"]
    assert cell["trades"]["event_stream_ready"]
    assert not cell["trades"]["tick_ready"]
    assert "binance:BTCUSDT:deep_book" in report["summary"]["blocking_cells"]
    assert "binance:BTCUSDT:individual_trades" in report["summary"]["tick_trade_blocking_cells"]


def test_modality_matrix_accepts_deep_snapshot_and_derives_bbo(tmp_path):
    root = tmp_path / "data"
    health = tmp_path / "health"
    health.mkdir()
    start = 2000 * NS
    stop = start + NS
    (health / "bybit.json").write_text(json.dumps({"started_ns": start, "stopped_ns": stop}))
    rows = []
    for side, px in [("bid",100),("ask",101)]:
        rows.append({"venue":"bybit","symbol":"BTCUSDT","event_ts_ns":start,"receive_ts_ns":start+1,"sequence_id":1,"event_type":"snapshot","side":side,"price":px,"qty":1,"source_stream":"orderbook.50","_record_type":"BookEvent"})
    _write_jsonl(root / "raw/book_events/venue=bybit/symbol=BTCUSDT/date=2026-08-15/events.jsonl", rows)
    _write_jsonl(root / "raw/trades/venue=bybit/symbol=BTCUSDT/date=2026-08-15/events.jsonl", [{"venue":"bybit","symbol":"BTCUSDT","event_ts_ns":start,"receive_ts_ns":start+1,"trade_id":"1","price":100,"qty":1,"aggressor":"buy","source_stream":"publicTrade","granularity":"individual"}])
    report = audit_modality_matrix(str(root), str(health), venues=("bybit",), symbols=("BTCUSDT",))
    cell = report["cells"]["bybit:BTCUSDT"]
    assert cell["book"]["deep_ready"]
    assert cell["book"]["bbo_ready"]
    assert cell["book"]["bbo_mode"] == "derived_from_deep"
    assert cell["trades"]["tick_ready"]
    assert report["summary"]["ready_for_synchronized_books"]


def test_modality_cli_bootstraps_repo_root():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    p = subprocess.run(
        [sys.executable, str(root / "scripts/audit_market_physics_modalities_v3.py"), "--help"],
        cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert p.returncode == 0, p.stderr
