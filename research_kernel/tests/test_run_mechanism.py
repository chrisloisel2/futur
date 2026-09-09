"""End to end: a synthetic mechanism through the whole pipeline.

The point is that the gates fire in the right order and that the status is a
consequence of the numbers rather than of a choice made in the runner.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from research_kernel.forward_seal import seal_forward
from research_kernel.ledger import RunLedger
from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.multiplicity import MultiplicityLedger
from research_kernel.run_mechanism import RunContext, run_mechanism
from research_kernel.verdict import VerdictStatus

RUN_TEMPLATE = '''
import numpy as np
import pandas as pd

from research_kernel.data_contracts import DataQualityReport
from research_kernel.run_mechanism import DataUnavailable, MechanismRun


def build(spec, ctx):
    mode = "{mode}"
    if mode == "no_data":
        raise DataUnavailable("the capture does not exist yet")

    rng = np.random.default_rng(11)
    n = {n}
    ts = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    symbols = np.array(["BTCUSDT", "ETHUSDT", "SOLUSDT"])[np.arange(n) % 3]
    gross = rng.normal({loc}, {scale}, n)
    decisions = pd.DataFrame(
        {{"timestamp": ts, "symbol": symbols, "side": 1, "gross_bps": gross}}
    )
    panel = pd.DataFrame(
        {{"timestamp": ts, "symbol": symbols, "fwd_bps": rng.normal(0.0, {scale}, n)}}
    )
    quality = DataQualityReport(dataset_id="synthetic", n_rows=n)
    if mode == "dirty":
        quality.violations.append("column 'x' is constant (0.0) - placeholder, not data")
    return MechanismRun(
        decisions=decisions,
        quality=quality,
        panel=panel,
        trades=decisions,
        measured_latency_ms={latency},
        manifests=["synthetic"],
    )
'''

SPEC = {
    "mechanism_id": "synthetic_v1",
    "hypothesis": (
        "A synthetic mechanism used to prove that the gates fire in order and that a "
        "status is a consequence of the numbers."
    ),
    "economic_reason": (
        "There is none: this exists only to exercise the pipeline, and it is labelled "
        "so that nobody mistakes it for a claim about a market."
    ),
    "data_sources": ["synthetic"],
    "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "timeframe": "1d",
    "horizon": "1d",
    "side_mode": "long_short",
    "entry_rule": {"always": True},
    "exit_rule": {"holding_days": 1},
    "cost_model": {"taker_fee_bps": 5.0, "spread_bps": 1.0, "maker_ratio": 0.0},
    "validation_window": {"start": "2024-01-01", "end": "2026-01-01"},
    "placebo_tests": ["direction_flip", "same_event_random_side"],
    "declustering_rule": {"method": "fixed_windows", "window_seconds": 86400},
    "multiplicity_family": "microstructure",
    "kill_criteria": {"placebo_percentile_lte": 95},
    "promotion_criteria": {"profit_factor_min": 1.2, "min_independent_events": 200},
    "declared_data_latency_ms": 100,
}


def make(tmp_path, mode="ok", n=600, loc=60.0, scale=30.0, latency=100.0, **spec_over):
    d = tmp_path / "mechanisms" / "synthetic_v1"
    (d / "results").mkdir(parents=True, exist_ok=True)
    spec = dict(SPEC)
    spec.update(spec_over)
    (d / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (d / "run.py").write_text(
        RUN_TEMPLATE.format(mode=mode, n=n, loc=loc, scale=scale, latency=latency),
        encoding="utf-8",
    )
    return d


def run(tmp_path, d, **kw):
    return run_mechanism(
        d / "spec.json",
        ctx=RunContext(now="2026-09-10T00:00:00Z"),
        multiplicity_ledger=MultiplicityLedger(tmp_path / "mult.json"),
        run_ledger=RunLedger(tmp_path / "runs.jsonl"),
        sealed_root=tmp_path / "sealed",
        placebo_draws=200,
        **kw,
    )


def test_missing_data_is_an_honest_answer_not_a_crash(tmp_path):
    v = run(tmp_path, make(tmp_path, mode="no_data"))
    assert v.status is VerdictStatus.DATA_BROKEN
    assert "does not exist yet" in v.decision


def test_dirty_data_stops_before_any_number_is_computed(tmp_path):
    v = run(tmp_path, make(tmp_path, mode="dirty"))
    assert v.status is VerdictStatus.DATA_BROKEN
    assert v.gross_edge_bps == 0.0


def test_latency_over_the_budget_rejects_however_good_the_history(tmp_path):
    v = run(tmp_path, make(tmp_path, loc=500.0, latency=30 * 3600 * 1000.0))
    assert v.status is VerdictStatus.REJECTED
    assert any("latency" in f for f in v.failed_gates)
    assert v.gross_edge_bps == 0.0  # cost was never even computed


def test_a_signal_under_the_cost_floor_is_a_cost_wall(tmp_path):
    v = run(tmp_path, make(tmp_path, loc=10.0, scale=5.0))
    assert v.status is VerdictStatus.COST_WALL
    assert v.net_edge_bps < v.gross_edge_bps
    assert any("gate 2 cost" in f for f in v.failed_gates)


def test_the_cost_wall_fires_before_robustness(tmp_path):
    v = run(tmp_path, make(tmp_path, loc=10.0, scale=5.0, n=50))
    assert v.status is VerdictStatus.COST_WALL
    assert not any("gate 3" in f for f in v.failed_gates)


def test_a_thin_sample_is_rejected_at_robustness(tmp_path):
    v = run(tmp_path, make(tmp_path, loc=200.0, scale=50.0, n=60))
    assert v.status is VerdictStatus.REJECTED
    assert any("n_independent" in f for f in v.failed_gates)


def test_noise_that_clears_the_cost_wall_still_fails_the_placebo(tmp_path):
    # a large mean with an enormous spread: it passes the cost floor on average
    # but does not beat its own sign-flip null
    v = run(tmp_path, make(tmp_path, loc=40.0, scale=4000.0, n=800))
    assert v.status in (VerdictStatus.OVERFIT, VerdictStatus.REJECTED)


def test_a_clean_mechanism_stops_at_promising_and_never_at_validated(tmp_path):
    v = run(tmp_path, make(tmp_path, loc=80.0, scale=40.0, n=800))
    assert v.status is VerdictStatus.PROMISING_NEEDS_FORWARD
    assert v.forward_seal_id is None
    assert "not an edge" in v.decision


def test_a_sealed_window_moves_it_to_sealed_forward_active(tmp_path):
    d = make(tmp_path, loc=80.0, scale=40.0, n=800)
    spec = MechanismSpec.from_json(d / "spec.json")
    seal_forward(
        spec,
        "2026-09-11",
        "2026-12-11",
        root=tmp_path / "sealed",
        now="2026-09-10T00:00:00Z",
    )
    v = run(tmp_path, d)
    assert v.status is VerdictStatus.SEALED_FORWARD_ACTIVE
    assert v.forward_seal_id == "synthetic_v1_2026-09-11"


def test_the_first_run_charges_a_trial_and_the_second_does_not(tmp_path):
    d = make(tmp_path, loc=80.0, scale=40.0, n=800)
    mult = MultiplicityLedger(tmp_path / "mult.json")
    run(tmp_path, d)
    first = mult.family_size("microstructure")
    run(tmp_path, d)
    assert mult.family_size("microstructure") == first == 1


def test_sensitivities_raise_the_bar_for_the_same_mechanism(tmp_path):
    plain = run(tmp_path, make(tmp_path, loc=80.0, scale=40.0, n=800))
    other = tmp_path / "two"
    other.mkdir()
    d2 = make(other, loc=80.0, scale=40.0, n=800, sensitivities={"a": 1, "b": 2})
    v2 = run_mechanism(
        d2 / "spec.json",
        ctx=RunContext(now="2026-09-10T00:00:00Z"),
        multiplicity_ledger=MultiplicityLedger(other / "mult.json"),
        run_ledger=RunLedger(other / "runs.jsonl"),
        sealed_root=other / "sealed",
        placebo_draws=200,
    )
    assert v2.multiplicity_adjusted_threshold > plain.multiplicity_adjusted_threshold


def test_every_run_writes_the_full_result_set(tmp_path):
    d = make(tmp_path, loc=80.0, scale=40.0, n=800)
    run(tmp_path, d)
    for name in (
        "verdict.md",
        "verdict.json",
        "metrics.json",
        "placebo.json",
        "cost_sensitivity.json",
        "decisions.parquet",
        "trades.parquet",
        "data_quality_report.json",
    ):
        assert (d / "results" / name).exists(), name


def test_every_run_is_recorded_in_the_chained_ledger(tmp_path):
    d = make(tmp_path, loc=10.0, scale=5.0)
    run(tmp_path, d)
    run(tmp_path, d)
    led = RunLedger(tmp_path / "runs.jsonl")
    assert led.count_runs("synthetic_v1") == 2
    assert led.verify()


def test_the_verdict_carries_provenance(tmp_path):
    v = run(tmp_path, make(tmp_path, loc=10.0, scale=5.0))
    assert v.rules_hash
    assert v.kernel_version
    assert v.created_at == "2026-09-10T00:00:00Z"


def test_the_metrics_file_reports_the_t_on_gross(tmp_path):
    d = make(tmp_path, loc=10.0, scale=5.0)
    run(tmp_path, d)
    metrics = json.loads((d / "results" / "metrics.json").read_text())
    assert metrics["t_stat_on"] == "gross"
    assert "t_stat_net_artefact" in metrics
