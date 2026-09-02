"""
tests/test_microstructure_monitor.py — item P1 (phase OPERATIONAL
HARDENING) : monitoring rolling 1h/6h/24h du collecteur microstructure
réduit. Détecter un collecteur vivant mais silencieusement incomplet.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

import scripts.microstructure_monitor as mon


def _write_hour_file(base, date_str, hh, lines):
    d = base / f"date={date_str}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"events-{hh:02d}.jsonl.gz"
    with gzip.open(p, "wb") as f:
        for line in lines:
            f.write((json.dumps(line) + "\n").encode())
    return p


def _event(ts: datetime, symbol="BTCUSDT", venue="binance"):
    return {"event_ts_ns": int(ts.timestamp() * 1e9), "symbol": symbol, "venue": venue, "source_stream": "bookTicker"}


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mon, "ROW_COUNT_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(mon, "SNAPSHOT_LOG", tmp_path / "snapshots.jsonl")
    mon._row_count_cache = {}
    yield


def test_hour_files_parses_hour_despite_double_suffix(tmp_path):
    """Path.stem ne retire qu'UN suffixe -- events-05.jsonl.gz doit quand
    même être reconnu comme l'heure 05, pas silencieusement ignoré."""
    base = tmp_path / "symbol=BTCUSDT"
    now = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)
    _write_hour_file(base, "2026-09-02", 5, [_event(now)])
    files = mon._hour_files(base)
    assert len(files) == 1
    assert files[0]["hour_start"] == datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)


def test_count_rows_correct_and_cached(tmp_path, monkeypatch):
    now = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)
    base = tmp_path / "symbol=BTCUSDT"
    p = _write_hour_file(base, "2026-09-02", 5, [_event(now), _event(now), _event(now)])
    assert mon._count_rows(p) == 3

    calls = {"n": 0}
    real_gzip_open = gzip.open

    def counting_open(*a, **k):
        calls["n"] += 1
        return real_gzip_open(*a, **k)

    monkeypatch.setattr(mon.gzip, "open", counting_open)
    assert mon._count_rows(p) == 3   # cache hit -- pas de nouvel appel gzip.open
    assert calls["n"] == 0


def test_count_rows_recomputes_when_file_changes(tmp_path):
    """Un fichier dont mtime/size changent (l'heure en cours, toujours en
    écriture) doit être recompté, jamais servi depuis un cache périmé."""
    now = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)
    base = tmp_path / "symbol=BTCUSDT"
    p = _write_hour_file(base, "2026-09-02", 5, [_event(now)])
    assert mon._count_rows(p) == 1
    p2 = _write_hour_file(base, "2026-09-02", 5, [_event(now), _event(now)])
    assert p2 == p
    assert mon._count_rows(p) == 2   # pas resté bloqué à 1


