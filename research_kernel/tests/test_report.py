from __future__ import annotations

import json
import math

import pandas as pd

from research_kernel.report import json_safe, render_verdict_md, write_json, write_parquet
from research_kernel.verdict import Verdict, VerdictStatus


def test_non_finite_numbers_are_written_as_null_not_as_infinity(tmp_path):
    p = write_json({"pf": float("inf"), "t": float("nan"), "ok": 1.5}, tmp_path / "m.json")
    payload = json.loads(p.read_text())
    assert payload["pf"] is None
    assert payload["t"] is None
    assert payload["ok"] == 1.5


def test_json_safe_walks_nested_structures():
    out = json_safe({"a": [1.0, float("inf")], "b": {"c": float("nan")}})
    assert out == {"a": [1.0, None], "b": {"c": None}}


def test_a_verdict_renders_its_gates_and_its_provenance():
    v = Verdict(
        mechanism_id="demo_v1",
        status=VerdictStatus.COST_WALL,
        gross_edge_bps=9.6,
        cost_bps=16.0,
        net_edge_bps=-6.4,
        passed_gates=["gate 0 data quality"],
        failed_gates=["gate 2 cost: gross 9.60 bps < 3 x cost 16.00 bps"],
        decision="Under the cost floor.",
        rules_hash="abc123",
        kernel_version="0.1.0",
    )
    text = render_verdict_md(
        v,
        {"gross_edge_bps": 9.6, "cost_bps": 16.0, "net_edge_bps": -6.4, "t_stat_net_artefact": -5.9},
        [{"test": "direction_flip", "status": "RUN", "percentile": 30.0}],
        [{"cost_multiplier": 1.0, "cost_bps": 16.0, "net_edge_bps": -6.4}],
        {"hypothesis": "H", "economic_reason": "R", "multiplicity_family": "microstructure",
         "horizon": "1d", "side_mode": "long_short", "universe": ["BTCUSDT"]},
    )
    assert "COST_WALL" in text
    assert "FAILED — gate 2 cost" in text
    assert "abc123" in text
    assert "measures the cost constant" in text


def test_an_infinite_profit_factor_reads_as_infinite_not_as_a_number():
    v = Verdict("demo_v1", VerdictStatus.REJECTED)
    text = render_verdict_md(
        v,
        {"profit_factor": float("inf")},
        [],
        [],
        {"hypothesis": "H", "economic_reason": "R", "universe": []},
    )
    assert "| profit factor | infinite |" in text


def test_parquet_round_trip(tmp_path):
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    p = write_parquet(df, tmp_path / "d.parquet")
    assert pd.read_parquet(p).equals(df)
