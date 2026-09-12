import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from research_kernel.mechanisms import mexc_to_binance_migration_effect as M
from research_kernel.mechanism_spec import MechanismSpec


def test_spec_validates():
    s = MechanismSpec.from_dict(M.spec()); s.validate(); assert M.POPULATION == "MEXC_FIRST"


def test_only_mexc_first_with_a_tape_is_kept():
    rows = [{"event_id": "a", "population": "MEXC_FIRST", "blockers": [], "capacity_status": "UNKNOWN"},
            {"event_id": "b", "population": "MEXC_FIRST", "blockers": [], "capacity_status": "UNKNOWN"},
            {"event_id": "c", "population": "OKX_FIRST", "blockers": [], "capacity_status": "CAPACITY_OK"}]
    r = M.select(rows, {"a": "collected", "b": "not_collected"})
    assert [x["event_id"] for x in r["kept"]] == ["a"]
    reasons = {x["event_id"]: x["exclusion_reason"] for x in r["excluded"]}
    assert "not_collected" in reasons["b"] and "not MEXC_FIRST" in reasons["c"]


def test_no_verdict_and_conditioning_is_single():
    with pytest.raises(RuntimeError):
        M.verdict()
    assert "never two" in M.spec()["entry_rule"]["conditioning"]
