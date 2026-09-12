"""book_depth_parser: both archive generations, malformed/empty books, bounds never interpolated downwards."""
import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import book_depth_parser as P  # noqa: E402


def _zip(p: Path, rows):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("d.csv", "timestamp,percentage,depth,notional\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n")


def _book(ts, bid, ask):
    """bid/ask : dict bande_bps -> notional cumule."""
    rows = []
    for b, v in bid.items():
        rows.append([ts, "-%.2f" % (b / 100), 1, v])
    for b, v in ask.items():
        rows.append([ts, "%.2f" % (b / 100), 1, v])
    return rows


def test_parse_ts_is_utc_and_accepts_epochs():
    assert P.parse_ts("2026-01-22 10:45:34") == 1769078734000
    assert P.parse_ts("1769078734000") == 1769078734000 and P.parse_ts("1769078734000000") == 1769078734000
    assert P.parse_ts("timestamp") is None and P.parse_ts("") is None


def test_fine_and_coarse_generations_are_both_valid(tmp_path):
    fine = {20: 100.0, 100: 500.0, 200: 900.0, 300: 1200.0, 400: 1400.0, 500: 1500.0}
    coarse = {100: 500.0, 200: 900.0, 300: 1200.0, 400: 1400.0, 500: 1500.0}
    p = tmp_path / "a.zip"; _zip(p, _book("2026-01-22 10:45:34", fine, fine) + _book("2026-01-22 10:46:04", coarse, coarse))
    s = P.load(p)
    assert len(s) == 2 and all(x["ok"] for x in s) and s[0]["resolution_bps"] == 20 and s[1]["resolution_bps"] == 100


def test_malformed_books_are_flagged_not_dropped(tmp_path):
    not_cumulative = {100: 900.0, 200: 500.0, 300: 1200.0, 400: 1400.0, 500: 1500.0}
    negative = {100: -1.0, 200: 500.0, 300: 1200.0, 400: 1400.0, 500: 1500.0}
    missing = {100: 1.0, 200: 2.0}
    p = tmp_path / "b.zip"
    _zip(p, _book("2026-01-22 10:45:34", not_cumulative, not_cumulative) + _book("2026-01-22 10:46:04", negative, negative) + _book("2026-01-22 10:47:04", missing, missing))
    s = P.load(p)
    assert len(s) == 3 and not any(x["ok"] for x in s)
    assert "not cumulative" in s[0]["error"] and "negative" in s[1]["error"] and "bands" in s[2]["error"]


def test_empty_archive_and_bad_zip(tmp_path):
    e = tmp_path / "e.zip"; _zip(e, [])
    assert P.load(e) == []
    bad = tmp_path / "x.zip"; bad.write_bytes(b"nope")
    with pytest.raises(P.BookError):
        P.load(bad)


def test_depth_within_never_interpolates_below_resolution():
    fine = {20: 100.0, 100: 500.0, 200: 900.0, 300: 1200.0, 400: 1400.0, 500: 1500.0}
    assert P.depth_within(fine, 10) == (None, None, False)                    # sous la resolution
    assert P.depth_within(fine, 20) == (100.0, 20, False)
    assert P.depth_within(fine, 25) == (100.0, 20, True)                      # borne inferieure, bande 20
    assert P.depth_within(fine, 50) == (100.0, 20, True)
    assert P.depth_within(fine, 150) == (500.0, 100, True)
    coarse = {100: 500.0, 200: 900.0, 300: 1200.0, 400: 1400.0, 500: 1500.0}
    assert P.depth_within(coarse, 25) == (None, None, False)                  # 100 bps est la plus fine ici


def test_slippage_bounds_and_overflow():
    side = {20: 100.0, 100: 500.0, 200: 900.0, 300: 1200.0, 400: 1400.0, 500: 1500.0}
    s = P.slippage_for_notional(side, 50.0)
    assert s["fills"] and s["ub_bps"] == 20.0 and 0 < s["bps"] < 20
    s = P.slippage_for_notional(side, 300.0)
    assert s["ub_bps"] == 100.0 and 20 < s["bps"] < 100 and abs(s["bps"] - 60.0) < 1e-6     # lineaire : 100 + (300-100)/(500-100) * 80
    assert P.slippage_for_notional(side, 10_000.0) == {"ub_bps": None, "bps": None, "fills": False}
    coarse = {100: 500.0, 200: 900.0, 300: 1200.0, 400: 1400.0, 500: 1500.0}
    assert P.slippage_for_notional(coarse, 50.0)["ub_bps"] == 100.0


def test_nearest_snapshot_and_imbalance():
    snaps = [{"ts_ms": 1000, "bid": {100: 10.0}, "ask": {100: 30.0}, "ok": True}, {"ts_ms": 400_000, "bid": {}, "ask": {}, "ok": True}]
    assert P.nearest_at_or_after(snaps, 500)["ts_ms"] == 1000
    assert P.nearest_at_or_after(snaps, 2000, tolerance_ms=1000) is None          # trop loin
    assert P.count_between(snaps, 0, 2000) == 1
    assert P.imbalance(snaps[0], 100) == -0.5 and P.imbalance(snaps[0], 20) is None
