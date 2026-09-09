from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_kernel.validation import (
    compute_metrics,
    leave_one_out_stability,
    min_detectable_edge_bps,
    robustness_gate,
)

RULE = {"method": "fixed_windows", "window_seconds": 86400}


def frame(gross, n=None, symbol="BTCUSDT", freq="1D"):
    gross = np.asarray(gross, dtype=float)
    n = n or len(gross)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2021-01-01", periods=n, freq=freq, tz="UTC"),
            "symbol": symbol,
            "gross_bps": gross,
        }
    )


def test_the_t_is_computed_on_the_gross_series():
    rng = np.random.default_rng(0)
    df = frame(rng.normal(0.5, 227, 2355))
    m = compute_metrics(df, cost_bps=28.0, declustering_rule=RULE)
    assert abs(m["t_stat"]) < 2.5
    assert abs(m["t_stat_net_artefact"]) > 5.0
    assert m["t_stat_on"] == "gross"


def test_the_net_t_grows_with_n_on_pure_noise():
    rng = np.random.default_rng(1)
    small = compute_metrics(frame(rng.normal(0.0, 100, 300)), 20.0, RULE)
    big = compute_metrics(frame(rng.normal(0.0, 100, 5000)), 20.0, RULE)
    assert abs(big["t_stat_net_artefact"]) > abs(small["t_stat_net_artefact"])


def test_costs_are_subtracted_once_and_twice():
    m = compute_metrics(frame([10.0] * 300), cost_bps=4.0, declustering_rule=RULE)
    assert m["gross_edge_bps"] == pytest.approx(10.0)
    assert m["net_edge_bps"] == pytest.approx(6.0)
    assert m["net_edge_bps_cost_x2"] == pytest.approx(2.0)
    assert m["breakeven_capture"] == pytest.approx(0.4)


def test_breakeven_is_null_when_there_is_no_gross_edge():
    m = compute_metrics(frame([-1.0] * 300), cost_bps=4.0, declustering_rule=RULE)
    assert m["breakeven_capture"] is None


def test_an_infinite_profit_factor_fails_the_gate():
    m = compute_metrics(frame([5.0] * 300), cost_bps=1.0, declustering_rule=RULE)
    assert not np.isfinite(m["profit_factor"])
    failures = robustness_gate(m)
    assert any("infinite" in f for f in failures)


def test_the_gate_catches_a_thin_sample():
    rng = np.random.default_rng(2)
    m = compute_metrics(frame(rng.normal(30, 10, 50)), cost_bps=1.0, declustering_rule=RULE)
    assert any("n_independent" in f for f in robustness_gate(m))


def test_the_gate_catches_a_one_year_edge():
    rng = np.random.default_rng(3)
    good = rng.normal(40, 5, 365)
    flat = rng.normal(-2, 5, 730)
    df = frame(np.concatenate([good, flat]))
    m = compute_metrics(df, cost_bps=1.0, declustering_rule=RULE)
    failures = robustness_gate(m, min_independent=100)
    assert any("disappears without" in f for f in failures)


def test_the_gate_catches_a_one_symbol_edge():
    rng = np.random.default_rng(4)
    a = frame(rng.normal(60, 5, 400), symbol="BTCUSDT")
    b = frame(rng.normal(-5, 5, 400), symbol="ETHUSDT")
    m = compute_metrics(pd.concat([a, b], ignore_index=True), 1.0, RULE)
    failures = robustness_gate(m, min_independent=100)
    assert any("disappears without" in f for f in failures)


def test_a_clean_result_passes_every_robustness_check():
    rng = np.random.default_rng(5)
    frames = []
    for i, sym in enumerate(["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
        frames.append(frame(rng.normal(30, 20, 400), symbol=sym))
    m = compute_metrics(pd.concat(frames, ignore_index=True), 5.0, RULE)
    assert robustness_gate(m, min_independent=100) == []


def test_episodes_are_the_unit_of_statistics():
    df = frame([10.0] * 48, freq="1H")
    m = compute_metrics(df, cost_bps=1.0, declustering_rule=RULE)
    assert m["n_raw"] == 48
    assert m["n_independent"] == 2


def test_leave_one_out_needs_two_keys():
    assert leave_one_out_stability({"2024": {"n": 5, "net_bps_sum": 1.0}})["applicable"] is False


def test_min_detectable_edge():
    assert min_detectable_edge_bps(4.67, 3.25) == pytest.approx(15.18, abs=0.01)
