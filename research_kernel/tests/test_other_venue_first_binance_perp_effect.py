import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from research_kernel.mechanisms import other_venue_first_binance_perp_effect as M
from research_kernel.mechanism_spec import MechanismSpec


def test_spec_validates_and_is_research_only():
    s = MechanismSpec.from_dict(M.spec()); s.validate()
    assert M.STATUS == "RESEARCH_ONLY" and M.MECHANISM_ID.endswith("_v1") and "RESEARCH_ONLY" in M.spec()["notes"]


def test_true_first_and_unknown_are_excluded_by_rule():
    rows = [{"event_id": "a", "population": "MEXC_FIRST", "blockers": [], "capacity_status": "UNKNOWN"},
            {"event_id": "b", "population": "TRUE_BINANCE_PERP_FIRST", "blockers": [], "capacity_status": "CAPACITY_OK"},
            {"event_id": "c", "population": "UNKNOWN_PRECEDENCE", "blockers": [], "capacity_status": "CAPACITY_OK"},
            {"event_id": "d", "population": "OKX_FIRST", "blockers": ["BAD_TIMESTAMP"], "capacity_status": "CAPACITY_OK"},
            {"event_id": "e", "population": "BYBIT_FIRST", "blockers": [], "capacity_status": "NO_DEPTH"}]
    r = M.select(rows)
    assert [x["event_id"] for x in r["kept"]] == ["a"] and r["n_excluded"] == 4
    reasons = {x["event_id"]: x["exclusion_reason"] for x in r["excluded"]}
    assert "TRUE_BINANCE_PERP_FIRST" in reasons["b"] and "UNKNOWN_PRECEDENCE" in reasons["c"] and reasons["d"] == "BAD_TIMESTAMP" and "capacity" in reasons["e"]
    assert r["no_return_computed"] is True


def test_no_verdict_can_be_produced():
    with pytest.raises(RuntimeError, match="no verdict"):
        M.verdict([])
    assert M.EXCLUSIONS and M.FAILURE_MODES and len(M.QUESTIONS) == 6
