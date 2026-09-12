"""alpha_zone_readiness: the default is NO_ALPHA_TEST, no zone allows a test, capital stays false, and the
ten questions are answered from measured inputs."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from data_lake.indices import alpha_zone_readiness as R


def test_default_decision_is_no_alpha_test_while_fees_unknown():
    g = R.gather(); z = R.zones(g)
    d = R.decide({**g, "execution": {**g["execution"], "fee_known": False}}, z)
    assert d["decision"] == "NO_ALPHA_TEST" and d["capital_deployable"] is False and d["budget"] == 0
    d2 = R.decide({**g, "execution": {**g["execution"], "fee_known": True}, "capacity": {**g["capacity"], "measured": 0}}, z)
    assert d2["decision"] == "NO_ALPHA_TEST"
    d3 = R.decide({**g, "execution": {**g["execution"], "fee_known": True}, "capacity": {**g["capacity"], "measured": g["capacity"]["n"]}}, z)
    assert d3["decision"] == "READY_FOR_PREREG_ONLY" and d3["capital_deployable"] is False        # jamais plus qu'un preenregistrement


def test_no_zone_allows_a_test_and_the_verdicts_are_the_prescribed_ones():
    doc = R.build()
    assert all(r["alpha_test_allowed"] is False for r in doc["readiness"])
    assert set(doc["executive_verdict"]) == set(R.ZONES)
    assert doc["executive_verdict"]["H2_POOLED"].startswith("dead") and doc["executive_verdict"]["OTHER_VENUE_FIRST"] == "closest research zone"
    assert doc["executive_verdict"]["MEXC_TO_BINANCE"] == "highest mechanistic interest" and "forward" in doc["executive_verdict"]["TRUE_FIRST_LISTING"]
    assert doc["final_decision"]["decision"] in R.DECISIONS and doc["capital_deployable"] is False and doc["no_alpha_test"] is True


def test_blockers_are_classified_and_the_credential_one_is_named():
    b = R.blockers(R.gather())
    assert all(x["kind"] in R.BLOCKER_KINDS for x in b)
    assert any(x["kind"] == "credential_blocker" and "read-only" in x["blocker"] for x in b)
    assert any(x["kind"] == "provider_blocker" for x in b) and any(x["kind"] == "forward_only_blocker" for x in b)


def test_reports_written_and_free_of_promotion_language(tmp_path):
    doc = R.build(); R.write_reports(doc, out=tmp_path)
    for f in ("ALPHA_ZONE_READINESS.md", "ALPHA_ZONE_READINESS.json", "ALPHA_ZONE_BLOCKERS.md", "ALPHA_ZONE_NEXT_DECISION.md", "ALPHA_ZONE_DATA_MAP.csv", "ALPHA_ZONE_DATA_MAP.json"):
        assert (tmp_path / f).exists(), f
    md = (tmp_path / "ALPHA_ZONE_READINESS.md").read_text()
    assert "## Executive verdict" in md and "## Readiness table" in md and "## Blockers" in md and "## Final decision" in md
    low = md.lower()
    for banned in ("deployable=true", "alpha found", "signal live", "bot ready", "budget reopened", "sharpe", "t-stat"):
        assert banned not in low, banned
    assert "capital_deployable: **false**" in md
    assert len(doc["answers"]) == 10 and "false" in doc["answers"]["10_capital_deployable_remains_false"].lower() or "yes" in doc["answers"]["10_capital_deployable_remains_false"]
