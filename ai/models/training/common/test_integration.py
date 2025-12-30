"""
Test suite for the corrected architecture.

Validates:
1. Regime classifier (binary)
2. Impulse detector (event)
3. Meta-control (downscale)
4. Execution engine (MAKER/TAKER)
5. Full pipeline integration
"""

import numpy as np
import pandas as pd
import sys
from typing import Dict

# Import corrected modules
from regime_classifier_v2 import (
    train_calibrated_regime_classifier,
    evaluate_regime_classifier,
    production_gates,
)
from impulse_detector import ImpulseDetector
from impulse_gates import ImpulseGates, validate_impulse_production
from meta_control import MetaControl, MetaControlConfig
from execution_engine import ExecutionEngine, OrderType


def test_regime_classifier_binary():
    """Test 1: Regime classifier produces binary outputs."""
    print("=" * 80)
    print("TEST 1: Regime Classifier (Binary)")
    print("=" * 80)

    # Generate mock training data
    n_samples = 1000
    n_features = 10

    X_train = np.random.randn(n_samples, n_features)
    y_train = np.random.randint(0, 2, n_samples)  # Binary: 0=calm, 1=reversal

    # Train
    print("Training regime classifier...")
    clf = train_calibrated_regime_classifier(X_train, y_train, class_names=['calm', 'reversal'])

    # Evaluate
    X_val = np.random.randn(200, n_features)
    y_val = np.random.randint(0, 2, 200)

    metrics = evaluate_regime_classifier(clf, X_val, y_val, class_names=['calm', 'reversal'])

    print(f"Accuracy: {metrics['accuracy']:.3f}")
    print(f"Calm recall: {metrics['recall_per_class']['calm']:.3f}")
    print(f"Reversal recall: {metrics['recall_per_class']['reversal']:.3f}")
    print(f"ECE: {metrics['ece']:.3f}")

    # Check gates
    passed, msg = production_gates(metrics)
    print(f"Production gates: {'PASSED' if passed else 'FAILED'}")
    if not passed:
        print(f"  Reason: {msg}")

    # Assertions
    assert len(metrics['recall_per_class']) == 2, "Should have exactly 2 classes"
    assert 'impulse' not in metrics['recall_per_class'], "Impulse should not be a class"

    print("✓ Test 1 passed")
    print()


def test_impulse_detector():
    """Test 2: Impulse detector produces event scores."""
    print("=" * 80)
    print("TEST 2: Impulse Detector (Event)")
    print("=" * 80)

    detector = ImpulseDetector(threshold=0.7)

    # Test 1: Normal conditions (no impulse)
    is_impulse, score = detector.detect(
        timestamp=pd.Timestamp.now(),
        ret_1m=0.0001,  # Small return
        rv_60=0.01,
        volume=1000,
        volume_ma=1000,
        volume_std=100,
        spread_z=0.0,
    )

    print(f"Normal conditions: is_impulse={is_impulse}, score={score:.3f}")
    assert not is_impulse, "Should not detect impulse in normal conditions"
    assert score < 0.5, "Score should be low"

    # Test 2: Impulse conditions (large return + volume spike)
    is_impulse, score = detector.detect(
        timestamp=pd.Timestamp.now(),
        ret_1m=0.015,  # Large return (1.5%)
        rv_60=0.005,   # Low RV → high z-score
        volume=2000,   # Volume spike
        volume_ma=1000,
        volume_std=100,
        spread_z=2.0,  # Spread expansion
    )

    print(f"Impulse conditions: is_impulse={is_impulse}, score={score:.3f}")
    assert is_impulse, "Should detect impulse"
    assert score > 0.7, "Score should be high"

    # Test 3: Event metrics
    metrics = detector.get_event_metrics(total_days=1)
    print(f"Event metrics: {metrics}")
    assert metrics['impulse_frequency_per_day'] == 1.0, "Should have 1 event in 1 day"

    print("✓ Test 2 passed")
    print()


def test_meta_control_downscale():
    """Test 3: Meta-control applies impulse downscale."""
    print("=" * 80)
    print("TEST 3: Meta-Control (Impulse Downscale)")
    print("=" * 80)

    meta_control = MetaControl(config=MetaControlConfig(
        regime_mult_calm=1.0,
        regime_mult_reversal=0.7,
        impulse_hard_mult=0.3,
    ))

    timestamp = pd.Timestamp.now()
    base_size = 1.0

    # Test 1: Calm, no impulse
    output1 = meta_control.compute_position_size(
        timestamp=timestamp,
        base_size=base_size,
        regime='calm',
        impulse_score=0.1,
        is_impulse=False,
        recent_pnl=0.0,
    )

    print(f"Calm, no impulse:")
    print(f"  Size: {output1.position_size:.3f} (expected ~1.0)")
    print(f"  Multipliers: {output1.multipliers}")
    assert abs(output1.position_size - 1.0) < 0.1, "Size should be ~1.0"

    # Test 2: Reversal, impulse active
    output2 = meta_control.compute_position_size(
        timestamp=timestamp,
        base_size=base_size,
        regime='reversal',
        impulse_score=0.9,
        is_impulse=True,
        recent_pnl=0.0,
    )

    expected_size = base_size * 0.7 * 0.3  # regime * impulse
    print(f"Reversal + impulse:")
    print(f"  Size: {output2.position_size:.3f} (expected ~{expected_size:.3f})")
    print(f"  Multipliers: {output2.multipliers}")
    assert abs(output2.position_size - expected_size) < 0.05, f"Size should be ~{expected_size}"

    # Test 3: Cooldown after loss
    output3 = meta_control.compute_position_size(
        timestamp=timestamp,
        base_size=base_size,
        regime='calm',
        impulse_score=0.1,
        is_impulse=False,
        recent_pnl=-0.010,  # Large loss triggers cooldown
    )

    print(f"Cooldown (after loss):")
    print(f"  Size: {output3.position_size:.3f}")
    print(f"  In cooldown: {output3.in_cooldown}")
    print(f"  Multipliers: {output3.multipliers}")
    assert output3.in_cooldown, "Should be in cooldown after loss"
    assert output3.multipliers['cooldown'] == 0.5, "Cooldown mult should be 0.5"

    print("✓ Test 3 passed")
    print()


