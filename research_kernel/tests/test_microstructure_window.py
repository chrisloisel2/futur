"""microstructure_window: pure book metrics, window bounds, snapshot/manifest without network."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import microstructure_window as W  # noqa: E402
from data_lake.collectors.market_state_schema import validate  # noqa: E402


def test_book_metrics_bands_truncation_and_imbalance():
    bids = [(100.0 - i * 0.01, 10.0) for i in range(20)]     # 20 niveaux -> 19 bps de large seulement
    asks = [(100.01 + i * 0.01, 5.0) for i in range(20)]
    m = W.book_metrics(bids, asks)
    assert m["spread_bps"] == pytest.approx(1.0, rel=1e-3) and m["covered_bps"] == 10
    assert m["10_bid"] == pytest.approx(sum(p * q for p, q in bids if p >= m["mid"] * (1 - 1e-3))) and m["25_bid"] is None and m["50_ask"] is None
    assert m["imbalance_10"] == pytest.approx((m["10_bid"] - m["10_ask"]) / (m["10_bid"] + m["10_ask"]))
    deep = W.book_metrics([(100.0 - i * 0.1, 1.0) for i in range(100)], [(100.1 + i * 0.1, 1.0) for i in range(100)])
    assert deep["covered_bps"] == 50 and deep["50_bid"] is not None


def test_window_bounds_flag_missing_pre_window():
    from datetime import datetime, timezone
    now = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    b = W.window_bounds("2026-09-11T10:30:00+00:00", 1800, 3600, now=now); assert b["pre_window_missing"] is False and b["pre_window_actual_s"] == 1800 and b["end_epoch"] == datetime(2026, 9, 11, 11, 30, tzinfo=timezone.utc).timestamp()
    b2 = W.window_bounds("2026-09-11T09:00:00+00:00", 1800, 3600, now=now); assert b2["pre_window_missing"] is True
    b3 = W.window_bounds(None, 1800, 3600, now=now); assert b3["pre_window_missing"] is True and b3["end_epoch"] == now.timestamp() + 3600


def test_snapshot_from_injected_state_and_manifest(tmp_path):
    w = W.WindowCapture("binance", "XUSDT", "manual", "test", None, 0, 60, root=tmp_path, trigger_id="t_test")
    w.bbo = {"bid": 100.0, "bq": 1.0, "ask": 100.02, "aq": 2.0, "E": 1789000000000, "T": 1789000000000}
    w.book_rest = {"bids": [(100.0 - i * 0.05, 1.0) for i in range(100)], "asks": [(100.02 + i * 0.05, 1.0) for i in range(100)], "E": 1789000000000}
    w.mark = {"mark": 100.01, "index": 100.0, "funding": 0.0001, "E": 1789000000000}; w.oi = {"oi": 12345.0, "time": 1789000000000}
    rec = w.build_snapshot(); validate("market_state_snapshot", rec)
    assert rec["depth_50bps_bid_usd"] is not None and rec["open_interest"] == 12345.0 and rec["depth_source"] == "rest"
    w.snapshots.append(rec); man = w.write_manifest(final=True)
    assert man["no_alpha_test"] is True and man["trigger_type"] == "manual" and (tmp_path / "t_test" / "manifest.json").exists() and man["pre_window_missing"] is True
