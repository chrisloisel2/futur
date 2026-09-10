"""P3B forced-flow first look: pure universe rules, fixed verdict order, seal refuses tampering, pins match."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_s = importlib.util.spec_from_file_location("ff_first_look", ROOT / "mechanisms" / "forced_liquidation_reaction_v1" / "first_look.py")
FF = importlib.util.module_from_spec(_s); _s.loader.exec_module(FF)
FREEZE = ROOT / "reports" / "first_look" / "forced_liquidation_reaction_v1_FREEZE.json"
UNIVERSE = ROOT / "reports" / "first_look" / "forced_liquidation_reaction_v1_UNIVERSE.json"


def test_direction_and_cost_are_pure_functions_of_the_record():
    assert FF.sign_of({"liquidated_position": "long"}) == -1 and FF.sign_of({"liquidated_position": "short"}) == 1
    assert FF.cost_bps({"spread_before_bps": 0.5}) == 14.5 and FF.ENTRY_DELAY_MS == 2000
    assert FF.HYPOTHESES["H1"]["primary"] == "30s" and FF.HYPOTHESES["H2"]["primary"] == "5m" and FF.N_FAMILY_TESTS == 2


def test_verdict_order_is_fixed():
    thr = 1.96; cost = 14.0
    good = {"n": 100, "mean_bps": 60.0, "median_bps": 20.0, "t": 3.0, "n_eff": 50, "top1_share": 0.05, "n_clusters": 40, "top_cluster_share": 0.10}
    assert FF.verdict_for({**good, "n": 10}, cost, thr)[0] == "INDECIDABLE"
    assert FF.verdict_for({**good, "mean_bps": 5.0}, cost, thr)[0] == "REJECTED_NO_GROSS"
    assert FF.verdict_for({**good, "mean_bps": 30.0}, cost, thr)[0] == "REJECTED_COST_WALL"
    assert FF.verdict_for({**good, "n_clusters": 15}, cost, thr)[0] == "INDECIDABLE"
    assert FF.verdict_for({**good, "top_cluster_share": 0.5}, cost, thr)[0] == "INDECIDABLE"
    assert FF.verdict_for(good, cost, thr)[0] == "FORWARD_SEAL_REQUIRED"


def test_synthetic_chain_recovers_injected_effect():
    r = FF.positive_control(n=40, effect_bps=40.0, seed=2)
    assert 0.75 * 40 <= r["recovered_bps"] <= 1.15 * 40 and r["null"]["verdict"] != "FORWARD_SEAL_REQUIRED"


def test_seal_refuses_tampering(monkeypatch, tmp_path):
    monkeypatch.setattr(FF, "FREEZE", tmp_path / "none.json")
    with pytest.raises(SystemExit, match="pas de FREEZE"):
        FF.check_seal()


@pytest.mark.skipif(not UNIVERSE.exists(), reason="universe not built")
def test_universe_is_metadata_only_and_bounded():
    u = json.loads(UNIVERSE.read_text())
    assert u["n_H1"] >= 30 and all(e["notional_usd"] >= 50_000 and e["bbo_age_ms"] >= 0 for e in u["events"])
    assert "ret" not in json.dumps(u["events"][0]) and all(e["in_H2"] == (e["notional_usd"] >= 250_000) for e in u["events"])


@pytest.mark.skipif(not FREEZE.exists(), reason="not sealed yet")
def test_freeze_pins_match_files_on_disk():
    fz = json.loads(FREEZE.read_text())
    for rel, expected in fz["pins"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == expected, f"{rel} modified after the seal"
    assert hashlib.sha256(UNIVERSE.read_bytes()).hexdigest() == fz["universe"]["sha256"]
