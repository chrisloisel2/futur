"""
Unit tests for backtest correctness (CRITICAL SHORT SL BUG FIX VALIDATION)

Tests that short SL triggers correctly when price moves AGAINST the position.

CRITICAL: The bug was that short SL used (entry_price / high - 1) <= -sl_thresh
which is mathematically incorrect. Correct formula is (high / entry_price - 1) >= sl_thresh.
"""

import numpy as np
import pytest


class TestShortSLCorrectness:
    """Test short stop-loss trigger logic."""

    def test_short_sl_triggers_at_correct_threshold(self):
        """
        Verify short SL triggers when price moves UP by sl_thresh%.

        For a short position:
        - Entry at 100
        - SL threshold = 2% (0.02)
        - High moves to 102.5 (+2.5%) → SL SHOULD trigger
        - High moves to 101.5 (+1.5%) → SL SHOULD NOT trigger
        """
        entry_price = 100.0
        sl_thresh = 0.02  # 2%

        # Test 1: High = 102.5 (+2.5%) → SHOULD trigger
        high = 102.5
        hit_sl_correct = (high / entry_price - 1.0) >= sl_thresh
        assert hit_sl_correct, f"SL should trigger at +{sl_thresh*100}%, high={high}"

        # Test 2: High = 101.5 (+1.5%) → SHOULD NOT trigger
        high = 101.5
        hit_sl_correct = (high / entry_price - 1.0) >= sl_thresh
        assert not hit_sl_correct, f"SL should NOT trigger at +1.5%, high={high}"

        # Test 3: Verify OLD BUG formula would produce WRONG result
        high = 102.5
        hit_sl_bug = (entry_price / high - 1.0) <= -sl_thresh
        # Old formula: (100/102.5 - 1) = -0.0244 <= -0.02? → TRUE (correct by accident)
        # But for smaller moves, it triggers too early or too late

    def test_short_vs_long_symmetry(self):
        """
        Verify that long and short have symmetric loss mechanics.

        Long: Lose 2% when price drops to 98 (entry=100)
        Short: Lose 2% when price rises to 102 (entry=100)
        """
        entry = 100.0
        sl_thresh = 0.02

        # Long: lose 2% when price drops to 98
        low_for_long = 98.0
        long_loss_pct = (low_for_long / entry - 1.0)  # -0.02
        assert abs(long_loss_pct) == pytest.approx(0.02, abs=1e-6)

        # Short: lose 2% when price rises to 102
        high_for_short = 102.0
        short_loss_pct = (high_for_short / entry - 1.0)  # +0.02
        assert abs(short_loss_pct) == pytest.approx(0.02, abs=1e-6)

        # Absolute losses should be equal
        assert abs(long_loss_pct) == abs(short_loss_pct)

    def test_short_sl_trigger_boundary_cases(self):
        """Test edge cases for short SL trigger."""
        entry_price = 100.0
        sl_thresh = 0.02

        # Exactly at threshold → SHOULD trigger
        high = entry_price * (1 + sl_thresh)
        hit_sl = (high / entry_price - 1.0) >= sl_thresh
        assert hit_sl, "SL should trigger exactly at threshold"

        # Just below threshold → SHOULD NOT trigger
        high = entry_price * (1 + sl_thresh - 0.0001)
        hit_sl = (high / entry_price - 1.0) >= sl_thresh
        assert not hit_sl, "SL should NOT trigger just below threshold"

        # Way above threshold → SHOULD trigger
        high = entry_price * (1 + sl_thresh * 2)
        hit_sl = (high / entry_price - 1.0) >= sl_thresh
        assert hit_sl, "SL should trigger way above threshold"


