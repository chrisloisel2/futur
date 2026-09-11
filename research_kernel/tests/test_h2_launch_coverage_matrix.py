"""Coverage matrix: scoring sums to 100 by design, buckets are fixed, rows carry every mandatory column, no network needed."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import h2_launch_coverage_matrix as M  # noqa: E402

MANDATORY = ["symbol", "event_id", "announcement_ts", "announcement_body_present", "body_trading_time_present", "onboardDate_present", "pending_trading_ts_present", "trading_ts_present", "first_orderbook_ts_present",
             "first_trade_ts_present", "first_mark_ts_present", "first_index_ts_present", "first_oi_ts_present", "l2_t0_t6h_present", "trades_t0_t6h_present", "funding_present", "spot_existed_before",
             "other_venue_existed_before", "actual_fee_present", "capacity_present", "coverage_score", "missing_p0_fields", "backfill_source", "action"]


def _ev(sym="AAAUSDT", onboard=True):
    return {"symbol": sym, "event_id": "e_" + sym, "asset": sym[:-4], "launch_ts": "2025-05-02T08:30:00+00:00", "announcement_ts": "2025-05-02T06:00:00+00:00", "onboard_ts": "2025-05-02T08:30:00+00:00" if onboard else None, "vision_first_bar_ts": "2025-05-02T08:30:00+00:00", "launch_ts_source": "vision"}


def test_scoring_and_columns_without_network(tmp_path):
    ev = [_ev("AAAUSDT"), _ev("BBBUSDT", onboard=False)]
    vis = {f"{ds}|AAAUSDT|2025-05-02": "ok" for ds in M.DATASETS + ("fundingRate",)}; vis.update({f"{ds}|BBBUSDT|2025-05-02": ("404" if ds in ("bookDepth", "indexPriceKlines", "premiumIndexKlines") else "ok") for ds in M.DATASETS + ("fundingRate",)})
    rows = M.build_rows(ev, vis, {"AAA": "bybit:futures_listing@2025-04-30T10:00", "BBB": None}, ms=tmp_path / "none")
    for r in rows:
        assert all(c in r for c in MANDATORY) and r["bucket"] == M.bucket(r["coverage_score"])
    a, b = rows
    assert a["coverage_score"] == 5 + 5 + 5 + 5 + 10 == 30 and a["bucket"] == "unusable" and a["action"] == "collect"
    assert a["coverage_score_after_free"] == 100 - 5 - 3 == 92 and a["bucket_after_free"] == "clean" and a["backfill_source"] == "free" and a["coverage_score_after_free_and_key"] == 95
    assert b["coverage_score"] == 15 and b["backfill_source"] in ("free", "paid") and "bookDepth" in b["vision_missing"] and b["coverage_score_after_free"] < a["coverage_score_after_free"]
    assert "first_index_ts_present" in a["missing_p0_fields"] and "body_trading_time_present" in a["missing_p0_fields"]
    s = M.write(rows, tmp_path / "out"); assert s["n_events"] == 2 and (tmp_path / "out" / "H2_LAUNCH_COVERAGE_MATRIX.csv").exists()
    js = json.loads((tmp_path / "out" / "H2_LAUNCH_COVERAGE_MATRIX.json").read_text()); assert js["no_alpha_test"] is True and js["scoring"]["group_max"] == {"timestamps": 25, "market_state": 25, "execution": 20, "cross_venue": 15, "body": 15}


def test_bucket_edges():
    assert M.bucket(39) == "unusable" and M.bucket(40) == "partial" and M.bucket(69) == "partial" and M.bucket(70) == "near_usable" and M.bucket(89) == "near_usable" and M.bucket(90) == "clean"


def test_probe_uses_cache_without_network(tmp_path):
    cache = tmp_path / "c.json"; cache.write_text(json.dumps({"aggTrades|AAAUSDT|2025-05-02": "ok"}))
    v = M.probe_vision([_ev("AAAUSDT")], cache_path=cache, network=False); assert v["aggTrades|AAAUSDT|2025-05-02"] == "ok" and len(v) == 1
