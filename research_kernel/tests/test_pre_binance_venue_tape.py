import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from data_lake.collectors import pre_binance_venue_tape as V
from data_lake.collectors import venue_pre_binance_paths as VP


def test_routes_are_documented_for_every_venue_and_only_mexc_is_collected():
    assert set(VP.ROUTES) == set(VP.VENUES) and VP.ROUTES["mexc"]["status"] == "collected_by_this_module"
    assert all(VP.ROUTES[v]["status"] == "route_documented_not_collected" for v in VP.VENUES if v != "mexc")
    assert all(VP.ROUTES[v]["cost"] == "free" for v in VP.VENUES)


def test_placeholders_are_written_once_and_say_not_collected(tmp_path):
    evs = [{"event_id": "e1", "asset": "A", "t0": "2025-05-02T08:30:00+00:00", "venue": "okx", "symbol": "A-USDT", "market": "spot", "listed_ts": None, "lead_days": 2.0}]
    n = V.write_placeholder_manifests(evs, root=tmp_path); assert n == {"written": 1}
    m = json.loads((tmp_path / "okx" / "manifests" / "e1.json").read_text())
    assert m["status"] == "not_collected" and m["route"]["spot"].startswith("GET") and len(m["expected_files"]) == 3 and m["no_post_t0_data"] is True
    assert V.write_placeholder_manifests(evs, root=tmp_path) == {"existing": 1}


def test_reports_exist_and_are_descriptive_only(tmp_path):
    r = V.write_reports(out=tmp_path)
    assert r["mexc_features"] > 100
    for f in ("PRE_BINANCE_VENUE_TAPE_COVERAGE.md", "PRE_BINANCE_VENUE_TAPE_COVERAGE.json", "MEXC_PRE_BINANCE_TAPE_COVERAGE.md", "MEXC_PRE_BINANCE_FEATURES.csv", "MEXC_PRE_BINANCE_FEATURES.json"):
        assert (tmp_path / f).exists(), f
    md = (tmp_path / "MEXC_PRE_BINANCE_TAPE_COVERAGE.md").read_text().lower()
    assert "not a signal" in md and "nothing after t0" in md
    js = json.loads((tmp_path / "MEXC_PRE_BINANCE_FEATURES.json").read_text()); assert js["descriptive_only"] is True and js["no_post_t0_data"] is True
