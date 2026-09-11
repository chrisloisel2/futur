"""cross_venue_lifecycle: every client returns the same shape, a venue failure is a fact not a crash,
and the reports never turn a metadata finding into an alpha claim."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import cross_venue_lifecycle as C  # noqa: E402
from data_lake.collectors import venue_clients as VC  # noqa: E402
from data_lake.collectors import venue_precedence as VP  # noqa: E402


def test_every_client_is_registered_and_declares_whether_it_has_listing_times():
    reg = VC.registry()
    assert set(reg) == {"okx", "bybit", "gate", "mexc", "kucoin"}
    for name, mod in reg.items():
        assert hasattr(mod, "fetch_instruments") and hasattr(mod, "HAS_NATIVE_LISTING_TIME"), name
    assert reg["gate"].HAS_NATIVE_LISTING_TIME is False          # Gate ne publie aucune date : dit, pas cache


def test_instrument_shape_is_uniform():
    i = VC.instrument("okx", "spot", "ABC-USDT", "abc", "usdt", "live", "2025-01-01T00:00:00+00:00", "listTime", {"x": 1})
    assert set(i) == set(VC.INSTRUMENT_FIELDS) and i["base"] == "ABC" and i["quote"] == "USDT" and len(i["raw_hash"]) == 64


def test_timestamp_parsing_rejects_nonsense():
    assert VC.ms_to_iso(1585555200000) == "2020-03-30T08:00:00+00:00"
    assert VC.ms_to_iso(1585555200000000) == "2020-03-30T08:00:00+00:00"     # microsecondes
    assert VC.ms_to_iso(0) is None and VC.ms_to_iso("") is None and VC.ms_to_iso(None) is None
    assert VC.ms_to_iso(-1) is None and VC.ms_to_iso(1) is None              # une epoque a 1 ms n'est pas une date de cotation


def test_http_failure_is_raised_as_a_venue_error_not_an_arbitrary_exception(monkeypatch):
    import http.client
    monkeypatch.setattr(VC.time, "sleep", lambda s: None)
    monkeypatch.setattr(VC, "urlopen", lambda *a, **k: (_ for _ in ()).throw(http.client.IncompleteRead(b"x")))
    with pytest.raises(VC.VenueError):
        VC.http_json("https://x")


def test_a_failing_venue_does_not_stop_the_collection(tmp_path, monkeypatch):
    class Good:
        HAS_NATIVE_LISTING_TIME = True
        @staticmethod
        def fetch_instruments():
            return [VC.instrument("okx", "spot", "A-USDT", "A", "USDT", "live", "2025-01-01T00:00:00+00:00", "listTime", {})]
    class Bad:
        HAS_NATIVE_LISTING_TIME = False
        @staticmethod
        def fetch_instruments():
            raise VC.VenueError("down")
    monkeypatch.setattr(C, "registry", lambda: {"okx": Good, "gate": Bad})
    d = C.collect(store=tmp_path)
    assert d["venues"]["okx"]["status"] == "ok" and d["venues"]["gate"]["status"] == "error" and len(d["instruments"]) == 1
    assert (tmp_path / "instruments.json").exists()


def test_reports_are_written_and_stay_descriptive(tmp_path, monkeypatch):
    dec = [VP.decide("ABC", "2025-05-02T08:30:00+00:00", VP.index_by_base([{"venue": "mexc", "market_type": "spot", "symbol": "ABCUSDT", "base": "ABC",
            "quote": "USDT", "status": "1", "first_listed_ts": "2025-04-01T00:00:00+00:00", "first_listed_source": "firstOpenTime", "raw_hash": "h"}]))]
    res = {"resolved_at_utc": "2026-09-12T00:00:00+00:00", "venues": {"mexc": {"status": "ok", "instruments": 1, "with_listing_date": 1, "by_market": {"spot": 1}, "native_listing_time": True}},
           "n_instruments": 1, "resolved_by_first_candle": 0, "decisions": dec, "summary": VP.summarise(dec), "no_alpha_test": True, "no_price_join": True}
    monkeypatch.setattr(C, "OUT", tmp_path)
    out = C.write_reports(res, out=tmp_path)
    for f in ("CROSS_VENUE_LIFECYCLE_COVERAGE.md", "CROSS_VENUE_LIFECYCLE_COVERAGE.json", "VENUE_PRECEDENCE_UNKNOWN_REMAINING.md",
              "H2_AFTER_CROSS_VENUE_COVERAGE_MATRIX.csv", "H2_AFTER_CROSS_VENUE_COVERAGE_MATRIX.json"):
        assert (tmp_path / f).exists(), f
    md = (tmp_path / "CROSS_VENUE_LIFECYCLE_COVERAGE.md").read_text()
    assert "no signal" in md and "does not re-open" in md
    for banned in ("t-stat", "sharpe", "edge bps", "profitable"):
        assert banned not in md.lower()
    assert json.loads((tmp_path / "H2_AFTER_CROSS_VENUE_COVERAGE_MATRIX.json").read_text())["no_alpha_test"] is True
