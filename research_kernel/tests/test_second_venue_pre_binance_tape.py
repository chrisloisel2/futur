"""Tapes de controle et de second venue : routes, normalisation, borne de cloture, choix des candidats, Gate borne en profondeur."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from data_lake.collectors import control_venue_pre_binance_tape as C
from data_lake.collectors import second_venue_pre_binance_tape as S
from data_lake.collectors import venue_pre_binance_paths as VP

H = 3_600_000; D = 86_400_000


def test_okx_daily_is_utc_and_every_venue_builds_a_url():
    assert "bar=1Dutc" in C.build_url("okx", "spot", "X-USDT", "1d", 0, 1) and "bar=1H" in C.build_url("okx", "perp", "X-USDT-SWAP", "60m", 0, 1)
    assert "category=linear" in C.build_url("bybit", "perp", "XUSDT", "60m", 0, 1) and "category=spot" in C.build_url("bybit", "spot", "XUSDT", "60m", 0, 1)
    assert "granularity=60" in C.build_url("kucoin", "perp", "XUSDTM", "60m", 0, 1) and "type=1hour" in C.build_url("kucoin", "spot", "X-USDT", "60m", 0, 1)
    assert "futures/usdt/candlesticks?contract=X_USDT&interval=1h" in C.build_url("gate", "perp", "X_USDT", "60m", 0, 1) and "spot/candlesticks?currency_pair=X_USDT" in C.build_url("gate", "spot", "X_USDT", "60m", 0, 1)


def test_normalise_reads_each_venue_shape_and_gate_quote_volume_column():
    g = C.normalise("gate", "spot", [["1700000000", "1234.5", "2", "3", "1", "1.5", "600", "true"]])
    assert g == [{"open_time_ms": 1700000000000, "open": 1.5, "high": 3.0, "low": 1.0, "close": 2.0, "volume": 600.0, "quote_volume": 1234.5}]
    gp = C.normalise("gate", "perp", [{"t": 1700000000, "v": 10, "c": "2", "h": "3", "l": "1", "o": "1.5", "sum": "20"}])
    assert gp[0]["quote_volume"] == 20.0 and gp[0]["volume"] == 10.0
    k = C.normalise("kucoin", "spot", {"data": [["1700000000", "1.5", "2", "3", "1", "600", "1234.5"]]})
    assert k[0]["open"] == 1.5 and k[0]["close"] == 2.0 and k[0]["high"] == 3.0 and k[0]["quote_volume"] == 1234.5
    assert C.normalise("okx", "spot", {"data": "garbage"}) == []


def test_fetch_window_is_close_bounded(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None); t0 = 1_700_000_000_000 + 30 * 60_000
    payload = {"result": {"list": [[str(t0 - 2 * H), "1", "1", "1", "1", "1", "1"], [str(t0 - H), "1", "1", "1", "1", "1", "1"], [str(t0 - 30 * 60_000), "1", "1", "1", "1", "1", "1"]]}}
    monkeypatch.setattr(C, "_get", lambda url, timeout=30: payload)
    r = C.fetch_window("bybit", "perp", "XUSDT", "60m", t0 - 72 * H, t0)
    assert [x["open_time_ms"] for x in r["rows"]] == [t0 - 2 * H, t0 - H]                     # celle qui chevauche t0 est retiree


def test_candidates_prefer_dated_second_venues_with_enough_overlap():
    ev = {"event_id": "e", "asset": "1000PEPE", "t0": "2023-05-05T12:30:00+00:00"}; t0 = int(VP.parse_ts(ev["t0"]).timestamp() * 1000); pub = t0 - 2 * H
    by = {"PEPE": [{"venue": "kucoin", "market_type": "spot", "symbol": "PEPE-USDT", "first_listed_ts": "2023-04-20T00:00:00+00:00"},
                   {"venue": "bybit", "market_type": "perp", "symbol": "PEPEUSDT", "first_listed_ts": "2023-05-04T20:00:00+00:00"},       # trop recent : < 36 h utiles
                   {"venue": "gate", "market_type": "spot", "symbol": "PEPE_USDT", "first_listed_ts": None}]}
    c = S.candidates(ev, by, pub)
    assert [x["venue"] for x in c] == ["kucoin", "gate"] and c[0]["dated"] and not c[1]["dated"] and c[0]["overlap_h"] > 36


def test_collect_event_writes_manifest_and_counts_hours_in_window(tmp_path, monkeypatch):
    monkeypatch.setattr(S.time, "sleep", lambda s: None); t0 = 1_700_000_000_000; pub = t0 - 2 * H
    ev = {"event_id": "e1", "asset": "X", "t0": "2023-11-14T22:13:20+00:00"}; assert int(VP.parse_ts(ev["t0"]).timestamp() * 1000) == t0
    by = {"X": [{"venue": "kucoin", "market_type": "spot", "symbol": "X-USDT", "first_listed_ts": "2023-10-01T00:00:00+00:00"}]}
    rows = [{"open_time_ms": t0 - 72 * H + i * H, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "quote_volume": 1} for i in range(72)]
    monkeypatch.setattr(S, "fetch_window", lambda v, m, s, iv, a, b: {"status": "ok", "url": "u", "rows": rows, "raw_hash": "h", "error": None})
    man = S.collect_event(ev, by, pub, root=tmp_path)
    assert man["status"] == "collected" and man["usable_tapes"] == 1 and man["attempts"][0]["n_in_window"] == 69 and man["close_bounded"] and man["no_post_t0_data"]
    assert S.manifest_path("e1", tmp_path).exists() and (tmp_path / "second_venue" / "tape_log.jsonl").exists()
    assert S.collect_event(ev, by, pub, root=tmp_path) == man                                                     # idempotent sans refetch


def test_gate_is_skipped_beyond_its_depth_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(S.time, "sleep", lambda s: None); calls = []
    monkeypatch.setattr(S, "fetch_window", lambda v, m, s, iv, a, b: calls.append(v) or {"status": "empty", "url": "u", "rows": [], "raw_hash": None, "error": None})
    ev = {"event_id": "old", "asset": "Y", "t0": "2022-09-10T03:00:00+00:00"}; by = {"Y": [{"venue": "gate", "market_type": "spot", "symbol": "Y_USDT", "first_listed_ts": None}]}
    man = S.collect_event(ev, by, None, root=tmp_path)
    assert calls == [] and man["status"] == "no_candidate" and man["candidates_skipped_gate_depth"] == 1
