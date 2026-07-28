"""
tests/test_alpha20_deployment_guard.py
─────────────────────────────────────────────────────────────────────────────
Garde de dérive de déploiement (incident 2026-07-21 : une démotion de
runner commitée n'avait jamais atteint le fichier live). Aucun réseau,
aucun fichier réel modifié -- tout sur tmp_path.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.alpha20.deployment_guard import (
    DeploymentDriftError, assert_deployment_matches_approved, current_hashes)


def _write_tracked_files(root: Path, content_a: str = "a: 1\n", content_b: str = "b: 2\n"):
    (root / "configs").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "alpha20_runners.yaml").write_text(content_a)
    (root / "configs" / "alpha20.yaml").write_text(content_b)


def test_missing_manifest_refuses_to_start(tmp_path, monkeypatch):
    import src.alpha20.deployment_guard as dg
    _write_tracked_files(tmp_path)
    monkeypatch.setattr(dg, "ROOT", tmp_path)
    monkeypatch.setattr(dg, "MANIFEST_PATH", tmp_path / "configs" / "DEPLOYMENT_MANIFEST.json")
    with pytest.raises(DeploymentDriftError):
        assert_deployment_matches_approved(exit_on_fail=False)


def test_matches_approved_manifest_passes(tmp_path, monkeypatch):
    import src.alpha20.deployment_guard as dg
    _write_tracked_files(tmp_path)
    monkeypatch.setattr(dg, "ROOT", tmp_path)
    manifest_path = tmp_path / "configs" / "DEPLOYMENT_MANIFEST.json"
    monkeypatch.setattr(dg, "MANIFEST_PATH", manifest_path)
    manifest_path.write_text(json.dumps({
        "approved_at": "2026-07-22T00:00:00Z", "git_commit": "deadbeef",
        "config_hash": current_hashes()}))
    assert_deployment_matches_approved(exit_on_fail=False)   # ne lève rien


def test_drift_after_approval_refuses_to_start(tmp_path, monkeypatch):
    import src.alpha20.deployment_guard as dg
    _write_tracked_files(tmp_path)
    monkeypatch.setattr(dg, "ROOT", tmp_path)
    manifest_path = tmp_path / "configs" / "DEPLOYMENT_MANIFEST.json"
    monkeypatch.setattr(dg, "MANIFEST_PATH", manifest_path)
    manifest_path.write_text(json.dumps({
        "approved_at": "2026-07-22T00:00:00Z", "git_commit": "deadbeef",
        "config_hash": current_hashes()}))

    # modification NON approuvée après la génération du manifeste
    (tmp_path / "configs" / "alpha20_runners.yaml").write_text("a: 999\n")

    with pytest.raises(DeploymentDriftError) as exc:
        assert_deployment_matches_approved(exit_on_fail=False)
    assert "alpha20_runners.yaml" in str(exc.value)


def test_exit_on_fail_raises_systemexit(tmp_path, monkeypatch):
    import src.alpha20.deployment_guard as dg
    _write_tracked_files(tmp_path)
    monkeypatch.setattr(dg, "ROOT", tmp_path)
    monkeypatch.setattr(dg, "MANIFEST_PATH", tmp_path / "configs" / "DEPLOYMENT_MANIFEST.json")
    with pytest.raises(SystemExit) as exc:
        assert_deployment_matches_approved(exit_on_fail=True)
    assert exc.value.code == 2
