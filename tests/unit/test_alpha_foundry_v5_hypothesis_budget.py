import hashlib
import json

import pytest

from alpha_foundry_v5.hypothesis_budget import (
    HypothesisBudgetLedger,
    load_hypothesis_budget_manifest,
    require_lab_budget,
)


def _digest(payload):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _write_budget(path, budget=2, provenance="prov"):
    payload = {
        "version": 1,
        "support_audit_digest": "audit",
        "support_policy_digest": "policy",
        "feature_provenance_digest": provenance,
        "target_free": True,
        "labs": {
            "A3": {
                "support_verdict": "STRONG_SUPPORT",
                "max_hypothesis_tests": budget,
            }
        },
        "policy": "test",
    }
    payload["manifest_digest"] = _digest(payload)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return payload


def test_budget_manifest_is_integrity_checked_and_bound_to_provenance(tmp_path):
    path = tmp_path / "budget.json"
    raw = _write_budget(path, budget=2, provenance="p1")
    manifest = load_hypothesis_budget_manifest(str(path))
    assert manifest["manifest_digest"] == raw["manifest_digest"]
    assert require_lab_budget(manifest, "A3", "p1") == 2
    with pytest.raises(ValueError):
        require_lab_budget(manifest, "A3", "other")


def test_reservation_consumes_budget_before_completion(tmp_path):
    ledger = HypothesisBudgetLedger(str(tmp_path / "ledger.jsonl"))
    r1 = ledger.reserve("A3", "fam", "h1", "e1", "budget", 2)
    r2 = ledger.reserve("A3", "fam", "h2", "e2", "budget", 2)
    assert r1.action == "RESERVED"
    assert r2.action == "RESERVED"
    assert ledger.used("A3", "budget") == 2
    with pytest.raises(ValueError):
        ledger.reserve("A3", "other-family", "h3", "e3", "budget", 2)
    completed = ledger.complete("e1")
    assert completed.action == "COMPLETE"
    assert ledger.used("A3", "budget") == 2
    assert ledger.verify()["ok"] is True


def test_completion_without_reservation_is_refused(tmp_path):
    ledger = HypothesisBudgetLedger(str(tmp_path / "ledger.jsonl"))
    with pytest.raises(ValueError):
        ledger.complete("missing")


def test_budget_ledger_is_tamper_evident(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = HypothesisBudgetLedger(str(path))
    ledger.reserve("A3", "fam", "h1", "e1", "budget", 2)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["lab_id"] = "A4"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert ledger.verify()["ok"] is False
