"""
tests/test_command_center_status_api.py — /api/status (vivacité par fraîcheur
des artefacts). Artefacts temporaires avec mtimes contrôlés (os.utime) :
fresh / stale / stopped / error / unknown ; lab ; disque ; cache 15 s.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend_pipeline import status_api as sa  # noqa: E402

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)


def _touch(p: Path, age_min: float, content: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    t = NOW.timestamp() - age_min * 60.0
    os.utime(str(p), (t, t))
    return p


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "_now", lambda: NOW)
    monkeypatch.setattr(sa, "_cache", {})
    paths = {
        "CYCLE_STATE_PATH": tmp_path / "lab" / "CYCLE_STATE.json",
        "DERIVATIVES_RAW_DIR": tmp_path / "derivatives_raw",
        "HYPERLIQUID_DIR": tmp_path / "hyperliquid",
        "MICROSTRUCTURE_PATH": tmp_path / "lab" / "microstructure_monitoring.jsonl",
        "DISK_WATCHDOG_PATH": tmp_path / "ops" / "disk_watchdog.jsonl",
        "NEWS_RAW_DIR": tmp_path / "news_raw",
        "POSITIONING_DIR": tmp_path / "positioning",
        "OPTIONS_DERIBIT_DIR": tmp_path / "options" / "deribit",
        "EVENT_SHADOW_STATE_PATH": tmp_path / "liq" / "shadow" / "state.json",
        "PAPER_V1_PATH": tmp_path / "paper_trading" / "fleet_summary.json",
        "TOURNAMENT_LEDGER_DIR": tmp_path / "alpha20" / "ledger",
        "DISK_PATH": tmp_path,
    }
    for k, v in paths.items():
        monkeypatch.setattr(sa, k, v)
    monkeypatch.setattr(sa, "_legacy_paper_rebalanced_at", lambda: None)
    return paths


def _rows(d):
    return {s["key"]: s for s in d["services"]}


def test_all_unknown_when_nothing_exists(env):
    d = sa.build_status()
    rows = _rows(d)
    assert "hyperliquid" not in rows                 # répertoire absent → service omis
    for k, s in rows.items():
        assert s["state"] == "unknown", k
        assert s["age_min"] is None
    assert d["lab"]["live"] is False and d["lab"]["status"] is None
    assert d["ts"] == NOW.isoformat()
    assert d["disk"]["path"] == str(env["DISK_PATH"]) and d["disk"]["free_gb"] > 0


def test_running_fresh_vs_stale(env):
    _touch(env["CYCLE_STATE_PATH"], 10, json.dumps({
        "cycle_finished_at": (NOW - timedelta(minutes=10)).isoformat(), "status": "OK",
        "producers_ok": 4, "producers_run": 5, "producers_failed": [{"name": "X"}]}))
    _touch(env["MICROSTRUCTURE_PATH"], 31)          # > 30 → stale
    _touch(env["DISK_WATCHDOG_PATH"], 30)           # == 30 → fresh (<=)
    _touch(env["NEWS_RAW_DIR"] / "date=2026-09-03" / "a.parquet", 500)
    _touch(env["NEWS_RAW_DIR"] / "date=2026-09-03" / "b.parquet", 100)   # le plus récent compte
    _touch(env["POSITIONING_DIR"] / "BTCUSDT_taker_vol.parquet", 700)
    _touch(env["POSITIONING_DIR"] / "BTCUSDT_taker_vol.parquet.lock", 1)  # ignoré (suffixe)
    _touch(env["OPTIONS_DERIBIT_DIR"] / "features" / "BTC_daily.parquet", 1561)
    _touch(env["EVENT_SHADOW_STATE_PATH"], 1000)
    rows = _rows(sa.build_status())
    assert rows["lab_timer"]["state"] == "fresh" and rows["lab_timer"]["age_min"] == 10.0
    assert rows["microstructure"]["state"] == "stale"
    assert rows["disk_watchdog"]["state"] == "fresh"
    assert rows["news"]["state"] == "fresh" and rows["news"]["age_min"] == 100.0
    assert rows["positioning"]["state"] == "fresh" and rows["positioning"]["age_min"] == 700.0
    assert rows["options"]["state"] == "stale"
    assert rows["event_shadow"]["state"] == "fresh"
    for s in rows.values():
        assert s["expected"] in ("running", "stopped")
        assert set(s) >= {"key", "label", "state", "artefact", "age_min", "expected"}


def test_lab_summary_semantics(env):
    _touch(env["CYCLE_STATE_PATH"], 1, json.dumps({
        "cycle_finished_at": (NOW - timedelta(minutes=25)).isoformat(), "status": "OK",
        "producers_ok": 4, "producers_run": 5, "producers_failed": [{"name": "X"}, "Y"]}))
    lab = sa.build_status()["lab"]
    assert lab == {"finished_at": (NOW - timedelta(minutes=25)).isoformat(), "status": "OK",
                   "producers_ok": 4, "producers_run": 5, "producers_failed": ["X", "Y"],
                   "age_min": 25.0, "live": True}
    sa._cache.clear()
    _touch(env["CYCLE_STATE_PATH"], 1, json.dumps({
        "cycle_finished_at": (NOW - timedelta(minutes=31)).isoformat(), "status": "OK"}))
    assert sa.build_status()["lab"]["live"] is False
    sa._cache.clear()
    _touch(env["CYCLE_STATE_PATH"], 1, json.dumps({
        "cycle_finished_at": (NOW - timedelta(minutes=1)).isoformat(), "status": "DEGRADED"}))
    assert sa.build_status()["lab"]["live"] is False
    sa._cache.clear()
    _touch(env["CYCLE_STATE_PATH"], 1, "{not json")
    assert sa.build_status()["lab"]["status"] is None


def test_derivatives_manifest_walk_today_then_yesterday(env):
    raw = env["DERIVATIVES_RAW_DIR"]
    today = NOW.date().isoformat()
    yday = (NOW - timedelta(days=1)).date().isoformat()
    old = (NOW - timedelta(days=5)).date().isoformat()
    # seul un vieux jour → unknown (le parcours ne remonte pas au-delà d'hier)
    _touch(raw / "exchange=binance/market=usdm/stream=oi/symbol=BTCUSDT" / ("date=" + old) / "p.manifest.json", 3)
    assert _rows(sa.build_status())["derivatives"]["state"] == "unknown"
    sa._cache.clear()
    # hier → pris
    _touch(raw / "exchange=okx/market=swap/stream=oi/symbol=BTC" / ("date=" + yday) / "p.manifest.json", 45)
    r = _rows(sa.build_status())["derivatives"]
    assert r["state"] == "stale" and r["age_min"] == 45.0
    sa._cache.clear()
    # aujourd'hui → prioritaire, .parquet ignoré (manifests seulement)
    _touch(raw / "exchange=binance/market=usdm/stream=oi/symbol=ETHUSDT" / ("date=" + today) / "p.parquet", 1)
    _touch(raw / "exchange=binance/market=usdm/stream=oi/symbol=ETHUSDT" / ("date=" + today) / "p.manifest.json", 7)
    r = _rows(sa.build_status())["derivatives"]
    assert r["state"] == "fresh" and r["age_min"] == 7.0


def test_hyperliquid_present_when_dir_exists(env):
    _touch(env["HYPERLIQUID_DIR"] / "trades" / "date=2026-09-03" / "part-1.parquet", 12)
    r = _rows(sa.build_status())["hyperliquid"]
    assert r["state"] == "fresh" and r["age_min"] == 12.0 and r["expected"] == "running"


def test_expected_stopped_states(env):
    _touch(env["PAPER_V1_PATH"], 61)                                    # > 60 → stopped
    _touch(env["TOURNAMENT_LEDGER_DIR"] / "carry" / "ledger.jsonl", 59)  # frais → error
    rows = _rows(sa.build_status())
    assert rows["paper_v1"]["state"] == "stopped" and rows["paper_v1"]["expected"] == "stopped"
    assert rows["tournament"]["state"] == "error"
    assert rows["legacy_paper"]["state"] == "unknown"                  # doc sans rebalanced_at


def test_legacy_paper_via_mongo_doc(env, monkeypatch):
    monkeypatch.setattr(sa, "_legacy_paper_rebalanced_at",
                        lambda: (NOW - timedelta(hours=5)).isoformat())
    r = _rows(sa.build_status())["legacy_paper"]
    assert r["state"] == "stopped" and r["age_min"] == 300.0
    sa._cache.clear()
    monkeypatch.setattr(sa, "_legacy_paper_rebalanced_at",
                        lambda: (NOW - timedelta(minutes=5)).isoformat())
    assert _rows(sa.build_status())["legacy_paper"]["state"] == "error"
    sa._cache.clear()

    def boom():
        raise RuntimeError("mongo indisponible")
    monkeypatch.setattr(sa, "_legacy_paper_rebalanced_at", boom)
    assert _rows(sa.build_status())["legacy_paper"]["state"] == "unknown"


def test_service_order_and_labels(env):
    _touch(env["HYPERLIQUID_DIR"] / "state.json", 1)
    keys = [s["key"] for s in sa.build_status()["services"]]
    assert keys == ["lab_timer", "derivatives", "hyperliquid", "microstructure", "disk_watchdog",
                    "news", "positioning", "options", "event_shadow",
                    "paper_v1", "tournament", "legacy_paper"]
    labels = {s["key"]: s["label"] for s in sa.build_status()["services"]}
    assert labels["paper_v1"] == "paper V1.1 (arrêté 03/09)"
    assert labels["tournament"] == "tournoi ALPHA_20 (arrêté 03/09)"
    assert labels["legacy_paper"] == "paper Mongo (gelé 03/09)"
    assert labels["event_shadow"] == "event shadow (quotidien)"


def test_endpoint_cached_15s(env, monkeypatch):
    from fastapi.testclient import TestClient
    from frontend_pipeline import auth
    from frontend_pipeline.command_center import app
    import base64, hashlib
    users = env["DISK_PATH"] / "users.json"
    salt = b"s" * 16
    users.write_text(json.dumps({"schema_version": 1, "users": {"a": {
        "algo": "pbkdf2_sha256", "iterations": 10, "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"p", salt, 10)).decode(),
        "role": "guest"}}}), encoding="utf-8")
    secret = env["DISK_PATH"] / "secret"
    secret.write_text("cd" * 32, encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users)
    monkeypatch.setattr(auth, "SECRET_FILE", secret)
    monkeypatch.setattr(auth, "_users_cache", {"mtime": None, "users": None})
    monkeypatch.setattr(auth, "_secret_cache", {"mtime": None, "secret": None})
    c = TestClient(app)
    assert c.get("/api/status").status_code == 401
    c.post("/login", data={"username": "a", "password": "p"}, follow_redirects=False)
    _touch(env["DISK_WATCHDOG_PATH"], 5)
    d1 = c.get("/api/status").json()
    assert _rows(d1)["disk_watchdog"]["state"] == "fresh"
    _touch(env["DISK_WATCHDOG_PATH"], 500)
    d2 = c.get("/api/status").json()
    assert d2 == d1                                  # cache 15 s
    sa._cache["status"] = (time.time() - sa.STATUS_TTL_S - 1, d1)
    assert _rows(c.get("/api/status").json())["disk_watchdog"]["state"] == "stale"


def test_newest_walk_capped(env):
    for i in range(20):
        _touch(env["NEWS_RAW_DIR"] / ("f%02d" % i), 20 - i)
    assert sa._newest_in_dir(env["NEWS_RAW_DIR"]) == pytest.approx(NOW.timestamp() - 60.0)
    capped = sa._newest_in_dir(env["NEWS_RAW_DIR"], cap=3)
    assert capped is not None and capped <= NOW.timestamp()
