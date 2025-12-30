"""
Production Gates - Hard Fail Criteria
======================================

ZERO tolerance gates that must pass before ANY model goes to production.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Tuple, Optional
import numpy as np


@dataclass
class RegimeClassifierGates:
    """
    Hard gates for BINARY regime classifier (calm vs reversal).

    CRITICAL: impulse is NO LONGER a regime class.
    It has been moved to event detection (see impulse_detector.py).
    """

    # Macro metrics (BINARY classification)
    min_accuracy: float = 0.60  # Binary threshold (was 3-class accuracy ~46%)
    min_macro_f1: float = 0.55  # Raised for binary
    max_brier: float = 0.20

    # Per-class minimum recall (BINARY: calm, reversal)
    # REMOVED: min_impulse_recall (impulse is now an event, not a regime)
    min_calm_recall: float = 0.50  # Raised from 0.30
    min_reversal_recall: float = 0.50  # Raised from 0.35

    # Calibration (stricter for binary)
    max_ece: float = 0.10  # Relaxed slightly from 0.08 for initial rollout

    # Stability (adjusted for binary)
    min_entropy: float = 0.50  # Binary entropy range is smaller
    max_entropy: float = 0.75  # log(2) = 0.693 for perfect uniform binary

    def validate(self, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate metrics against gates (BINARY classification).

        Returns:
            (passed, reason_if_failed)
        """
        # Accuracy (new gate for binary)
        accuracy = metrics.get('accuracy', 0)
        if accuracy < self.min_accuracy:
            return False, f"Accuracy {accuracy:.3f} < {self.min_accuracy} (BINARY THRESHOLD)"

        # Macro F1
        macro_f1 = metrics.get('macro_f1', 0)
        if macro_f1 < self.min_macro_f1:
            return False, f"Macro F1 {macro_f1:.3f} < {self.min_macro_f1}"

        # Brier
        brier = metrics.get('brier', 1.0)
        if brier > self.max_brier:
            return False, f"Brier {brier:.4f} > {self.max_brier}"

        # Per-class recall (BINARY: calm, reversal only)
        recall = metrics.get('recall_per_class', {})

        calm_recall = recall.get('calm', 0)
        if calm_recall < self.min_calm_recall:
            return False, f"Calm recall {calm_recall:.3f} < {self.min_calm_recall} (CLASS COLLAPSE)"

        reversal_recall = recall.get('reversal', 0)
        if reversal_recall < self.min_reversal_recall:
            return False, f"Reversal recall {reversal_recall:.3f} < {self.min_reversal_recall} (CLASS COLLAPSE)"

        # REMOVED: impulse_recall check (impulse is now an event, not a regime)

        # ECE
        ece = metrics.get('ece', 1.0)
        if ece > self.max_ece:
            return False, f"ECE {ece:.4f} > {self.max_ece} (POOR CALIBRATION)"

        # Entropy bounds (adjusted for binary)
        entropy = metrics.get('entropy', 0)
        if entropy < self.min_entropy:
            return False, f"Entropy {entropy:.3f} < {self.min_entropy} (TOO CONFIDENT)"
        if entropy > self.max_entropy:
            return False, f"Entropy {entropy:.3f} > {self.max_entropy} (TOO UNIFORM)"

        return True, ""


