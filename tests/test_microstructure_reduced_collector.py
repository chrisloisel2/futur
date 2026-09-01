"""
tests/test_microstructure_reduced_collector.py
─────────────────────────────────────────────────────────────────────────────
Regression tests for scripts/collect_microstructure_reduced.py — no network,
no real filesystem disk-usage calls (both shutil.disk_usage and the
directory-size walk are mocked). Covers:
  1. The disk-budget-check logic itself (MIN_FREE_DISK_GB floor and
     DISK_BUDGET_GB ceiling), per the mission's explicit requirement that
     this must be testable "without needing to actually run a live
     collector."
  2. The GzipJsonlSink's refusal to open a NEW file once the guard reports a
     breach (never deletes existing data, never corrupts an in-flight file).
  3. Pure per-venue BBO/trade parsers (sanity, not exhaustive).
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

_spec = importlib.util.spec_from_file_location(
    "msr", Path(__file__).parents[1] / "scripts" / "collect_microstructure_reduced.py")
msr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(msr)


def _disk_usage(free_gb):
    def _fn(path):
        return SimpleNamespace(total=1_000 * 1024 ** 3, used=0, free=free_gb * 1024 ** 3)
    return _fn


def _dir_size(used_gb):
    def _fn(path):
        return int(used_gb * 1024 ** 3)
    return _fn


# ── 1. disk_budget_status ────────────────────────────────────────────────

def test_disk_budget_ok_when_both_thresholds_clear():
    st = msr.disk_budget_status(
        Path("/fake/out"), min_free_gb=20.0, budget_gb=12.0,
        disk_usage_fn=_disk_usage(32.0), dir_size_fn=_dir_size(1.0),
    )
    assert st["ok"] is True
    assert st["reason"] is None
    assert st["free_gb"] == 32.0
    assert st["used_gb"] == 1.0


def test_disk_budget_breaches_on_low_free_space():
    """Mock free space below MIN_FREE_DISK_GB -> must signal stop."""
    st = msr.disk_budget_status(
        Path("/fake/out"), min_free_gb=20.0, budget_gb=12.0,
        disk_usage_fn=_disk_usage(19.9), dir_size_fn=_dir_size(1.0),
    )
    assert st["ok"] is False
    assert "MIN_FREE_DISK_GB" in st["reason"]


def test_disk_budget_breaches_on_collector_own_ceiling():
    """Free space is fine, but this collector's own cumulative footprint
    has reached DISK_BUDGET_GB -> must also signal stop."""
    st = msr.disk_budget_status(
        Path("/fake/out"), min_free_gb=20.0, budget_gb=12.0,
        disk_usage_fn=_disk_usage(100.0), dir_size_fn=_dir_size(12.5),
    )
    assert st["ok"] is False
    assert "DISK_BUDGET_GB" in st["reason"]


def test_disk_budget_free_space_floor_takes_priority_message():
    """Both breached at once: free-space floor is a whole-machine safety
    concern, must still be reported (not silently overridden)."""
    st = msr.disk_budget_status(
        Path("/fake/out"), min_free_gb=20.0, budget_gb=12.0,
        disk_usage_fn=_disk_usage(5.0), dir_size_fn=_dir_size(13.0),
    )
    assert st["ok"] is False
    assert "MIN_FREE_DISK_GB" in st["reason"]


def test_disk_guard_alerts_once_per_breach_transition(tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr(msr, "alert", lambda msg: alerts.append(msg))
    guard = msr.DiskGuard(
        tmp_path, min_free_gb=20.0, budget_gb=12.0,
        disk_usage_fn=_disk_usage(5.0), dir_size_fn=_dir_size(1.0),
    )
    assert guard.ok(force=True) is False
    assert guard.ok(force=True) is False
    assert len(alerts) == 1, "must not spam an ALERT on every single check"

    guard2 = msr.DiskGuard(
        tmp_path, min_free_gb=20.0, budget_gb=12.0,
        disk_usage_fn=_disk_usage(32.0), dir_size_fn=_dir_size(1.0),
    )
    assert guard2.ok(force=True) is True
    assert alerts == ["MIN_FREE_DISK_GB breach: 5.00GB free on disk < floor 20.00GB "
                       "(whole-machine floor, not just this collector's budget)"]


# ── 2. GzipJsonlSink refuses new files once budget is breached ──────────

def test_sink_refuses_new_file_once_budget_breached(tmp_path):
    guard = msr.DiskGuard(
        tmp_path, min_free_gb=20.0, budget_gb=12.0,
        disk_usage_fn=_disk_usage(5.0), dir_size_fn=_dir_size(1.0),
    )
    sink = msr.GzipJsonlSink(guard)
    path = tmp_path / "events-00.jsonl.gz"
    try:
        sink.append(path, {"a": 1})
        raised = False
    except msr.DiskBudgetExceeded:
        raised = True
    assert raised is True
    assert not path.exists(), "no partial/corrupt file should be created on a refused open"


def test_sink_writes_and_closes_readable_gzip(tmp_path):
    import gzip
    import json as _json

    guard = msr.DiskGuard(
        tmp_path, min_free_gb=20.0, budget_gb=12.0,
        disk_usage_fn=_disk_usage(32.0), dir_size_fn=_dir_size(1.0),
    )
    sink = msr.GzipJsonlSink(guard)
    path = tmp_path / "events-00.jsonl.gz"
    sink.append(path, {"a": 1})
    sink.append(path, {"a": 2})
    sink.close_all()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rows = [_json.loads(line) for line in fh]
    assert rows == [{"a": 1}, {"a": 2}]


# ── 3. pure parsers (sanity) ─────────────────────────────────────────────

def test_parse_binance_bookticker():
    msg = {"e": "bookTicker", "s": "BTCUSDT", "T": 1784375301000,
           "b": "64000.1", "B": "1.5", "a": "64000.2", "A": "2.0"}
    rows = msr.parse_binance_bookticker(msg, receive_ns=1784375301500000000)
    assert len(rows) == 1
    r = rows[0]
    assert r["venue"] == "binance" and r["symbol"] == "BTCUSDT"
    assert r["bid_price"] == 64000.1 and r["ask_qty"] == 2.0
    assert r["source_stream"] == "bookTicker"
    assert msr.parse_binance_bookticker({"e": "depthUpdate"}, 0) == []


def test_parse_binance_aggtrade_side_from_maker_flag():
    msg = {"e": "aggTrade", "s": "ETHUSDT", "T": 1784375301000,
           "a": 12345, "p": "3000.5", "q": "0.4", "m": True}
    rows = msr.parse_binance_aggtrade(msg, receive_ns=1784375301500000000)
    assert rows[0]["side"] == "sell"   # buyer is maker -> aggressor sold
    msg["m"] = False
    assert msr.parse_binance_aggtrade(msg, 0)[0]["side"] == "buy"


def test_parse_okx_bbo_and_trades():
    bbo_msg = {"arg": {"channel": "bbo-tbt", "instId": "BTC-USDT-SWAP"},
               "data": [{"asks": [["64001.0", "1.2", "0", "3"]],
                         "bids": [["64000.0", "0.8", "0", "2"]],
                         "ts": "1784375301000"}]}
    bbo_rows, trade_rows = msr.parse_okx(bbo_msg, receive_ns=0)
    assert trade_rows == []
    assert bbo_rows[0]["symbol"] == "BTCUSDT"
    assert bbo_rows[0]["bid_price"] == 64000.0 and bbo_rows[0]["ask_price"] == 64001.0

    trade_msg = {"arg": {"channel": "trades", "instId": "SOL-USDT-SWAP"},
                 "data": [{"tradeId": "9", "px": "150.5", "sz": "10", "side": "sell",
                           "ts": "1784375301000"}]}
    bbo_rows2, trade_rows2 = msr.parse_okx(trade_msg, receive_ns=0)
    assert bbo_rows2 == []
    assert trade_rows2[0]["symbol"] == "SOLUSDT" and trade_rows2[0]["side"] == "sell"

    # full-depth channel must never be turned into rows even if a message
    # arrives on it (defense in depth -- this collector never subscribes to
    # `books`, but the parser itself should also refuse to normalize it)
    depth_msg = {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "data": [{}]}
    assert msr.parse_okx(depth_msg, receive_ns=0) == ([], [])


def test_parse_hyperliquid_bbo_and_trades():
    bbo_msg = {"channel": "bbo", "data": {"coin": "BTC", "time": 1784375301000,
               "bbo": [{"px": "64000.0", "sz": "1.1"}, {"px": "64001.0", "sz": "0.9"}]}}
    bbo_rows, trade_rows = msr.parse_hyperliquid(bbo_msg, receive_ns=0)
    assert trade_rows == []
    assert bbo_rows[0]["symbol"] == "BTCUSDT" and bbo_rows[0]["ask_qty"] == 0.9

    trade_msg = {"channel": "trades", "data": [{"coin": "ETH", "side": "B", "px": "3000.0",
                 "sz": "0.5", "time": 1784375301000, "tid": 42}]}
    bbo_rows2, trade_rows2 = msr.parse_hyperliquid(trade_msg, receive_ns=0)
    assert bbo_rows2 == []
    assert trade_rows2[0]["symbol"] == "ETHUSDT" and trade_rows2[0]["side"] == "buy"

    # full l2Book snapshot must never be turned into rows
    l2_msg = {"channel": "l2Book", "data": {"coin": "BTC", "time": 0, "levels": [[], []]}}
    assert msr.parse_hyperliquid(l2_msg, receive_ns=0) == ([], [])


def test_partition_path_hourly_rotation():
    p1 = msr.partition_path(Path("/data"), "bbo", "binance", "BTCUSDT", event_ts_ns=1_735_689_600_000_000_000)
    p2 = msr.partition_path(Path("/data"), "bbo", "binance", "BTCUSDT", event_ts_ns=1_735_693_200_000_000_000)
    assert p1 != p2, "partition path must change across an hour boundary"
    assert p1.name.startswith("events-") and p1.name.endswith(".jsonl.gz")
