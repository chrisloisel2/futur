"""mexc_pre_binance_tape: candles normalised from both MEXC formats, nothing at or after t0 kept, graceful failures."""
import json, sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from data_lake.collectors import mexc_pre_binance_tape as T
from data_lake.collectors import venue_pre_binance_paths as VP


def test_requests_never_cross_t0():
    t0 = "2025-05-02T08:30:00+00:00"; t0_ms = int(VP.parse_ts(t0).timestamp() * 1000)
    for r in VP.requests_for(t0):
        assert r["end_ms"] == t0_ms and r["start_ms"] < t0_ms
    w = VP.windows(t0); assert w["30d"]["end"] == "2025-05-02T08:30:00+00:00" and w["6h"]["start"] == "2025-05-02T02:30:00+00:00"


def test_spot_and_perp_payloads_normalise_to_one_shape():
    spot = [[1000, "1", "2", "0.5", "1.5", "10", 1999, "15"], [2000, "1.5", "2", "1", "1.2", "5", 2999, "6"]]
    perp = {"data": {"time": [1, 2], "open": [1, 1.5], "high": [2, 2], "low": [0.5, 1], "close": [1.5, 1.2], "vol": [10, 5], "amount": [15, 6]}}
    a, b = T.normalise_candles(spot, "spot"), T.normalise_candles(perp, "perp")
    assert a[0]["close"] == 1.5 and a[0]["quote_volume"] == 15.0 and b[0]["open_time_ms"] == 1000 and b[1]["close"] == 1.2
    assert T.normalise_candles("garbage", "spot") == [] and T.normalise_candles([[1]], "spot") == []


def test_fetch_window_drops_candles_at_or_after_t0_and_survives_errors(monkeypatch):
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    monkeypatch.setattr(T, "_get", lambda url, timeout=30: [[900, "1", "1", "1", "1", "1", 999, "1"], [1000, "1", "1", "1", "1", "1", 1099, "1"]])
    r = T.fetch_window("XUSDT", "spot", "1d", 0, 1000)
    assert r["status"] == "ok" and len(r["rows"]) == 1 and r["rows"][0]["open_time_ms"] == 900      # la bougie a t0 est retiree
    import http.client
    monkeypatch.setattr(T, "_get", lambda url, timeout=30: (_ for _ in ()).throw(http.client.IncompleteRead(b"x")))
    assert T.fetch_window("XUSDT", "spot", "1d", 0, 1000)["status"] == "error"
    from urllib.error import HTTPError
    monkeypatch.setattr(T, "_get", lambda url, timeout=30: (_ for _ in ()).throw(HTTPError(url, 404, "no", {}, None)))
    assert T.fetch_window("XUSDT", "spot", "1d", 0, 1000)["status"] == "not_found"


def test_collect_event_writes_manifest_and_never_post_t0(tmp_path, monkeypatch):
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    monkeypatch.setattr(T, "_get", lambda url, timeout=30: [[1746100000000, "1", "1", "1", "1", "1", 1746100000001, "1"]])
    ev = {"event_id": "e1", "asset": "X", "binance_symbol": "XUSDT", "t0": "2025-05-02T08:30:00+00:00", "mexc_symbol": "XUSDT", "mexc_market": "spot", "mexc_listed_ts": None, "lead_days": 1.0}
    m = T.collect_event(ev, root=tmp_path, refetch=True)
    assert m["status"] == "collected" and m["no_post_t0_data"] is True and (tmp_path / "mexc" / "manifests" / "e1.json").exists()
    m2 = T.collect_event({**ev, "mexc_symbol": None, "event_id": "e2"}, root=tmp_path)
    assert m2["status"] == "not_collected"


def test_mexc_first_events_come_from_the_real_precedence():
    evs = T.mexc_first_events()
    assert len(evs) > 100 and all(e["mexc_symbol"] and e["mexc_market"] in ("spot", "perp") for e in evs)
