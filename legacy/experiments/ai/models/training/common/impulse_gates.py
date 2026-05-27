"""
Impulse Gates: Production monitoring and quality gates for impulse events.

CRITICAL: These are EVENT-LEVEL gates, not classification gates.
Impulse is not a regime, so accuracy/recall don't apply.

Gates monitor:
- Frequency (too rare → useless, too frequent → false positives)
- Conditional PnL (impulse should not correlate with losses)
- Execution cost (impulse should not cause excessive slippage)
- Drawdown correlation (impulse should not trigger during drawdowns)
"""

from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
import logging

logger = logging.getLogger(__name__)


class ImpulseGates:
    """
    Production gates for impulse event quality.

    Unlike classification gates, these monitor:
    - Event frequency
    - Conditional performance metrics
    - Operational impact (costs, drawdown)
    """

    def __init__(
        self,
        min_freq_per_day: float = 0.5,
        max_freq_per_day: float = 20.0,
        min_avg_pnl: float = -0.001,
        max_cost_multiplier: float = 2.0,
        max_drawdown_correlation: float = 0.01,
    ):
        """
        Args:
            min_freq_per_day: Minimum impulse frequency (too rare = useless)
            max_freq_per_day: Maximum impulse frequency (too frequent = false positives)
            min_avg_pnl: Minimum average PnL during impulse events
            max_cost_multiplier: Max execution cost vs normal (2x = double cost acceptable)
            max_drawdown_correlation: Max average drawdown during impulse
        """
        self.min_freq_per_day = min_freq_per_day
        self.max_freq_per_day = max_freq_per_day
        self.min_avg_pnl = min_avg_pnl
        self.max_cost_multiplier = max_cost_multiplier
        self.max_drawdown_correlation = max_drawdown_correlation

    def check_frequency_gate(
        self,
        impulse_metrics: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Gate 1: Frequency bounds.

        Too rare: impulse detection is not useful
        Too frequent: detector is too sensitive (false positives)
        """
        freq = impulse_metrics.get('impulse_frequency_per_day', 0.0)

        if freq < self.min_freq_per_day:
            return False, (
                f"IMPULSE TOO RARE: {freq:.2f}/day < {self.min_freq_per_day}/day. "
                "Lower threshold or disable module."
            )

        if freq > self.max_freq_per_day:
            return False, (
                f"IMPULSE TOO FREQUENT: {freq:.2f}/day > {self.max_freq_per_day}/day. "
                "Raise threshold or tighten detection."
            )

        return True, ""

    def check_pnl_gate(
        self,
        impulse_metrics: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Gate 2: Conditional PnL during impulse.

        Impulse events should NOT be systematically correlated with losses.
        If avg PnL during impulse is strongly negative, the detector is:
        - Triggering at wrong times, OR
        - The meta-control response is incorrect
        """
        avg_pnl = impulse_metrics.get('avg_pnl_during_impulse', None)

        if avg_pnl is None:
            logger.warning("PnL during impulse not available, skipping PnL gate")
            return True, ""

        if avg_pnl < self.min_avg_pnl:
            return False, (
                f"IMPULSE NEGATIVE PNL: avg_pnl={avg_pnl:.4f} < {self.min_avg_pnl:.4f}. "
                "Impulse correlated with losses. Review detection logic or meta-control."
            )

        return True, ""

    def check_execution_cost_gate(
        self,
        impulse_metrics: Dict[str, Any],
        normal_metrics: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Gate 3: Execution cost during impulse vs normal.

        Impulse may trigger TAKER orders (higher cost), but:
        - Cost should not explode (max 2x)
        - If cost is too high, may need to skip execution during impulse
        """
        impulse_cost = impulse_metrics.get('avg_execution_cost_impulse', None)
        normal_cost = normal_metrics.get('avg_execution_cost', None)

        if impulse_cost is None or normal_cost is None:
            logger.warning("Execution cost not available, skipping cost gate")
            return True, ""

        if normal_cost == 0:
            logger.warning("Normal execution cost is zero, skipping cost gate")
            return True, ""

        cost_ratio = impulse_cost / normal_cost

        if cost_ratio > self.max_cost_multiplier:
            return False, (
                f"IMPULSE COST TOO HIGH: {cost_ratio:.2f}x normal cost > "
                f"{self.max_cost_multiplier}x. Consider skipping execution during impulse."
            )

        return True, ""

    def check_drawdown_correlation_gate(
        self,
        impulse_metrics: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Gate 4: Drawdown correlation.

        WARNING (not hard failure): If impulse is correlated with drawdown:
        - Impulse detector may be triggering during adverse conditions
        - Meta-control should apply cooldown / leverage cap
        """
        # This gate uses events with drawdown metadata
        # If drawdown during impulse is high, issue warning
        # (This is typically computed post-hoc with PnL timeseries)

        # For now, we check if there's a 'drawdown_during_impulse' metric
        dd_impulse = impulse_metrics.get('avg_drawdown_during_impulse', None)

        if dd_impulse is None:
            logger.warning("Drawdown during impulse not available, skipping DD gate")
            return True, ""

        if dd_impulse > self.max_drawdown_correlation:
            logger.warning(
                f"IMPULSE CORRELATED WITH DRAWDOWN: avg_dd={dd_impulse:.4f} > "
                f"{self.max_drawdown_correlation:.4f}. Consider cooldown or leverage cap."
            )
            # This is a warning, not a hard failure
            return True, ""

        return True, ""

    def check_all(
        self,
        impulse_metrics: Dict[str, Any],
        normal_metrics: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Run all gates and return pass/fail + list of failures.

        Args:
            impulse_metrics: Event metrics from ImpulseDetector
            normal_metrics: Normal (non-impulse) metrics for comparison

        Returns:
            (all_passed, list_of_failures)
        """
        if normal_metrics is None:
            normal_metrics = {}

        gates = [
            self.check_frequency_gate(impulse_metrics),
            self.check_pnl_gate(impulse_metrics),
            self.check_execution_cost_gate(impulse_metrics, normal_metrics),
            self.check_drawdown_correlation_gate(impulse_metrics),
        ]

        failures = [msg for passed, msg in gates if not passed]
        all_passed = len(failures) == 0

        return all_passed, failures

    def __repr__(self) -> str:
        return (
            f"ImpulseGates(freq=[{self.min_freq_per_day}, {self.max_freq_per_day}]/day, "
            f"min_pnl={self.min_avg_pnl}, cost_mult={self.max_cost_multiplier})"
        )


def validate_impulse_production(
    impulse_metrics: Dict[str, Any],
    normal_metrics: Optional[Dict[str, Any]] = None,
    gates: Optional[ImpulseGates] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Convenience function to validate impulse metrics for production.

    Args:
        impulse_metrics: Metrics from ImpulseDetector.get_event_metrics()
        normal_metrics: Optional normal (non-impulse) metrics for comparison
        gates: Optional ImpulseGates instance (uses default if None)

    Returns:
        (passed, validation_report)

    Example:
        >>> from impulse_detector import ImpulseDetector
        >>> detector = ImpulseDetector()
        >>> # ... run detector on data ...
        >>> metrics = detector.get_event_metrics(total_days=30)
        >>> passed, report = validate_impulse_production(metrics)
        >>> if not passed:
        >>>     print("IMPULSE VALIDATION FAILED:", report['failures'])
    """
    if gates is None:
        gates = ImpulseGates()

    passed, failures = gates.check_all(impulse_metrics, normal_metrics)

    report = {
        'passed': passed,
        'failures': failures,
        'metrics': impulse_metrics,
        'gates_config': {
            'min_freq_per_day': gates.min_freq_per_day,
            'max_freq_per_day': gates.max_freq_per_day,
            'min_avg_pnl': gates.min_avg_pnl,
            'max_cost_multiplier': gates.max_cost_multiplier,
            'max_drawdown_correlation': gates.max_drawdown_correlation,
        },
    }

    if not passed:
        logger.error(f"IMPULSE PRODUCTION VALIDATION FAILED: {failures}")
    else:
        logger.info("IMPULSE PRODUCTION VALIDATION PASSED")

    return passed, report


# Example usage and testing
if __name__ == "__main__":
    # Example: Mock impulse metrics
    impulse_metrics = {
        'impulse_frequency_per_day': 5.2,
        'avg_score': 0.82,
        'avg_return_magnitude': 0.0035,
        'avg_pnl_during_impulse': 0.0002,  # Slightly positive
        'avg_execution_cost_impulse': 0.00015,
    }

    normal_metrics = {
        'avg_execution_cost': 0.00008,  # Normal cost lower
    }

    gates = ImpulseGates()
    passed, failures = gates.check_all(impulse_metrics, normal_metrics)

    print(f"Gates passed: {passed}")
    if failures:
        print("Failures:")
        for f in failures:
            print(f"  - {f}")

    # Full validation
    passed, report = validate_impulse_production(impulse_metrics, normal_metrics)
    print(f"\nFull validation: {passed}")
    print(f"Report: {report}")
