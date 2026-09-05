"""tests/test_provenance_scope.py — périmètre du stamp working_tree_dirty
(décision utilisateur 2026-09-05) : seul le CODE DE DÉCISION compte
(src/, scripts/, configs/, freeze_spec.json, DEPLOYMENT_DECISIONS), les fichiers
d'état runtime sous reports/ ne rendent plus toutes les décisions « dirty »."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import src.institutional.live_alpha_lab.provenance as prov


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Dépôt git jetable avec la topologie réelle du projet, un commit propre."""
    r = tmp_path / "repo"
    for d in ("src/institutional/live_alpha_lab", "scripts", "configs",
              "reports/live_alpha_lab/ALPHA_X", "reports/live_alpha_lab/portfolios/P1",
              "frontend_pipeline/static", "tests"):
        (r / d).mkdir(parents=True)
    files = {
        "src/institutional/live_alpha_lab/portfolio.py": "x = 1\n",
        "scripts/run_alpha_x_shadow.py": "print(1)\n",
        "configs/live_alpha_registry.yaml": "alphas: []\n",
        "configs/validation_registry.yaml": "candidates: []\n",
        "reports/live_alpha_lab/ALPHA_X/freeze_spec.json": "{}\n",
        "reports/live_alpha_lab/DEPLOYMENT_DECISIONS_2026-09-03.md": "# d\n",
        "reports/live_alpha_lab/portfolios/P1/state.json": "{}\n",
        "reports/live_alpha_lab/cycle_log.jsonl": "",
        "frontend_pipeline/static/index.html": "<p>\n",
        "tests/test_x.py": "def test(): pass\n",
    }
    for rel, content in files.items():
        (r / rel).write_text(content)
    _git(r, "init", "-q")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    monkeypatch.setattr(prov, "_ROOT", r)
    return r


def test_clean_repo_is_clean(repo):
    assert prov.dirty_decision_paths() == []
    assert prov.working_tree_dirty() is False
    assert prov.dirty_decision_paths_sha1() == ""


def test_runtime_state_churn_under_reports_does_not_dirty_the_stamp(repo):
    """LE cas : 35 fichiers d'état réécrits toutes les 15 min."""
    (repo / "reports/live_alpha_lab/portfolios/P1/state.json").write_text('{"equity": 1}\n')
    (repo / "reports/live_alpha_lab/cycle_log.jsonl").write_text('{"c": 1}\n')
    (repo / "reports/live_alpha_lab/SCOREBOARD.md").write_text("# s\n")   # nouveau, non suivi
    assert prov.working_tree_dirty() is False
    assert prov.dirty_decision_paths() == []


def test_frontend_and_tests_are_out_of_scope(repo):
    (repo / "frontend_pipeline/static/index.html").write_text("<p>changed\n")
    (repo / "frontend_pipeline/auth.py").write_text("x\n")
    (repo / "tests/test_x.py").write_text("def test(): assert 1\n")
    assert prov.working_tree_dirty() is False


@pytest.mark.parametrize("rel", [
    "src/institutional/live_alpha_lab/portfolio.py",
    "scripts/run_alpha_x_shadow.py",
    "configs/live_alpha_registry.yaml",
    "configs/validation_registry.yaml",
    "reports/live_alpha_lab/ALPHA_X/freeze_spec.json",
    "reports/live_alpha_lab/DEPLOYMENT_DECISIONS_2026-09-03.md",
])
def test_each_decision_code_path_dirties_the_stamp(repo, rel):
    (repo / rel).write_text("changed\n")
    assert prov.working_tree_dirty() is True
    assert prov.dirty_decision_paths() == [rel]
    assert len(prov.dirty_decision_paths_sha1()) == 16


def test_untracked_new_runner_counts_and_is_named(repo):
    """Un nouveau runner pas encore `git add` EST un changement de code."""
    (repo / "scripts/run_alpha_y_shadow.py").write_text("print(2)\n")
    assert prov.dirty_decision_paths() == ["scripts/run_alpha_y_shadow.py"]


def test_new_freeze_spec_of_a_new_alpha_counts(repo):
    (repo / "reports/live_alpha_lab/ALPHA_Y").mkdir()
    (repo / "reports/live_alpha_lab/ALPHA_Y/freeze_spec.json").write_text("{}\n")
    assert prov.working_tree_dirty() is True


def test_sha1_is_deterministic_and_order_independent(repo):
    (repo / "scripts/b.py").write_text("b\n")
    (repo / "scripts/a.py").write_text("a\n")
    h1 = prov.dirty_decision_paths_sha1()
    assert prov.dirty_decision_paths() == ["scripts/a.py", "scripts/b.py"]
    assert prov.dirty_decision_paths_sha1() == h1


def test_git_unavailable_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(prov, "_ROOT", tmp_path / "not_a_repo")
    assert prov.dirty_decision_paths() == [prov.GIT_STATUS_UNAVAILABLE]
    assert prov.working_tree_dirty() is True


def test_spec_provenance_carries_scope_marker_and_scalar_fields_only(repo, monkeypatch):
    registry = repo / "configs/live_alpha_registry.yaml"
    registry.write_text("alphas:\n  - alpha_id: ALPHA_X\n    version: 1\n")
    # via la fixture monkeypatch, PAS `prov._REGISTRY_PATH = ...` : une
    # affectation directe survit à ce test et ferait lire le registre jetable
    # (déjà supprimé avec tmp_path) à tout test suivant qui touche la
    # provenance -- une fuite d'état qui ne se manifesterait qu'à
    # l'ordonnancement près.
    monkeypatch.setattr(prov, "_REGISTRY_PATH", registry)
    d = prov.spec_provenance("ALPHA_X")
    assert d["working_tree_dirty_scope"] == "DECISION_CODE_V2"
    assert d["working_tree_dirty"] is True                      # le registre vient d'être édité
    assert isinstance(d["dirty_decision_paths_sha1"], str) and d["dirty_decision_paths_sha1"]
    # les runners font `df[k] = v` : tout doit être scalaire
    for k, v in d.items():
        assert not isinstance(v, (list, dict, set)), k


def test_real_repo_current_state_is_scoped_not_global():
    """Sur le vrai dépôt : reports/ est sale (churn) mais le drapeau ne doit
    dépendre que du périmètre. Ce test documente le comportement, il ne
    présume pas de l'état de l'arbre à l'instant t."""
    paths = prov.dirty_decision_paths()
    assert all(
        p == prov.GIT_STATUS_UNAVAILABLE or p.startswith(("src/", "scripts/", "configs/"))
        or p.endswith("freeze_spec.json") or "DEPLOYMENT_DECISIONS_" in p
        for p in paths
    ), paths