@dataclass
class EdgeForecasterGates:
    """Hard gates for edge forecaster."""

    # Paper trading (CRITICAL)
    min_sharpe: float = 0.5
    min_roi: float = -0.05  # Allow small loss in warmup
    max_drawdown: float = -0.20

    # Prediction quality
    min_hit_rate: float = 0.51  # Must beat 50/50
    min_corr_q50_return: float = 0.10  # Directional signal

    # Quantile consistency
    min_monotonicity_rate: float = 0.99  # q05 <= q50 <= q95

    # Stability
    max_pred_std: float = 0.05  # Not too volatile
    min_pred_std: float = 0.001  # Not collapsed

    # Calibration
    max_brier_p_hit: float = 0.25

    def validate(self, metrics: Dict[str, Any], epoch: int, warmup_epochs: int = 3) -> Tuple[bool, str]:
        """
        Validate metrics against gates.

        Args:
            metrics: Metrics dict
            epoch: Current epoch
            warmup_epochs: Grace period before strict checks
        """
        is_warmup = epoch < warmup_epochs

        # ALWAYS check monotonicity (no warmup)
        mono_rate = metrics.get('monotonicity_rate', 0)
        if mono_rate < self.min_monotonicity_rate:
            return False, f"Monotonicity {mono_rate:.4f} < {self.min_monotonicity_rate} (QUANTILES BROKEN)"

        # ALWAYS check prediction variance
        pred_std = metrics.get('pred_std', 0)
        if pred_std > self.max_pred_std:
            return False, f"Pred std {pred_std:.4f} > {self.max_pred_std} (TOO VOLATILE)"
        if pred_std < self.min_pred_std:
            return False, f"Pred std {pred_std:.4f} < {self.min_pred_std} (COLLAPSED)"

        # After warmup: strict paper trading checks
        if not is_warmup:
            paper_test = metrics.get('paper_test_realistic', {})

            sharpe = paper_test.get('sharpe', 0)
            if sharpe < self.min_sharpe:
                return False, f"Sharpe {sharpe:.2f} < {self.min_sharpe} (UNPROFITABLE)"

            roi = paper_test.get('roi', 0)
            if roi < self.min_roi:
                return False, f"ROI {roi:.2%} < {self.min_roi:.2%} (LOSING MONEY)"

            max_dd = paper_test.get('max_drawdown', 0)
            if max_dd < self.max_drawdown:
                return False, f"MaxDD {max_dd:.2%} < {self.max_drawdown:.2%} (TOO RISKY)"

            hit_rate = paper_test.get('hit_rate', 0.5)
            if hit_rate < self.min_hit_rate:
                return False, f"Hit rate {hit_rate:.2%} < {self.min_hit_rate:.2%} (NO EDGE)"

        # Correlation check
        corr = metrics.get('corr_q50_return', 0)
        if corr < self.min_corr_q50_return:
            return False, f"Corr(q50, return) {corr:.3f} < {self.min_corr_q50_return} (NO SIGNAL)"

        # Calibration
        brier_p_hit = metrics.get('brier_p_hit', 1.0)
        if brier_p_hit > self.max_brier_p_hit:
            return False, f"Brier(p_hit) {brier_p_hit:.4f} > {self.max_brier_p_hit}"

        return True, ""


def check_quantile_monotonicity(q05: np.ndarray, q50: np.ndarray, q95: np.ndarray) -> float:
    """
    Check if quantiles are monotonic: q05 <= q50 <= q95.

    Returns:
        Fraction of samples that satisfy monotonicity
    """
    valid = (q05 <= q50) & (q50 <= q95)
    return float(valid.mean())


def compute_signal_correlation(predictions: np.ndarray, targets: np.ndarray) -> float:
    """
    Compute correlation between predictions and targets.

    Handles NaNs gracefully.
    """
    mask = ~(np.isnan(predictions) | np.isnan(targets))
    if mask.sum() < 10:
        return 0.0

    return float(np.corrcoef(predictions[mask], targets[mask])[0, 1])


def detect_class_collapse(class_predictions: np.ndarray, n_classes: int, threshold: float = 0.05) -> Tuple[bool, str]:
    """
    Detect if model is ignoring some classes (collapse).

    Args:
        class_predictions: Array of class indices
        n_classes: Expected number of classes
        threshold: Minimum fraction per class

    Returns:
        (has_collapse, message)
    """
    unique, counts = np.unique(class_predictions, return_counts=True)
    fractions = counts / len(class_predictions)

    for cls in range(n_classes):
        if cls not in unique:
            return True, f"Class {cls} NEVER predicted (complete collapse)"

        idx = np.where(unique == cls)[0][0]
        if fractions[idx] < threshold:
            return True, f"Class {cls} only {fractions[idx]:.2%} of predictions (< {threshold:.2%})"

    return False, ""


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    # Example: Regime classifier validation (BINARY)
    regime_gates = RegimeClassifierGates()

    metrics = {
        'accuracy': 0.68,  # Binary accuracy (>60% threshold)
        'macro_f1': 0.62,
        'brier': 0.18,
        'recall_per_class': {
            # REMOVED 'impulse' - now an event, not a regime
            'calm': 0.64,
            'reversal': 0.60
        },
        'ece': 0.08,
        'entropy': 0.65  # Binary entropy (log(2) = 0.693 max)
    }

    passed, reason = regime_gates.validate(metrics)
    print(f"Regime Classifier (Binary): {'✅ PASS' if passed else f'❌ FAIL - {reason}'}")

    # Example: Edge forecaster validation
    edge_gates = EdgeForecasterGates()

    metrics = {
        'monotonicity_rate': 0.995,
        'pred_std': 0.012,
        'corr_q50_return': 0.15,
        'brier_p_hit': 0.22,
        'paper_test_realistic': {
            'sharpe': 0.85,
            'roi': 0.08,
            'max_drawdown': -0.12,
            'hit_rate': 0.54
        }
    }

    passed, reason = edge_gates.validate(metrics, epoch=5, warmup_epochs=3)
    print(f"Edge Forecaster: {'✅ PASS' if passed else f'❌ FAIL - {reason}'}")
