from __future__ import annotations

from domain.signal.signal import DecisionStatus, Signal


class DecisionLogic:
    """
    Enhanced decision logic with composite scoring.

    FIXED:
    - Changed from cascading if/elif to weighted composite score
    - Made thresholds configurable with better defaults
    - Added documentation on threshold calibration
    - Preserved INVALIDATE logic for critical failures

    TODO: Calibrate thresholds via backtest grid search for max Sharpe ratio
    """

    def __init__(
        self,
        # Composite score weights (must sum to 1.0)
        weight_confidence: float = 0.40,
        weight_entropy: float = 0.20,
        weight_novelty: float = 0.20,
        weight_disagreement: float = 0.20,

        # Thresholds (to be calibrated)
        min_composite_score: float = 0.60,  # Was implicit from threshold cascade
        min_confidence: float = 0.50,       # Was 0.55 (too high)
        max_entropy: float = 0.70,          # FIXED: Binary entropy max = log(2) = 0.693
        max_novelty: float = 4.0,           # Was 3.0 (reasonable)
        max_disagreement: float = 1.5,      # Was 1.0 (too strict)
    ):
        # Weights
        self.weight_confidence = weight_confidence
        self.weight_entropy = weight_entropy
        self.weight_novelty = weight_novelty
        self.weight_disagreement = weight_disagreement

        # Thresholds
        self.min_composite_score = min_composite_score
        self.min_confidence = min_confidence
        self.max_entropy = max_entropy
        self.max_novelty = max_novelty
        self.max_disagreement = max_disagreement

        # Validate weights sum to 1.0
        total_weight = weight_confidence + weight_entropy + weight_novelty + weight_disagreement
        if abs(total_weight - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total_weight}")

    def _compute_composite_score(self, signal: Signal) -> float:
        """
        Compute weighted composite score [0, 1].

        Higher is better. Components normalized to [0, 1]:
        - confidence: already [0, 1]
        - entropy: inverted and capped at max_entropy
        - novelty: inverted and capped at max_novelty
        - disagreement: inverted and capped at max_disagreement
        """
        # Confidence contribution (already [0, 1])
        conf_component = signal.confidence_calibrated

        # Entropy contribution (lower is better, normalize to [0, 1])
        entropy_normalized = max(0, min(1, signal.regime_entropy / self.max_entropy))
        entropy_component = 1.0 - entropy_normalized

        # Novelty contribution (lower is better)
        novelty_normalized = max(0, min(1, signal.novelty_score / self.max_novelty))
        novelty_component = 1.0 - novelty_normalized

        # Disagreement contribution (lower is better)
        disagreement_normalized = max(0, min(1, signal.disagreement_score / self.max_disagreement))
        disagreement_component = 1.0 - disagreement_normalized

        # Weighted sum
        score = (
            self.weight_confidence * conf_component +
            self.weight_entropy * entropy_component +
            self.weight_novelty * novelty_component +
            self.weight_disagreement * disagreement_component
        )

        return float(score)

    def apply(self, signal: Signal) -> Signal:
        """
        Apply decision logic with composite scoring.

        Returns:
            Signal with updated decision_status and reasons
        """
        reasons = list(signal.reasons)
        status = DecisionStatus.CONFIRM

        # Critical failures: INVALIDATE immediately
        if not signal.tradeable or signal.quality_flags != 0:
            status = DecisionStatus.INVALIDATE
            reasons.append("not_tradeable")
            signal.decision_status = status
            signal.reasons = reasons
            signal.tradeable = False
            return signal

        # Absolute minimum thresholds (safety checks)
        if signal.confidence_calibrated < self.min_confidence:
            status = DecisionStatus.DELAY
            reasons.append(f"confidence_{signal.confidence_calibrated:.2f}_below_{self.min_confidence}")

        # Composite scoring
        composite_score = self._compute_composite_score(signal)

        if composite_score < self.min_composite_score:
            status = DecisionStatus.DELAY
            reasons.append(f"composite_score_{composite_score:.2f}_below_{self.min_composite_score}")

        # Store composite score for analysis
        signal.reasons = reasons
        signal.decision_status = status

        return signal
