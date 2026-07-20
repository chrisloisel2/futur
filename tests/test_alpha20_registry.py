"""
tests/test_alpha20_registry.py
─────────────────────────────────────────────────────────────────────────────
Registre versionné : le VRAI fichier configs/alpha20_runners.yaml se charge
sans erreur (ACTIVE ont un config, EXCLUDED/BLOCKED ont une justification,
runner_id uniques) ; le chargeur rejette un registre malformé (règles
d'intégrité testées isolément sur des fixtures synthétiques). Aucun réseau,
aucun ledger.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.alpha20.tournament import runner_registry as rr


def test_real_registry_loads_and_is_internally_consistent():
    reg = rr.load_registry()
    assert len(reg) > 0
    ids = list(reg)
    assert len(ids) == len(set(ids))                  # aucun doublon
    for rid, spec in reg.items():
        if spec.status in rr.RUNNABLE_STATUSES:
            assert spec.config, f"{rid}: ACTIVE/OBSERVE_ONLY sans config"
            assert spec.config_hash is not None
        else:
            assert spec.justification, f"{rid}: {spec.status} sans justification"


def test_real_registry_expected_active_runners():
    specs = {s.runner_id for s in rr.runnable_specs()}
    assert specs == {"carry_basis_v12", "carry_solusdt", "carry_bnbusdt",
                     "basis_term_v0", "mh_events_exec"}


def test_xvenue_locked_out_of_the_tournament_registry():
    reg = rr.load_registry()
    xvenue = [rid for rid in reg if "xvenue" in rid.lower()]
    assert xvenue and all(reg[rid].status == "EXCLUDED" for rid in xvenue)


def test_v11_baseline_excluded_never_runnable():
    reg = rr.load_registry()
    assert reg["v1_1_baseline"].status == "EXCLUDED"
    assert "v1_1_baseline" not in {s.runner_id for s in rr.runnable_specs()}


def _write(tmp_path, raw):
    p = tmp_path / "reg.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def test_loader_rejects_duplicate_runner_id(tmp_path, monkeypatch):
    raw = {"git_commit": "x", "runners": [
        {"runner_id": "dup", "family": "f", "status": "EXCLUDED",
         "justification": "j"},
        {"runner_id": "dup", "family": "f", "status": "EXCLUDED",
         "justification": "j"}]}
    monkeypatch.setattr(rr, "REGISTRY_PATH", _write(tmp_path, raw))
    with pytest.raises(ValueError, match="dupliqué"):
        rr.load_registry()


def test_loader_rejects_active_without_config(tmp_path, monkeypatch):
    raw = {"git_commit": "x", "runners": [
        {"runner_id": "noconf", "family": "f", "status": "ACTIVE"}]}
    monkeypatch.setattr(rr, "REGISTRY_PATH", _write(tmp_path, raw))
    with pytest.raises(ValueError, match="config"):
        rr.load_registry()


def test_loader_rejects_excluded_without_justification(tmp_path, monkeypatch):
    raw = {"git_commit": "x", "runners": [
        {"runner_id": "nojust", "family": "f", "status": "EXCLUDED"}]}
    monkeypatch.setattr(rr, "REGISTRY_PATH", _write(tmp_path, raw))
    with pytest.raises(ValueError, match="justification"):
        rr.load_registry()


def test_loader_rejects_unknown_status(tmp_path, monkeypatch):
    raw = {"git_commit": "x", "runners": [
        {"runner_id": "x", "family": "f", "status": "MAYBE",
         "justification": "j"}]}
    monkeypatch.setattr(rr, "REGISTRY_PATH", _write(tmp_path, raw))
    with pytest.raises(ValueError, match="invalide"):
        rr.load_registry()