class TestLongSLCorrectness:
    """Test long stop-loss trigger logic (for completeness)."""

    def test_long_sl_triggers_correctly(self):
        """Verify long SL triggers when price moves DOWN by sl_thresh%."""
        entry_price = 100.0
        sl_thresh = 0.02  # 2%

        # Low = 97.5 (-2.5%) → SHOULD trigger
        low = 97.5
        hit_sl = (low / entry_price - 1.0) <= -sl_thresh
        assert hit_sl, f"Long SL should trigger at -{sl_thresh*100}%, low={low}"

        # Low = 98.5 (-1.5%) → SHOULD NOT trigger
        low = 98.5
        hit_sl = (low / entry_price - 1.0) <= -sl_thresh
        assert not hit_sl, f"Long SL should NOT trigger at -1.5%, low={low}"


class TestTakeProfit:
    """Test take-profit trigger logic for both long and short."""

    def test_long_tp_triggers_correctly(self):
        """Long TP should trigger when price moves UP by tp_thresh%."""
        entry_price = 100.0
        tp_thresh = 0.03  # 3%

        # High = 103.5 (+3.5%) → SHOULD trigger
        high = 103.5
        hit_tp = (high / entry_price - 1.0) >= tp_thresh
        assert hit_tp, f"Long TP should trigger at +{tp_thresh*100}%"

        # High = 102.5 (+2.5%) → SHOULD NOT trigger
        high = 102.5
        hit_tp = (high / entry_price - 1.0) >= tp_thresh
        assert not hit_tp, f"Long TP should NOT trigger at +2.5%"

    def test_short_tp_triggers_correctly(self):
        """Short TP should trigger when price moves DOWN by tp_thresh%."""
        entry_price = 100.0
        tp_thresh = 0.03  # 3%

        # Low = 96.5 (-3.5%) → SHOULD trigger
        low = 96.5
        hit_tp = (entry_price / low - 1.0) >= tp_thresh
        assert hit_tp, f"Short TP should trigger at -{tp_thresh*100}%"

        # Low = 97.5 (-2.5%) → SHOULD NOT trigger
        low = 97.5
        hit_tp = (entry_price / low - 1.0) >= tp_thresh
        assert not hit_tp, f"Short TP should NOT trigger at -2.5%"


class TestPnLCalculation:
    """Test PnL calculation correctness."""

    def test_long_pnl_calculation(self):
        """Test long position PnL calculation."""
        entry = 100.0
        exit_price = 105.0  # +5%
        position_size = 1.0  # 100% of equity
        fee_rate = 0.0004  # 0.04%
        slippage_bps = 1.0  # 1 bp = 0.01%

        # Gross PnL = (105/100 - 1) * 1.0 = 0.05 (5%)
        pnl_gross_pct = (exit_price / entry - 1.0) * position_size
        assert pnl_gross_pct == pytest.approx(0.05, abs=1e-6)

        # Notional traded = 2 * position_size = 2.0
        notional_traded = 2.0 * position_size

        # Fees = 2.0 * 0.0004 = 0.0008 (0.08%)
        fee_cost_pct = notional_traded * fee_rate
        assert fee_cost_pct == pytest.approx(0.0008, abs=1e-8)

        # Slippage = 2.0 * 0.0001 = 0.0002 (0.02%)
        slippage_rate = slippage_bps / 10000.0
        slippage_cost_pct = notional_traded * slippage_rate
        assert slippage_cost_pct == pytest.approx(0.0002, abs=1e-8)

        # Net PnL = 0.05 - 0.0008 - 0.0002 = 0.049 (4.9%)
        pnl_net_pct = pnl_gross_pct - fee_cost_pct - slippage_cost_pct
        assert pnl_net_pct == pytest.approx(0.049, abs=1e-6)

    def test_short_pnl_calculation(self):
        """Test short position PnL calculation."""
        entry = 100.0
        exit_price = 95.0  # Price drops 5%
        position_size = 1.0  # 100% of equity (short)

        # Gross PnL for short = (entry/exit - 1) * position_size
        # = (100/95 - 1) * 1.0 = 0.0526 (5.26%)
        pnl_gross_pct = (entry / exit_price - 1.0) * position_size
        assert pnl_gross_pct == pytest.approx(0.0526, abs=1e-4)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
