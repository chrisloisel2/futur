"""
Unit tests for forward-window label alignment (CRITICAL BUG FIX VALIDATION)

Tests that forward-window construction correctly computes max/min over [t+1, t+horizon]
and NOT some misaligned backward-looking window.

CRITICAL: These tests validate the fix for the most silent and dangerous bug that
was invalidating all performance metrics.
"""

import numpy as np
import pandas as pd
import pytest


def compute_forward_window_extremes_CORRECTED(
    high: pd.Series,
    low: pd.Series,
    horizon: int
) -> tuple[pd.Series, pd.Series]:
    """
    CORRECT forward-window construction for [t+1, t+horizon].

    Mathematical definition:
        fwd_high[t] = max(high[t+1], high[t+2], ..., high[t+horizon])
        fwd_low[t]  = min(low[t+1], low[t+2], ..., low[t+horizon])
    """
    # Forward series: shift(-1) gives us [t+1, t+2, ...]
    high_fwd = high.shift(-1)
    low_fwd = low.shift(-1)

    # Rolling window of size `horizon` on forward series
    fwd_high_rolled = high_fwd.rolling(window=horizon, min_periods=horizon).max()
    fwd_low_rolled = low_fwd.rolling(window=horizon, min_periods=horizon).min()

    # Shift back to align at t (where decision is made)
    fwd_high = fwd_high_rolled.shift(-(horizon - 1))
    fwd_low = fwd_low_rolled.shift(-(horizon - 1))

    return fwd_high, fwd_low


def compute_forward_window_extremes_OLD_BUG(
    high: pd.Series,
    low: pd.Series,
    horizon: int
) -> tuple[pd.Series, pd.Series]:
    """
    INCORRECT (OLD) forward-window construction.

    This is what the code was doing before the fix.
    """
    fwd_high = high.rolling(horizon).max().shift(-horizon)
    fwd_low = low.rolling(horizon).min().shift(-horizon)
    return fwd_high, fwd_low


