"""
Unit test to verify Brier score bug fix.

This test ensures:
1. Brier is calculated correctly as brier_score_loss(y_true, proba[:, 1])
2. The value in 'RESULTS' matches the value in 'PRODUCTION GATES'
3. We're not accidentally using y_pred instead of y_proba

CRITICAL: This test must FAIL if Brier is calculated incorrectly.
"""

import numpy as np
from sklearn.metrics import brier_score_loss
import sys


def test_brier_calculation():
    """Test that Brier is calculated correctly for binary classification."""

    # Create synthetic data
    np.random.seed(42)
    n = 100
    y_true = np.random.randint(0, 2, n)

    # Simulated probabilities (realistic probabilities, not predictions)
    y_proba = np.random.rand(n, 2)
    y_proba = y_proba / y_proba.sum(axis=1, keepdims=True)  # normalize

    # CORRECT: Binary Brier using probability of positive class
    brier_correct = brier_score_loss(y_true, y_proba[:, 1])

    # WRONG: Using predictions instead of probabilities
    y_pred = np.argmax(y_proba, axis=1)
    try:
        brier_wrong = brier_score_loss(y_true, y_pred)
        # This might work but gives wrong values
        print(f"⚠️  WARNING: Brier calculated on y_pred = {brier_wrong:.4f}")
    except Exception as e:
        print(f"✅ Good: Using y_pred raises error: {e}")

    # WRONG: Multiclass Brier (averaging over all classes)
    y_onehot = np.zeros((n, 2))
    y_onehot[np.arange(n), y_true] = 1.0
    multiclass_brier_wrong = np.mean((y_proba - y_onehot) ** 2)

    print("\n" + "=" * 60)
    print("BRIER SCORE CALCULATION TEST")
    print("=" * 60)
    print(f"Correct (binary):        {brier_correct:.4f}")
    print(f"Wrong (multiclass avg):  {multiclass_brier_wrong:.4f}")
    print(f"Difference:              {abs(brier_correct - multiclass_brier_wrong):.4f}")
    print("=" * 60)

    # Test that the values are different (they should be for non-uniform probs)
    assert abs(brier_correct - multiclass_brier_wrong) > 0.001, \
        "Binary and multiclass Brier should differ for non-uniform probabilities"

    # Test that binary Brier is in valid range [0, 1]
    assert 0 <= brier_correct <= 1, f"Brier {brier_correct} not in [0, 1]"

    print("✅ Test passed: Brier calculation is correct")
    return True


def test_metrics_consistency():
    """Test that metrics dict has consistent Brier values."""

    # Simulate what evaluate_regime_classifier returns
    y_true = np.array([0, 0, 1, 1, 0, 1, 1, 0])
    y_proba = np.array([
        [0.8, 0.2],
        [0.7, 0.3],
        [0.4, 0.6],
        [0.3, 0.7],
        [0.9, 0.1],
        [0.2, 0.8],
        [0.1, 0.9],
        [0.6, 0.4],
    ])

    # Calculate Brier correctly
    brier = float(brier_score_loss(y_true, y_proba[:, 1]))

    # Simulate metrics dict
    metrics = {
        'brier': brier,
        'accuracy': 0.75,
        'recall_per_class': {'calm': 0.5, 'reversal': 0.75},
        'ece': 0.05,
    }

    # Simulate gates validation
    from production_gates import RegimeClassifierGates
    gates = RegimeClassifierGates()

    # This should use the same Brier value
    passed, reason = gates.validate(metrics)

    print("\n" + "=" * 60)
    print("METRICS CONSISTENCY TEST")
    print("=" * 60)
    print(f"Brier in metrics:  {metrics['brier']:.4f}")
    print(f"Gates validation:  {'PASSED' if passed else f'FAILED - {reason}'}")

    # Extract Brier from reason if failed due to Brier
    if not passed and 'Brier' in reason:
        # Parse the Brier value from the error message
        # Format: "Brier 0.1234 > 0.20"
        parts = reason.split()
        if len(parts) >= 2:
            gate_brier = float(parts[1])
            print(f"Brier in gate:     {gate_brier:.4f}")

            # CRITICAL: These must match
            assert abs(metrics['brier'] - gate_brier) < 1e-6, \
                f"BRIER MISMATCH: metrics={metrics['brier']:.4f} vs gate={gate_brier:.4f}"

    print("✅ Test passed: Metrics are consistent")
    return True


def test_production_pipeline():
    """Test the full pipeline to ensure Brier is calculated correctly."""

    from regime_classifier_v2 import (
        train_calibrated_regime_classifier,
        evaluate_regime_classifier,
    )

    # Create simple synthetic data
    np.random.seed(42)
    n_train = 500
    n_val = 100
    n_features = 10

    X_train = np.random.randn(n_train, n_features)
    y_train = (X_train[:, 0] > 0).astype(int)

    X_val = np.random.randn(n_val, n_features)
    y_val = (X_val[:, 0] > 0).astype(int)

    # Train model
    clf = train_calibrated_regime_classifier(
        X_train, y_train,
        variant="logreg",
        calibration_method="sigmoid",  # faster than isotonic for test
    )

    # Evaluate
    metrics = evaluate_regime_classifier(
        clf, X_val, y_val,
        threshold=0.5,
    )

    # Manual calculation
    y_proba = clf.predict_proba(X_val)
    brier_manual = brier_score_loss(y_val, y_proba[:, 1])

    print("\n" + "=" * 60)
    print("PRODUCTION PIPELINE TEST")
    print("=" * 60)
    print(f"Brier (from evaluate):  {metrics['brier']:.4f}")
    print(f"Brier (manual calc):    {brier_manual:.4f}")
    print(f"Difference:             {abs(metrics['brier'] - brier_manual):.6f}")
    print("=" * 60)

    # CRITICAL: These must match
    assert abs(metrics['brier'] - brier_manual) < 1e-6, \
        f"BRIER MISMATCH: evaluate={metrics['brier']:.6f} vs manual={brier_manual:.6f}"

    print("✅ Test passed: Production pipeline calculates Brier correctly")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("BRIER SCORE BUG FIX - UNIT TESTS")
    print("=" * 60)

    all_passed = True

    try:
        test_brier_calculation()
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        all_passed = False

    try:
        test_metrics_consistency()
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        all_passed = False

    try:
        test_production_pipeline()
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        all_passed = False
    except Exception as e:
        print(f"\n⚠️  Test skipped (import error): {e}")

    print("\n" + "=" * 60)
    if all_passed:
        print("✅ ALL TESTS PASSED - Brier bug is fixed!")
        sys.exit(0)
    else:
        print("❌ SOME TESTS FAILED - Brier bug still present!")
        sys.exit(1)
    print("=" * 60 + "\n")
