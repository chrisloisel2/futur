import numpy as np
import pandas as pd

from research.alpha_discovery_v32.pipeline import (
    _calibration_selector,
    choose_dev_candidate,
    fit_month,
    make_month_fold,
    month_sequence,
)


def test_month_fold_is_strictly_chronological():
    ts = pd.Series(pd.date_range("2020-01-01", "2024-12-31 23:00", freq="h", tz="UTC"))
    fold = make_month_fold(ts, "2024-06")
    assert ts[fold.fit_mask].max() < ts[fold.calib_fit_mask].min()
    assert ts[fold.calib_fit_mask].max() < ts[fold.calib_select_mask].min()
    assert ts[fold.calib_select_mask].max() < ts[fold.test_mask].min()


def test_month_sequence_is_inclusive():
    assert month_sequence("2023-01", "2023-03") == ["2023-01", "2023-02", "2023-03"]


def test_calibration_selector_disables_losing_calibration():
    n = 500
    result = _calibration_selector(
        np.full(n, 0.01), np.full(n, 0.001), np.full(n, -0.01), 0.90
    )
    assert not bool(result["enabled"])


def test_calibration_selector_enables_real_edge():
    n = 500
    expected = np.linspace(0.002, 0.02, n)
    result = _calibration_selector(expected, np.full(n, 0.001), expected * 0.8, 0.90)
    assert bool(result["enabled"])
    assert result["threshold"] > 0


def test_isotonic_magnitude_calibration_can_shrink_overconfidence():
    from sklearn.isotonic import IsotonicRegression

    raw = np.linspace(0.01, 0.10, 100)
    actual = raw * 0.2
    iso = IsotonicRegression(y_min=0.0, increasing=True, out_of_bounds="clip").fit(raw, actual)
    assert np.mean(iso.predict(raw)) < np.mean(raw) * 0.3


def _synthetic_dataset():
    rng = np.random.default_rng(12)
    ts = pd.date_range("2020-01-01", "2024-12-31 23:00", freq="2h", tz="UTC")
    n = len(ts)
    signal = rng.normal(size=n)
    target = 0.006 * np.tanh(signal) + rng.normal(scale=0.001, size=n)
    sigma = np.full(n, 0.01)
    return pd.DataFrame({
        "timestamp": ts,
        "symbol": np.where(np.arange(n) % 3 == 0, "A", np.where(np.arange(n) % 3 == 1, "B", "C")),
        "f": signal,
        "target_residual_ret_1h": target,
        "target_standardized_1h": target / sigma,
        "ex_ante_sigma_1h": sigma,
        "decision_cost_x1": 0.0005,
        "realized_cost_x1": 0.0005,
        "realized_cost_x2": 0.001,
    })


def test_fit_month_recovers_known_oos_edge():
    data = _synthetic_dataset()
    fold = make_month_fold(data["timestamp"], "2024-06", fit_months=24, calib_days=120)
    result = fit_month(
        data, ["f"], fold, selection_quantile=0.70,
        max_train_rows=30000, max_calib_rows=5000, max_test_rows=5000,
    )
    assert result["status"] == "OK"
    assert result["ic_spearman"] > 0.5
    assert result["enabled_by_calibration"]
    assert result["net_x2_mean"] > 0


def _passing_summary():
    return {
        "status": "OK", "months_ok": 40, "months_enabled": 20, "selected_n": 1000,
        "pooled_net_x1_mean": 0.001, "pooled_net_x2_mean": 0.0005, "median_pf_x2": 1.2,
        "positive_net_x2_share": 0.65, "median_ic_spearman": 0.02,
        "median_brier_improvement": 0.001, "median_max_symbol_share": 0.7,
    }


def test_dev_candidate_gate_requires_x2_edge():
    good = _passing_summary()
    assert choose_dev_candidate({"A": good})["status"] == "CANDIDATE"
    bad = dict(good)
    bad["pooled_net_x2_mean"] = -0.0001
    assert choose_dev_candidate({"A": bad})["status"] == "NO_CANDIDATE"


def test_dev_candidate_gate_requires_breadth():
    bad = _passing_summary()
    bad["months_enabled"] = 5
    assert choose_dev_candidate({"A": bad})["status"] == "NO_CANDIDATE"
