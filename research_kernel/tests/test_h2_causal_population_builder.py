import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from data_lake.indices import h2_causal_population_builder as C


def test_builder_composes_real_inputs_and_reports(tmp_path):
    d = C.build()
    assert len(d["rows"]) == 174 and d["no_return_computed"] is True
    s = d["summary"]; a = s["answers"]
    assert a["1_true_first_listings"] + a["2_mexc_first"] + a["3_other_venue_first_excluding_mexc"] <= 174
    assert a["2_mexc_first"] > a["1_true_first_listings"]                                  # H2 est surtout une population MEXC
    assert all(r["class"] in C.L.ALL_CLASSES for r in d["rows"])
    r = C.write_reports(d, out=tmp_path)
    for f in ("H2_CAUSAL_POPULATIONS.md", "H2_CAUSAL_POPULATIONS.json", "H2_CAUSAL_POPULATION_MATRIX.csv", "H2_CAUSAL_POPULATION_MATRIX.json"):
        assert (tmp_path / f).exists(), f
    md = (tmp_path / "H2_CAUSAL_POPULATIONS.md").read_text().lower()
    assert "not one population" in md and "no return" in md


def test_missing_capacity_input_degrades_to_not_computed(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "CAPACITY", tmp_path / "none.json")
    d = C.build()
    assert d["capacity_inputs"] == 0 and all(r["capacity_status"] == "NOT_COMPUTED" and not r["capacity_measured"] for r in d["rows"])
    assert all(C.L.BLK_CAPACITY in r["blockers"] for r in d["rows"])                       # capacite absente = bloqueur, jamais suppose
