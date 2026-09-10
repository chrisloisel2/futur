"""H2 made executable: the universe is metadata-only and disjoint from H1/H2, the verdict order is
fixed, the seal refuses tampering, and the FREEZE pins must match the files on disk."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("perp_fade_first_look", ROOT / "mechanisms" / "event_listing_perp_fade_v1" / "first_look.py")
PF = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(PF)
FREEZE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_FREEZE.json"
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"


def test_constants_are_the_prereg_ones():
    assert PF.SIDE == -1 and PF.ENTRY_OFFSET_MIN == 15 and PF.PRIMARY == "6h" and PF.SENSITIVITIES == ["60m", "24h"]
    assert PF.cost_rt("taker") == 24.0 and PF.cost_rt("maker") == 18.0 and PF.COST_WALL_X == 3.0
    assert PF.N_FAMILY_TESTS == 5 and PF.MIN_N == 30 and PF.MAX_TOP1_SHARE == 0.20 and PF.MIN_GROSS_MEDIAN_BPS == 30


def test_verdict_order_is_fixed():
    thr = 2.3263; cost = 24.0
    good = {"n": 100, "mean_bps": 150.0, "median_bps": 60.0, "t": 4.0, "n_eff": 60, "top1_share": 0.05, "capacity_median_quote_volume_6h_usd": 5e6}
    assert PF.verdict_for({**good, "n": 10}, 0.0, cost, thr)[0] == "INDECIDABLE"
    assert PF.verdict_for(good, 0.5, cost, thr)[0] == "INDECIDABLE"
    assert PF.verdict_for({**good, "capacity_median_quote_volume_6h_usd": None}, 0.0, cost, thr)[0] == "INDECIDABLE"
    assert PF.verdict_for({**good, "median_bps": 10.0}, 0.0, cost, thr)[0] == "REJECTED_NO_GROSS"
    assert PF.verdict_for({**good, "mean_bps": 80.0}, 0.0, cost, thr)[0] == "REJECTED_COST_WALL"     # net 56 < 72
    assert PF.verdict_for({**good, "t": 1.0}, 0.0, cost, thr)[0] == "INDECIDABLE"
    assert PF.verdict_for({**good, "capacity_median_quote_volume_6h_usd": 1e5}, 0.0, cost, thr)[0] == "INDECIDABLE"
    assert PF.verdict_for(good, 0.0, cost, thr)[0] == "FORWARD_SEAL_REQUIRED"


@pytest.mark.skipif(not UNIVERSE.exists(), reason="universe not built")
def test_universe_is_disjoint_from_h1_h2_and_launch_times_are_bounded():
    u = json.loads(UNIVERSE.read_text())
    prior = json.loads((ROOT / "mechanisms" / "event_reaction_v1" / "results" / "first_look_results.json").read_text())
    priced = {e["asset"] for k in ("H1", "H2") for e in prior["hypotheses"][k]["events"]}
    assert u["n_events"] >= 30
    assets = [e["asset"] for e in u["events"]]
    assert not (set(assets) & priced) and len(assets) == len(set(assets))
    for e in u["events"]:
        d = e["tradable_start_ms"] - e["publication_ts_ms"]
        assert -3600_000 <= d <= 7 * 86400_000
        assert "excess" not in e and "return" not in json.dumps(e)   # metadata only


def test_seal_refuses_tampering(monkeypatch, tmp_path):
    monkeypatch.setattr(PF, "FREEZE", tmp_path / "none.json")
    with pytest.raises(SystemExit, match="pas de FREEZE"):
        PF.check_seal()


@pytest.mark.skipif(not FREEZE.exists(), reason="not sealed yet")
def test_freeze_pins_match_files_on_disk():
    fz = json.loads(FREEZE.read_text())
    for rel, expected in fz["pins"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == expected, f"{rel} modified after the seal"
    assert hashlib.sha256(UNIVERSE.read_bytes()).hexdigest() == fz["universe"]["sha256"]