def test_execution_maker_taker_switch():
    """Test 4: Execution engine switches MAKER→TAKER during impulse."""
    print("=" * 80)
    print("TEST 4: Execution Engine (MAKER/TAKER Switch)")
    print("=" * 80)

    execution_engine = ExecutionEngine()

    # Test 1: Normal execution (LIMIT_MAKER)
    order1 = execution_engine.place_order(
        symbol='BTCUSDT',
        side='BUY',
        size=0.5,
        regime='calm',
        impulse_active=False,
        impulse_score=0.2,
        mid_price=50000.0,
    )

    print(f"Normal execution:")
    print(f"  Order type: {order1.order_type.value} (expected LIMIT_MAKER)")
    print(f"  Price: {order1.price}")
    assert order1.order_type == OrderType.LIMIT_MAKER, "Should use LIMIT_MAKER"
    assert order1.price is not None, "Should have limit price"

    # Test 2: Impulse execution (MARKET)
    order2 = execution_engine.place_order(
        symbol='BTCUSDT',
        side='SELL',
        size=0.5,
        regime='reversal',
        impulse_active=True,
        impulse_score=0.85,
        mid_price=50000.0,
    )

    print(f"Impulse execution:")
    print(f"  Order type: {order2.order_type.value} (expected MARKET)")
    print(f"  Price: {order2.price} (should be None for MARKET)")
    assert order2.order_type == OrderType.MARKET, "Should use MARKET during impulse"
    assert order2.price is None, "MARKET orders have no price"

    print("✓ Test 4 passed")
    print()


def test_full_pipeline_integration():
    """Test 5: Full pipeline integration."""
    print("=" * 80)
    print("TEST 5: Full Pipeline Integration")
    print("=" * 80)

    # Setup
    from sklearn.linear_model import LogisticRegression

    # Mock regime classifier
    regime_model = LogisticRegression()
    X_dummy = np.random.randn(100, 5)
    y_dummy = np.random.randint(0, 2, 100)
    regime_model.fit(X_dummy, y_dummy)

    impulse_detector = ImpulseDetector(threshold=0.7)
    meta_control = MetaControl()
    execution_engine = ExecutionEngine()

    # Simulate tick
    timestamp = pd.Timestamp.now()
    market_data = {
        'close': 50000.0,
        'volume': 1500,
        'ret_1m': 0.012,  # Large return
        'rv_60': 0.006,
        'volume_ma': 1000,
        'volume_std': 100,
        'spread_z': 1.5,
    }

    # Step 1: Regime
    regime_features = np.random.randn(5)
    regime_label = regime_model.predict([regime_features])[0]
    regime = 'calm' if regime_label == 0 else 'reversal'
    print(f"Regime: {regime}")

    # Step 2: Impulse
    is_impulse, impulse_score = impulse_detector.detect(
        timestamp=timestamp,
        ret_1m=market_data['ret_1m'],
        rv_60=market_data['rv_60'],
        volume=market_data['volume'],
        volume_ma=market_data['volume_ma'],
        volume_std=market_data['volume_std'],
        spread_z=market_data['spread_z'],
        regime=regime,
    )
    print(f"Impulse: is_impulse={is_impulse}, score={impulse_score:.3f}")

    # Step 3: Meta-control
    meta_output = meta_control.compute_position_size(
        timestamp=timestamp,
        base_size=1.0,
        regime=regime,
        impulse_score=impulse_score,
        is_impulse=is_impulse,
        recent_pnl=0.0,
    )
    print(f"Meta-control: size={meta_output.position_size:.3f}, multipliers={meta_output.multipliers}")

    # Step 4: Execution
    order = execution_engine.place_order(
        symbol='BTCUSDT',
        side='BUY',
        size=meta_output.position_size,
        regime=regime,
        impulse_active=is_impulse,
        impulse_score=impulse_score,
        mid_price=market_data['close'],
    )
    print(f"Execution: order_type={order.order_type.value}, size={order.size:.3f}")

    # Assertions
    if is_impulse:
        assert order.order_type == OrderType.MARKET, "Should use MARKET during impulse"
        assert meta_output.position_size < 1.0, "Should downscale during impulse"

    print("✓ Test 5 passed")
    print()


def run_all_tests():
    """Run all tests."""
    print("\n")
    print("*" * 80)
    print("RUNNING ALL TESTS")
    print("*" * 80)
    print()

    tests = [
        test_regime_classifier_binary,
        test_impulse_detector,
        test_meta_control_downscale,
        test_execution_maker_taker_switch,
        test_full_pipeline_integration,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
            print()

    print("*" * 80)
    print(f"TEST RESULTS: {passed} passed, {failed} failed")
    print("*" * 80)
    print()

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
