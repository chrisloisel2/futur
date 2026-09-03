"""Tests du ingest RESULTS.json -> validation_registry.yaml (wave 2)."""
import json
import re
from pathlib import Path

import pytest
import yaml

import scripts.ingest_validation_results as ing

SAMPLE = """candidates:

  # ══════════════════════════ WAVE 2 ══
  - candidate_id: FOO_BAR
    family: cross_sectional
    overlap_family: X
    economic_mechanism: >
      Deux lignes de
      mécanisme.
    current_status: VALIDATING
    validation_wave: 2
    validation_worker: V9
    existing_live_alpha: null
    validation_report: reports/edge_discovery/validation_2026-09/FOO_BAR/REPORT.md

  - candidate_id: NEXT_ONE
    family: liquidation
    current_status: VALIDATING
    validation_worker: V9

  # ══════════════════════════ AUTRES ══
  - candidate_id: OLD
    current_status: ALREADY_LIVE
"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    p = tmp_path / "validation_registry.yaml"
    p.write_text(SAMPLE)
    vdir = tmp_path / "validation_2026-09"
    (vdir / "FOO_BAR").mkdir(parents=True)
    monkeypatch.setattr(ing, "REGISTRY", p)
    monkeypatch.setattr(ing, "VALIDATION_DIR", vdir)
    monkeypatch.setattr(ing, "ROOT", tmp_path)
    return p, vdir


def _write_results(vdir, payload):
    (vdir / "FOO_BAR" / "RESULTS.json").write_text(json.dumps(payload))


def test_ingest_enriches_only_target_block(registry):
    p, vdir = registry
    _write_results(vdir, {"candidate_id": "FOO_BAR", "verdict": "VALIDATED_FOR_FORWARD",
                          "validation_net_bps": 12.5, "n_validation_independent": 321,
                          "eta_conservative": "2.1 years", "confirmable_in_horizon": True,
                          "year_by_year": {"2024": 10.0, "2025": 15.0},
                          "validation_caveats": "un caveat \"quoté\""})
    applied = ing.ingest()
    assert applied == ["FOO_BAR"]
    d = yaml.safe_load(p.read_text())
    by_id = {c["candidate_id"]: c for c in d["candidates"]}
    foo = by_id["FOO_BAR"]
    assert foo["current_status"] == "VALIDATED_FOR_FORWARD"
    assert foo["validated_for_forward"] is True
    assert foo["confirmable_in_horizon"] is True
    assert foo["validation_net_bps"] == 12.5
    assert foo["n_validation_independent"] == 321
    assert json.loads(foo["year_by_year"]) == {"2024": 10.0, "2025": 15.0}
    assert foo["validation_caveats"] == 'un caveat "quoté"'
    assert foo["economic_mechanism"].startswith("Deux lignes")
    assert foo["validation_worker"] == "V9"
    # blocs voisins intacts
    assert by_id["NEXT_ONE"]["current_status"] == "VALIDATING"
    assert "validated_for_forward" not in by_id["NEXT_ONE"]
    assert by_id["OLD"]["current_status"] == "ALREADY_LIVE"
    # commentaires de section préservés
    assert "# ══════════════════════════ AUTRES ══" in p.read_text()


def test_ingest_is_idempotent(registry):
    p, vdir = registry
    _write_results(vdir, {"candidate_id": "FOO_BAR", "verdict": "REJECTED",
                          "validation_net_bps": -3.0, "validation_caveats": "x"})
    ing.ingest()
    first = re.sub(r"validation_ingested_at: .*", "", p.read_text())
    ing.ingest()
    second = re.sub(r"validation_ingested_at: .*", "", p.read_text())
    assert first == second
    assert p.read_text().count("validation_net_bps:") == 1
    assert p.read_text().count("validation_caveats:") == 1


def test_secondary_verdicts_are_mapped(registry):
    p, vdir = registry
    _write_results(vdir, {"candidate_id": "FOO_BAR", "verdict": "UNCONFIRMABLE_IN_HORIZON"})
    ing.ingest()
    foo = {c["candidate_id"]: c for c in yaml.safe_load(p.read_text())["candidates"]}["FOO_BAR"]
    assert foo["current_status"] == "NEEDS_MORE_RESEARCH"
    assert foo["validation_verdict_raw"] == "UNCONFIRMABLE_IN_HORIZON"
    assert foo["validated_for_forward"] is False


def test_unknown_verdict_raises(registry):
    p, vdir = registry
    _write_results(vdir, {"candidate_id": "FOO_BAR", "verdict": "MAYBE"})
    with pytest.raises(ValueError):
        ing.ingest()
    assert "MAYBE" not in p.read_text()


def test_dry_run_does_not_write(registry):
    p, vdir = registry
    _write_results(vdir, {"candidate_id": "FOO_BAR", "verdict": "REJECTED"})
    before = p.read_text()
    ing.ingest(dry_run=True)
    assert p.read_text() == before


def test_list_and_keyed_results_shapes(registry, tmp_path):
    p, vdir = registry
    (vdir / "FOO_BAR" / "RESULTS.json").write_text(json.dumps(
        {"FOO_BAR": {"verdict": "REJECTED"}, "NEXT_ONE": {"verdict": "DATA_LIMITED"}}))
    applied = ing.ingest()
    assert sorted(applied) == ["FOO_BAR", "NEXT_ONE"]
    by_id = {c["candidate_id"]: c for c in yaml.safe_load(p.read_text())["candidates"]}
    assert by_id["NEXT_ONE"]["current_status"] == "DATA_BLOCKED"
