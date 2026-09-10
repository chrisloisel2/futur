"""PROJECT_TRUTH.md is the repository's statement of what is real. These tests keep it
from drifting into optimism: the file must exist, must say there are zero validated
sleeves, and no mechanism whose verdict is terminal-negative may hold an active seal."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_project_truth_exists_and_states_zero_validated():
    text = (ROOT / "PROJECT_TRUTH.md").read_text(encoding="utf-8")
    assert "Validated sleeves: 0" in text
    assert "Capital deployable: **false.**" in text


def test_no_active_seal_for_cost_wall_or_rejected_mechanisms():
    active = {p.stem.split("_20")[0] for p in (ROOT / "sealed_forwards" / "active").glob("*.json")}
    for vj in (ROOT / "mechanisms").glob("*/results/verdict.json"):
        status = json.loads(vj.read_text()).get("status")
        if status in {"COST_WALL", "REJECTED", "OVERFIT", "DATA_BROKEN", "FORWARD_FAILED"}:
            assert vj.parents[1].name not in active, f"{vj.parents[1].name} is {status} but sealed"


def test_every_mechanism_has_a_spec():
    for d in (ROOT / "mechanisms").iterdir():
        if d.is_dir() and not d.name.startswith("_"):
            assert (d / "spec.json").exists(), f"{d.name} has no spec.json: it does not exist"


def test_legacy_freeze_forbids_live_use_of_old_alphas():
    text = (ROOT / "LEGACY_FREEZE.md").read_text(encoding="utf-8")
    assert "trading any old alpha" in text and "mock endpoints" in text