class TestForwardWindowAlignment:
    """Test forward-window max/min alignment correctness."""

    def test_forward_window_manual_verification(self):
        """
        Manual verification: small synthetic example.

        Timeline:
        t=0: high=10, low=9
        t=1: high=12, low=10
        t=2: high=15, low=11
        t=3: high=11, low=10
        t=4: high=13, low=11

        At t=0, with horizon=3:
        - fwd_high should be max(high[1], high[2], high[3]) = max(12, 15, 11) = 15
        - fwd_low should be min(low[1], low[2], low[3]) = min(10, 11, 10) = 10
        """
        df = pd.DataFrame({
            'high': [10, 12, 15, 11, 13, 14, 16],
            'low':  [9,  10, 11, 10, 11, 12, 13],
        })

        horizon = 3
        fwd_high, fwd_low = compute_forward_window_extremes_CORRECTED(
            df['high'], df['low'], horizon
        )

        # At t=0, fwd_high should be max(12, 15, 11) = 15
        assert fwd_high.iloc[0] == 15, f"Expected 15, got {fwd_high.iloc[0]}"

        # At t=1, fwd_high should be max(15, 11, 13) = 15
        assert fwd_high.iloc[1] == 15, f"Expected 15, got {fwd_high.iloc[1]}"

        # At t=0, fwd_low should be min(10, 11, 10) = 10
        assert fwd_low.iloc[0] == 10, f"Expected 10, got {fwd_low.iloc[0]}"

        # At t=1, fwd_low should be min(11, 10, 11) = 10
        assert fwd_low.iloc[1] == 10, f"Expected 10, got {fwd_low.iloc[1]}"

    def test_old_bug_produces_wrong_result(self):
        """Verify that the OLD (buggy) implementation produces DIFFERENT results."""
        df = pd.DataFrame({
            'high': [10, 12, 15, 11, 13, 14, 16],
            'low':  [9,  10, 11, 10, 11, 12, 13],
        })

        horizon = 3

        fwd_high_correct, fwd_low_correct = compute_forward_window_extremes_CORRECTED(
            df['high'], df['low'], horizon
        )
        fwd_high_bug, fwd_low_bug = compute_forward_window_extremes_OLD_BUG(
            df['high'], df['low'], horizon
        )

        # The buggy version should produce different values
        # (it computes backward window then shifts)
        assert not fwd_high_correct.equals(fwd_high_bug), \
            "Buggy version should produce different results than correct version"

    def test_no_lookahead_in_forward_window(self):
        """
        Critical test: Forward window at time t should NOT depend on any data >= t.

        We verify this by:
        1. Computing forward extremes at time t
        2. Changing data at t+1, t+2, ...
        3. Forward extremes MUST change (they depend on future)
        4. But forward extremes at t-1 (before the change) MUST NOT change
        """
        np.random.seed(42)
        n = 100
        high = pd.Series(np.random.uniform(90, 110, n))
        low = pd.Series(np.random.uniform(80, 90, n))

        horizon = 10
        t_anchor = 20  # Test at t=20

        # Compute original
        fwd_high_orig, fwd_low_orig = compute_forward_window_extremes_CORRECTED(
            high, low, horizon
        )
        orig_high_at_anchor = fwd_high_orig.iloc[t_anchor]
        orig_low_at_anchor = fwd_low_orig.iloc[t_anchor]

        # Modify future (t+21 onwards)
        high_modified = high.copy()
        low_modified = low.copy()
        high_modified.iloc[t_anchor + 1:t_anchor + horizon + 1] += 50  # big change

        # Recompute
        fwd_high_mod, fwd_low_mod = compute_forward_window_extremes_CORRECTED(
            high_modified, low_modified, horizon
        )
        mod_high_at_anchor = fwd_high_mod.iloc[t_anchor]

        # Forward extreme AT t_anchor MUST change (depends on future)
        assert abs(mod_high_at_anchor - orig_high_at_anchor) > 1.0, \
            "Forward window should depend on future data"

        # Forward extreme BEFORE t_anchor (at t_anchor-1) MUST NOT change
        # (does not depend on data after t_anchor)
        if t_anchor > horizon:
            orig_high_before = fwd_high_orig.iloc[t_anchor - 1]
            mod_high_before = fwd_high_mod.iloc[t_anchor - 1]
            assert abs(mod_high_before - orig_high_before) < 1e-9, \
                "Forward window at t-1 should NOT depend on future changes at t+1"

    def test_forward_window_respects_horizon_length(self):
        """Test that window contains exactly `horizon` candles from [t+1, t+horizon]."""
        df = pd.DataFrame({
            'high': [10, 20, 30, 40, 50, 60, 70, 80],
            'low':  [5,  10, 15, 20, 25, 30, 35, 40],
        })

        horizon = 3
        fwd_high, fwd_low = compute_forward_window_extremes_CORRECTED(
            df['high'], df['low'], horizon
        )

        # At t=0, should be max(high[1], high[2], high[3]) = max(20, 30, 40) = 40
        assert fwd_high.iloc[0] == 40

        # At t=1, should be max(high[2], high[3], high[4]) = max(30, 40, 50) = 50
        assert fwd_high.iloc[1] == 50

        # At t=2, should be max(high[3], high[4], high[5]) = max(40, 50, 60) = 60
        assert fwd_high.iloc[2] == 60


class TestRvFwdMeanAlignment:
    """Test rv_fwd_mean (forward volatility) alignment."""

    def test_rv_fwd_mean_alignment(self):
        """Verify rv_fwd_mean computes mean of |returns| over [t+1, t+horizon]."""
        close = pd.Series([100, 101, 99, 102, 98, 103, 97, 104])
        horizon = 3

        # Correct computation
        fwd_returns = close.pct_change().shift(-1)
        rv_fwd_rolled = fwd_returns.abs().rolling(window=horizon, min_periods=horizon).mean()
        rv_fwd_mean = rv_fwd_rolled.shift(-(horizon - 1))

        # At t=0, should be mean(|ret[1]|, |ret[2]|, |ret[3]|)
        # ret[1] = (99/101 - 1) = -0.0198
        # ret[2] = (102/99 - 1) = 0.0303
        # ret[3] = (98/102 - 1) = -0.0392
        # mean(abs) = (0.0198 + 0.0303 + 0.0392) / 3 = 0.0298
        expected = (abs(-0.0198) + abs(0.0303) + abs(-0.0392)) / 3

        assert abs(rv_fwd_mean.iloc[0] - expected) < 1e-3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
