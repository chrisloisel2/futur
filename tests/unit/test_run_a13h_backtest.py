from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from run_a13h_backtest import _capacity_usd, _decile_members, _leg_weights, _regime_for, _spread_bps


def test_spread_bps_tiers_by_liquidity():
    assert _spread_bps(1_000_000_000) == 1.0
    assert _spread_bps(500_000_000) == 1.0
    assert _spread_bps(100_000_000) == 5.0
    assert _spread_bps(50_000_000) == 5.0
    assert _spread_bps(5_000_000) == 15.0
    assert _spread_bps(float("nan")) == 15.0


def test_capacity_is_one_percent_of_adv():
    assert _capacity_usd(100_000_000) == pytest.approx(1_000_000)
    assert _capacity_usd(float("nan")) == 0.0


def test_decile_members_splits_lowest_and_highest_n():
    row = pd.Series({"A": -3.0, "B": -2.0, "C": -1.0, "D": 0.0, "E": 1.0, "F": 2.0, "G": 3.0})
    bottom, top = _decile_members(row, n=2)
    assert set(bottom) == {"A", "B"}
    assert set(top) == {"F", "G"}


def test_decile_members_drops_nan_before_ranking():
    row = pd.Series({"A": float("nan"), "B": -1.0, "C": 1.0})
    bottom, top = _decile_members(row, n=1)
    assert list(bottom) == ["B"]
    assert list(top) == ["C"]


def test_leg_weights_are_equal_dollar_and_sum_to_gross():
    weights = _leg_weights(pd.Index(["A", "B", "C", "D"]), gross_per_leg=0.5)
    assert weights.abs().sum() == pytest.approx(0.5)
    assert (weights == 0.125).all()


def test_leg_weights_empty_symbols_returns_empty_series():
    weights = _leg_weights(pd.Index([]), gross_per_leg=0.5)
    assert len(weights) == 0


def test_regime_for_buckets_the_preregistered_years():
    assert _regime_for(pd.Timestamp("2020-06-01", tz="UTC")) == "2019-2020"
    assert _regime_for(pd.Timestamp("2022-12-31T23:59:59Z")) == "2022"
    assert _regime_for(pd.Timestamp("2026-01-01", tz="UTC")) == "2026"
    assert _regime_for(pd.Timestamp("2018-01-01", tz="UTC")) is None
    assert _regime_for(pd.Timestamp("2027-01-01", tz="UTC")) is None
