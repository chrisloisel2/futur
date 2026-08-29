from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from build_a13h_panel import rolling_causal_beta


def _synthetic_market_and_asset(n: int, seed: int) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    market = pd.Series(rng.normal(0, 0.01, n), index=idx)
    asset = pd.Series(1.5 * market.to_numpy() + rng.normal(0, 0.001, n), index=idx)
    return asset, market


def test_beta_at_t_is_unaffected_by_values_at_or_after_t():
    window = 50
    asset, market = _synthetic_market_and_asset(200, seed=0)
    ret = pd.DataFrame({"X": asset})
    beta_before = rolling_causal_beta(ret, market, window)

    perturbed_market = market.copy()
    perturbed_ret = ret.copy()
    t = 150
    perturbed_market.iloc[t] = 999.0
    perturbed_ret.iloc[t, 0] = 999.0
    beta_after = rolling_causal_beta(perturbed_ret, perturbed_market, window)

    # Every beta strictly before t must be bit-for-bit identical: none of them
    # may have looked at the perturbed value at t.
    pd.testing.assert_series_equal(beta_before["X"].iloc[:t], beta_after["X"].iloc[:t])
    # beta at t itself must also be unaffected -- it uses [t-window, t-1], not t.
    assert beta_before["X"].iloc[t] == beta_after["X"].iloc[t]
    # beta strictly after t (within window reach) must differ -- otherwise the
    # perturbation was never actually used by anything, which would just as
    # wrongly hide a bug the other direction.
    assert beta_before["X"].iloc[t + 1] != beta_after["X"].iloc[t + 1]


def test_beta_recovers_the_true_slope_on_a_noiseless_synthetic_series():
    idx = pd.date_range("2024-01-01", periods=200, freq="1h")
    rng = np.random.default_rng(1)
    market = pd.Series(rng.normal(0, 0.01, 200), index=idx)
    asset = pd.Series(2.0 * market.to_numpy(), index=idx)
    ret = pd.DataFrame({"X": asset})
    beta = rolling_causal_beta(ret, market, window=50)
    assert beta["X"].iloc[-1] == pytest.approx(2.0, abs=1e-9)


def test_beta_is_nan_until_the_window_is_fully_populated():
    asset, market = _synthetic_market_and_asset(60, seed=2)
    ret = pd.DataFrame({"X": asset})
    beta = rolling_causal_beta(ret, market, window=50)
    assert beta["X"].iloc[:50].isna().all()
    assert beta["X"].iloc[50:].notna().all()
