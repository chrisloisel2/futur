"""h2_clean_dataset_builder: reads only what earlier phases wrote, never downloads, never computes a return."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import h2_clean_dataset_builder as B  # noqa: E402

SOURCE = (ROOT / "data_lake" / "indices" / "h2_clean_dataset_builder.py").read_text()


def test_the_builder_has_no_network_and_no_arithmetic_on_prices():
    for forbidden in ("urlopen", "requests", "http", "socket"):
        assert forbidden not in SOURCE.replace("https://", ""), forbidden
    for forbidden in ("return_bps", "math.log", "pnl", "sharpe"):
        assert forbidden not in SOURCE, forbidden


def test_missing_inputs_degrade_to_empty_not_to_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "VISION_MANIFESTS", tmp_path / "none"); monkeypatch.setattr(B, "BODIES", tmp_path / "none2")
    monkeypatch.setattr(B, "EXEC_REPORT", tmp_path / "none3.json"); monkeypatch.setattr(B, "PRECEDENCE", tmp_path / "none4.json")
    monkeypatch.setattr(B, "AFTER_CROSS", tmp_path / "none5.json")
    assert B.vision_index() == {} and B.body_index() == {} and B.precedence_index() == {}
    ex = B.execution_state(); assert ex["actual_fee_known"] is False and ex["report_present"] is False
    d = B.build()
    assert d["no_return_computed"] is True and len(d["events"]) == 174
    assert all(not e["market_state_core_complete"] and not e["actual_fee_known"] for e in d["events"])   # rien n'est suppose vrai


def test_real_inputs_are_composed():
    d = B.build()
    assert d["inputs"]["vision_manifests"] == 174 and d["inputs"]["bodies"] >= 174 and d["inputs"]["precedence"] == 174
    e = d["events"][0]
    assert e["vision_manifest_sha256"] and e["body_raw_hash"] and e["announced_start_ts"]
    assert e["binance_spot_existed_before"] is False                       # regle de l'univers, pas une supposition
    assert d["no_alpha_test"] is True
