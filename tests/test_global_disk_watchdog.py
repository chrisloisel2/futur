"""
tests/test_global_disk_watchdog.py — item P0.2 (phase OPERATIONAL
HARDENING) : seuils, listes d'arrêt opt-in, jamais de service essentiel
touché, logging append-only.
"""
from __future__ import annotations

import json

import pytest

import scripts.global_disk_watchdog as watchdog


def test_level_thresholds():
    assert watchdog.level_for(100.0) == "OK"
    assert watchdog.level_for(30.0) == "WARNING"
    assert watchdog.level_for(25.0) == "WARNING"
    assert watchdog.level_for(20.0) == "CRITICAL"
    assert watchdog.level_for(15.0) == "CRITICAL"
    assert watchdog.level_for(12.0) == "EMERGENCY"
    assert watchdog.level_for(0.5) == "EMERGENCY"


def test_essential_services_never_appear_in_any_stop_list():
    for svc in watchdog.ESSENTIAL_NEVER_STOP:
        assert svc not in watchdog.CRITICAL_STOPPABLE
        assert svc not in watchdog.EMERGENCY_STOPPABLE


def test_critical_stoppable_is_subset_of_emergency_stoppable():
    for svc in watchdog.CRITICAL_STOPPABLE:
        assert svc in watchdog.EMERGENCY_STOPPABLE


def test_warning_takes_no_action(monkeypatch, tmp_path):
    monkeypatch.setattr(watchdog, "free_gb", lambda: 25.0)
    monkeypatch.setattr(watchdog, "LOG_PATH", tmp_path / "disk_watchdog.jsonl")
    stopped = []
    monkeypatch.setattr(watchdog, "stop_service", lambda s: stopped.append(s) or "stopped")

    record = watchdog.run_once()
    assert record["level"] == "WARNING"
    assert record["actions"] == {}
    assert stopped == []


def test_critical_stops_only_critical_stoppable_services(monkeypatch, tmp_path):
    monkeypatch.setattr(watchdog, "free_gb", lambda: 18.0)
    monkeypatch.setattr(watchdog, "LOG_PATH", tmp_path / "disk_watchdog.jsonl")
    calls = []
    monkeypatch.setattr(watchdog, "stop_service", lambda s: calls.append(s) or "stopped")

    record = watchdog.run_once()
    assert record["level"] == "CRITICAL"
    assert set(calls) == set(watchdog.CRITICAL_STOPPABLE)
    for svc in watchdog.ESSENTIAL_NEVER_STOP:
        assert svc not in calls


def test_emergency_stops_the_full_expanded_list(monkeypatch, tmp_path):
    monkeypatch.setattr(watchdog, "free_gb", lambda: 5.0)
    monkeypatch.setattr(watchdog, "LOG_PATH", tmp_path / "disk_watchdog.jsonl")
    calls = []
    monkeypatch.setattr(watchdog, "stop_service", lambda s: calls.append(s) or "stopped")

    record = watchdog.run_once()
    assert record["level"] == "EMERGENCY"
    assert set(calls) == set(watchdog.EMERGENCY_STOPPABLE)
    for svc in watchdog.ESSENTIAL_NEVER_STOP:
        assert svc not in calls


def test_never_deletes_or_moves_anything(monkeypatch, tmp_path):
    """Le watchdog ne doit appeler QUE des opérations en lecture (disk_usage,
    is-active) et systemctl stop -- jamais rm/unlink/rename/shutil.move."""
    import inspect
    source = inspect.getsource(watchdog)
    for forbidden in ("os.remove", "unlink(", "shutil.rmtree", "shutil.move", "os.rename"):
        assert forbidden not in source, f"opération destructive trouvée dans le watchdog: {forbidden}"


def test_log_is_append_only_jsonl(monkeypatch, tmp_path):
    log_path = tmp_path / "disk_watchdog.jsonl"
    monkeypatch.setattr(watchdog, "LOG_PATH", log_path)
    monkeypatch.setattr(watchdog, "free_gb", lambda: 100.0)
    monkeypatch.setattr(watchdog, "stop_service", lambda s: "stopped")

    watchdog.run_once()
    watchdog.run_once()
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        d = json.loads(line)   # chaque ligne doit être un JSON valide indépendant
        assert "free_gb" in d and "level" in d and "timestamp" in d


def test_stop_service_already_inactive_does_not_call_systemctl_stop(monkeypatch):
    monkeypatch.setattr(watchdog, "is_active", lambda s: False)
    called = []
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda *a, **k: called.append(a) or pytest.fail("ne doit pas être appelé"))
    result = watchdog.stop_service("some.service")
    assert result == "already_inactive"
    assert called == []
