"""h2_dataset_freeze: the gate refuses by default, the freeze is hashed and reproducible, and no alpha claim is written."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import h2_dataset_freeze as F  # noqa: E402
from data_lake.indices import h2_event_classifier as K  # noqa: E402


def test_the_gate_refuses_unless_both_conditions_hold():
    ok = {"eligible": 100, "dominant_blocker": None}
    assert F.decide(ok, {"actual_fee_known": True}, {"if_fee_and_capacity_resolved": {"eligible": 100}})["decision"] == "PREPARE_PREREGISTRATION"
    assert F.decide(ok, {"actual_fee_known": False}, {"if_fee_and_capacity_resolved": {"eligible": 100}})["decision"] == "NO_TEST"
    few = F.decide({"eligible": 79, "dominant_blocker": None}, {"actual_fee_known": True}, {"if_fee_and_capacity_resolved": {"eligible": 79}})
    assert few["decision"] == "NO_TEST" and "threshold 80" in few["blockers"][0]
    prec = F.decide({"eligible": 100, "dominant_blocker": K.UNKNOWN_PRECEDENCE}, {"actual_fee_known": True}, {"if_fee_and_capacity_resolved": {"eligible": 100}})
    assert prec["decision"] == "NO_TEST" and any("P9" in b for b in prec["blockers"])
    assert F.decide(ok, {"actual_fee_known": True}, {"if_fee_and_capacity_resolved": {"eligible": 100}})["budget_reopen_requested"] is True


def test_freeze_is_hashed_stable_and_declares_what_it_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FROZEN", tmp_path)
    a = F.freeze(); b = F.freeze()
    assert a["freeze_sha256"] == b["freeze_sha256"] and len(a["freeze_sha256"]) == 64     # le contenu, pas l'horodatage
    assert a["no_alpha_test"] is True and a["no_return_computed"] is True and a["capital_deployable"] is False
    assert a["decision"]["decision"] in ("NO_TEST", "PREPARE_PREREGISTRATION")
    assert len(a["classified"]) == 174 and set(a["source_manifests"]) == {c["event_id"] for c in a["classified"]}
    assert list(tmp_path.glob("freeze_*.json"))


def test_reports_state_the_decision_and_never_a_performance_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FROZEN", tmp_path / "f")
    fz = F.freeze()
    F.write_reports(fz, out_acq=tmp_path, out_pre=tmp_path)
    for f in ("H2_CLEAN_DATASET_FREEZE.md", "H2_CLEAN_DATASET_FREEZE.json", "H2_EVENT_CLASSIFICATION.md",
              "H2_FINAL_COVERAGE_MATRIX.csv", "H2_FINAL_COVERAGE_MATRIX.json"):
        assert (tmp_path / f).exists(), f
    md = (tmp_path / "H2_CLEAN_DATASET_FREEZE.md").read_text().lower()
    assert "capital_deployable" in md and "no alpha verdict" in md
    for banned in ("t-stat", "sharpe", "profit", "edge of"):
        assert banned not in md, banned


def test_the_current_state_is_a_refusal_with_a_named_cause():
    fz = F.freeze()
    d = fz["decision"]
    assert d["decision"] == "NO_TEST" and d["clean_events"] == 0
    assert any("read-only API key" in b for b in d["blockers"])
    assert d["would_be_clean_if_unblocked"] > d["threshold"]                # la porte s'ouvrirait si on levait les deux manques
    assert fz["projection"]["structural_classes"].get("TRUE_BINANCE_PERP_FIRST", 0) < 10   # tres peu de vrais premiers listings