def test_analyze_series_windows_rows_correctly(tmp_path, monkeypatch):
    base = tmp_path / "symbol=BTCUSDT"
    now = datetime(2026, 9, 2, 5, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(mon, "_now", lambda: now)
    # heure courante (05h) : 2 events -- dans 1h/6h/24h
    _write_hour_file(base, "2026-09-02", 5, [_event(now), _event(now)])
    # 3h avant (02h) : 1 event -- dans 6h/24h mais PAS 1h
    _write_hour_file(base, "2026-09-02", 2, [_event(now - timedelta(hours=3))])
    # 20h avant (veille 09h) : 1 event -- dans 24h mais PAS 1h/6h
    _write_hour_file(base, "2026-09-01", 9, [_event(now - timedelta(hours=20))])

    entry = {"stream_type": "bbo", "venue": "binance", "symbol": "BTCUSDT", "dir": str(base)}
    result = mon.analyze_series(entry, now)
    assert result["rows_1h"] == 2
    assert result["rows_6h"] == 3
    assert result["rows_24h"] == 4


def test_last_event_age_uses_fresh_clock_not_frozen_now(tmp_path, monkeypatch):
    """Bug trouvé et corrigé : `now` figé au début d'un run de plusieurs
    dizaines de secondes donnait un âge NÉGATIF pour un collecteur qui
    continue d'écrire pendant l'exécution du script. L'âge doit toujours
    être calculé par rapport à une horloge fraîche."""
    base = tmp_path / "symbol=BTCUSDT"
    frozen_now = datetime(2026, 9, 2, 5, 0, 0, tzinfo=timezone.utc)
    real_event_time = datetime(2026, 9, 2, 5, 0, 10, tzinfo=timezone.utc)   # 10s APRÈS le now figé
    _write_hour_file(base, "2026-09-02", 5, [_event(real_event_time)])

    fresh_now = datetime(2026, 9, 2, 5, 0, 15, tzinfo=timezone.utc)   # horloge fraîche, 15s après real_event_time
    monkeypatch.setattr(mon, "_now", lambda: fresh_now)

    entry = {"stream_type": "bbo", "venue": "binance", "symbol": "BTCUSDT", "dir": str(base)}
    result = mon.analyze_series(entry, frozen_now)   # `now` figé passé en paramètre (comme le vrai code)
    assert result["last_event_age_s"] == pytest.approx(5.0)   # fresh_now - real_event_time, jamais négatif
    assert result["last_event_age_s"] > 0


def test_missing_intervals_detected(tmp_path):
    base = tmp_path / "symbol=BTCUSDT"
    now = datetime(2026, 9, 2, 5, 30, tzinfo=timezone.utc)
    # seulement l'heure courante -- toutes les 23 heures précédentes manquent
    _write_hour_file(base, "2026-09-02", 5, [_event(now)])
    entry = {"stream_type": "bbo", "venue": "binance", "symbol": "BTCUSDT", "dir": str(base)}
    result = mon.analyze_series(entry, now)
    assert len(result["missing_intervals_24h"]) == 23   # 24 heures attendues - 1 présente


def test_no_data_at_all_flagged_explicitly(tmp_path):
    base = tmp_path / "symbol=BTCUSDT"
    base.mkdir(parents=True)
    entry = {"stream_type": "bbo", "venue": "binance", "symbol": "BTCUSDT", "dir": str(base)}
    result = mon.analyze_series(entry, datetime.now(timezone.utc))
    assert result["missing_intervals_24h"] == "NO_DATA_AT_ALL"
    assert result["last_event_age_s"] is None


def test_windowed_counter_deltas_insufficient_history_on_first_run():
    now = datetime.now(timezone.utc)
    deltas = mon.windowed_counter_deltas({"binance_bbo": 100}, now)
    assert deltas["1h"] == "INSUFFICIENT_HISTORY"
    assert deltas["24h"] == "INSUFFICIENT_HISTORY"


def test_windowed_counter_deltas_computes_real_delta_against_prior_snapshot(tmp_path):
    now = datetime.now(timezone.utc)
    prior_ts = now - timedelta(hours=1)
    mon.SNAPSHOT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with mon.SNAPSHOT_LOG.open("a") as f:
        f.write(json.dumps({
            "timestamp": prior_ts.isoformat(),
            "collector_counters": {"binance_bbo": 100, "reconnects": {"binance_public": 2}},
        }) + "\n")

    deltas = mon.windowed_counter_deltas(
        {"binance_bbo": 250, "reconnects": {"binance_public": 3}}, now)
    assert deltas["1h"] == {"binance_bbo": 150, "reconnects": {"binance_public": 1}}
    assert deltas["24h"] == "INSUFFICIENT_HISTORY"   # rien d'assez ancien pour 24h


def test_never_reads_a_prior_snapshot_wildly_off_target_window():
    """Un snapshot vieux de 10 minutes ne doit pas servir de base pour un
    delta '1h' -- le résultat serait trompeur (pas vraiment 1h de fenêtre)."""
    now = datetime.now(timezone.utc)
    too_recent = now - timedelta(minutes=10)
    mon.SNAPSHOT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with mon.SNAPSHOT_LOG.open("a") as f:
        f.write(json.dumps({"timestamp": too_recent.isoformat(), "collector_counters": {}}) + "\n")
    result = mon._find_prior_snapshot(1.0, now)
    assert result is None
