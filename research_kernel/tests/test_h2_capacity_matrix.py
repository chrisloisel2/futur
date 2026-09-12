"""h2_capacity_matrix: summary and reports from synthetic results; capacity only credited when measured."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import h2_capacity_matrix as HM  # noqa: E402
from data_lake.indices import depth_capacity_features as DC  # noqa: E402


def _res(eid, sym, status, res=20, score=50.0):
    w = [{"window_min": m, "capacity_status": status, "capacity_score": score, "effective_spread_bps": 5.0, "bid_depth_20bps_native_usd": 3000.0, "ask_depth_20bps_native_usd": 3000.0,
          "sell_slippage_1000_usd_bps": 5.0, "buy_slippage_1000_usd_bps": 5.0, "capacity_reasons": ["x"]} for m in DC.WINDOWS_MIN]
    return {"event_id": eid, "symbol": sym, "asset": sym[:-4], "t0": "2025-05-02T08:30:00+00:00", "windows": w, "capacity_status_t0": status, "capacity_score_t0": score,
            "capacity_measured": status not in ("NO_DEPTH", "BAD_BOOK"), "depth_resolution_bps": res, "supports_usd": {"100": True, "500": True, "1000": status == "CAPACITY_OK"}, "no_return_computed": True}


def test_summary_counts():
    s = HM.summarise([_res("a", "AUSDT", "CAPACITY_OK"), _res("b", "BUSDT", "DEPTH_TOO_THIN"), _res("c", "CUSDT", "NO_DEPTH", res=None)])
    assert s["n"] == 3 and s["measured"] == 2 and s["status_at_t0"] == {"CAPACITY_OK": 1, "DEPTH_TOO_THIN": 1, "NO_DEPTH": 1}
    assert s["capacity_ok_at_any_window"] == 1 and s["fills_within_observed_book_at_t0"]["1000"] == 1


def test_reports_written_and_capacity_only_credited_when_measured(tmp_path, monkeypatch):
    import data_lake.indices.h2_launch_coverage_matrix as M
    ev = M.load_events()[:2]
    monkeypatch.setattr(M, "load_events", lambda: ev)
    res = [_res(ev[0]["event_id"], ev[0]["symbol"], "CAPACITY_OK"), _res(ev[1]["event_id"], ev[1]["symbol"], "NO_DEPTH", res=None)]
    out = HM.write_reports(res, out=tmp_path)
    for f in ("H2_DEPTH_CAPACITY_FEATURES.md", "H2_DEPTH_CAPACITY_FEATURES.json", "H2_AFTER_CAPACITY_COVERAGE_MATRIX.csv", "H2_AFTER_CAPACITY_COVERAGE_MATRIX.json", "H2_CAPACITY_BLOCKERS.md"):
        assert (tmp_path / f).exists(), f
    rows = json.loads((tmp_path / "H2_AFTER_CAPACITY_COVERAGE_MATRIX.json").read_text())["rows"]
    by = {r["event_id"]: r for r in rows}
    assert by[ev[0]["event_id"]]["capacity_present"] is True and by[ev[1]["event_id"]]["capacity_present"] is False
    md = (tmp_path / "H2_DEPTH_CAPACITY_FEATURES.md").read_text().lower()
    assert "no return computed" in md and "tradeable" not in md and "alpha found" not in md
