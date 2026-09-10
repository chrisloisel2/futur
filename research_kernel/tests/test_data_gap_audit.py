"""P4 data-gap audit: machine-readable, complete, and it touches no verdict."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "reports" / "data_gap" / "DATA_EDGE_GAP_AUDIT.json"
PRIORITIES = {"P0_REQUIRED", "P1_HIGH_VALUE", "P2_NICE_TO_HAVE"}
DECISIONS = {"BUY", "BUILD", "IGNORE"}


def _audit():
    return json.loads(AUDIT.read_text(encoding="utf-8"))


def test_audit_is_complete_and_well_formed():
    a = _audit()
    assert a["constraints"] == {"price_look": False, "budget_consumed": 0, "budget_remaining": 0, "verdicts_modified": False, "horizons_modified": False, "new_trading_hypotheses": False}
    for fam, rows in a["field_audit"].items():
        assert rows, fam
        for r in rows:
            assert r["priority"] in PRIORITIES and r["decision"] in DECISIONS and r["status"] in {"HAVE", "PARTIAL", "MISSING"}, (fam, r["field"])
    for c in a["data_classes"]:
        assert c["decision"] in DECISIONS and c["priority"] in PRIORITIES and set(c["impact"]) == {"H2", "H3", "forced_flow"}, c["id"]
        if c["priority"] == "P0_REQUIRED":
            assert c.get("why_blocking"), c["id"]
    assert len(a["top5_obtain_now"]) == 5 and len(a["top5_ignore"]) == 5
    assert a["diagnosis"]["answer"] in {"breadth", "quality", "spectrum"}
    assert a["minimum_dataset_before_any_new_test"]["must_have"] and a["forbidden_data"]
    for p in ("DATA_EDGE_GAP_AUDIT.md", "H2_LISTING_FADE_DATA_REQUIREMENTS.md", "H3_DELISTING_DATA_REQUIREMENTS.md", "FORCED_FLOW_DATA_REQUIREMENTS.md", "DATA_ACQUISITION_PRIORITY.md"):
        assert (ROOT / "reports" / "data_gap" / p).exists(), p


def test_audit_did_not_touch_verdicts():
    a = _audit()
    for mid, recorded in a["verdicts_unchanged"].items():
        vj = ROOT / "mechanisms" / mid / "results" / "verdict.json"
        if vj.exists():
            assert json.loads(vj.read_text())["status"] == recorded.split(" ")[0], mid
    for vj in (ROOT / "mechanisms").glob("*/results/verdict.json"):
        assert vj.parents[1].name in a["verdicts_unchanged"], vj.parents[1].name
