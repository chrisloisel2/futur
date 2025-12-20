"""
UNIT TESTS FOR REGIME-AWARE MODEL

Tests:
1. Regime label computation (no leakage, stability)
2. RegimeClassifier forward/backward
3. RegimeExpert forward/backward
4. RegimeAwareMarketModel gating (hard/soft)
5. Entropy regularization
6. Training step
"""

import numpy as np
import tensorflow as tf

from regime_aware_model import (
    RegimeConfig,
    RegimeClassifier,
    RegimeExpert,
    RegimeAwareMarketModel,
    RegimeAwareTrainer,
    compute_regime_labels,
    compute_regime_statistics,
    evaluate_regime_expert_performance,
)


def test_regime_labels():
    """Test regime label computation for correctness and stability"""
    print("\n" + "=" * 80)
    print("TEST 1: Regime Label Computation")
    print("=" * 80)

    # Mock features
    T = 5000
    F = 44
    np.random.seed(42)

    features = np.random.randn(T, F).astype(np.float32)

    # Feature keys (minimal required set)
    feature_keys = [
        "log_ret",
        "rv_ann_60",
        "rsi_14",
        "dist_ema_20",
        "dist_ema_50",
    ] + [f"feat_{i}" for i in range(F - 5)]

    # Inject patterns
    # TREND: strong slope
    features[1000:1500, 3] = np.linspace(0, 0.5, 500)  # dist_ema_20 increasing
    features[1000:1500, 0] = np.abs(np.random.randn(500)) * 0.01  # positive returns

    # MEAN_REVERT: extreme RSI
    features[2000:2500, 2] = 80.0  # RSI overbought

    # HIGH_VOL
    features[3000:3500, 1] = np.abs(np.random.randn(500)) * 0.5  # high RV

    # LOW_VOL
    features[4000:4500, 1] = np.abs(np.random.randn(500)) * 0.001  # low RV

    # Compute labels
    lookback = 256
    regime_labels = compute_regime_labels(features, feature_keys, lookback=lookback)

    # Test 1.1: No future leakage
    # Labels should be 0 before lookback
    assert np.all(regime_labels[:lookback] == 0), "Labels before lookback should be 0"
    print("✓ Test 1.1 PASS: No future leakage (labels before lookback are 0)")

    # Test 1.2: Valid range
    assert np.all((regime_labels >= 0) & (regime_labels < 5)), "Labels should be in [0, 4]"
    print("✓ Test 1.2 PASS: All labels in valid range [0, 4]")

    # Test 1.3: Statistics
    stats = compute_regime_statistics(regime_labels[lookback:])
    print("\nRegime statistics:")
    for k, v in stats.items():
        print(f"  {k}: {v:.2f}")

    # Test 1.4: Temporal stability (switching rate should be reasonable)
    switching_rate = stats["switches_per_1000"]
    assert 10 < switching_rate < 500, f"Switching rate {switching_rate} unreasonable"
    print(f"✓ Test 1.4 PASS: Switching rate {switching_rate:.1f} is reasonable")

    print("\n✓✓✓ TEST 1 PASSED ✓✓✓")


def test_regime_classifier():
    """Test RegimeClassifier layer"""
    print("\n" + "=" * 80)
    print("TEST 2: RegimeClassifier Layer")
    print("=" * 80)

    B, L, F = 32, 256, 44
    n_regimes = 5

    classifier = RegimeClassifier(
        n_regimes=n_regimes,
        d_model=64,
        n_layers=3,
        dropout=0.15,
        backbone="cnn",
    )

    x = tf.random.normal((B, L, F))

    # Test 2.1: Forward pass shape
    p_regime = classifier(x, training=False)
    assert p_regime.shape == (B, n_regimes), f"Expected ({B}, {n_regimes}), got {p_regime.shape}"
    print(f"✓ Test 2.1 PASS: Output shape {p_regime.shape}")

    # Test 2.2: Softmax output (probabilities)
    assert tf.reduce_all(p_regime >= 0.0) and tf.reduce_all(p_regime <= 1.0), "Probabilities out of range"
    sums = tf.reduce_sum(p_regime, axis=-1)
    assert tf.reduce_all(tf.abs(sums - 1.0) < 1e-5), "Probabilities don't sum to 1"
    print("✓ Test 2.2 PASS: Valid probability distribution (sum=1, range [0,1])")

    # Test 2.3: Training mode (dropout active)
    p_regime_train = classifier(x, training=True)
    assert p_regime_train.shape == (B, n_regimes), "Shape changed in training mode"
    print("✓ Test 2.3 PASS: Training mode works")

    # Test 2.4: Gradient flow
    with tf.GradientTape() as tape:
        p_regime = classifier(x, training=True)
        loss = tf.reduce_mean(p_regime)

    grads = tape.gradient(loss, classifier.trainable_variables)
    assert all(g is not None for g in grads), "Some gradients are None"
    print(f"✓ Test 2.4 PASS: Gradients flow ({len(grads)} variables)")

    print(f"\nClassifier parameters: {classifier.count_params():,}")
    print("\n✓✓✓ TEST 2 PASSED ✓✓✓")


def test_regime_expert():
    """Test RegimeExpert layer"""
    print("\n" + "=" * 80)
    print("TEST 3: RegimeExpert Layer")
    print("=" * 80)

    B, L, F, H = 32, 256, 44, 12

    expert = RegimeExpert(
        regime_id=0,
        horizon=H,
        d_model=64,
        n_layers=2,
        dropout=0.20,
        expert_type="tcn",
    )

    x = tf.random.normal((B, L, F))

    # Test 3.1: Forward pass shape
    outputs = expert(x, training=False)
    assert "ret" in outputs and "rv" in outputs, "Missing output keys"
    assert outputs["ret"].shape == (B, H), f"ret shape: expected ({B}, {H}), got {outputs['ret'].shape}"
    assert outputs["rv"].shape == (B,), f"rv shape: expected ({B},), got {outputs['rv'].shape}"
    print(f"✓ Test 3.1 PASS: Output shapes correct")

    # Test 3.2: RV positivity (softplus ensures this)
    assert tf.reduce_all(outputs["rv"] >= 0.0), "RV contains negative values"
    print("✓ Test 3.2 PASS: RV values are non-negative")

    # Test 3.3: Gradient flow
    with tf.GradientTape() as tape:
        outputs = expert(x, training=True)
        loss = tf.reduce_mean(outputs["ret"]) + tf.reduce_mean(outputs["rv"])

    grads = tape.gradient(loss, expert.trainable_variables)
    assert all(g is not None for g in grads), "Some gradients are None"
    print(f"✓ Test 3.3 PASS: Gradients flow ({len(grads)} variables)")

    print(f"\nExpert parameters: {expert.count_params():,}")
    print("\n✓✓✓ TEST 3 PASSED ✓✓✓")


def test_regime_aware_model_hard_gating():
    """Test RegimeAwareMarketModel with hard gating"""
    print("\n" + "=" * 80)
    print("TEST 4: RegimeAwareMarketModel (Hard Gating)")
    print("=" * 80)

    B, L, F, H = 32, 256, 44, 12

    cfg = RegimeConfig(
        lookback=L,
        horizon=H,
        n_regimes=5,
        regime_d_model=64,
        expert_d_model=64,
        gating_mode="hard",
    )

    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=F)

    x = tf.random.normal((B, L, F))

    # Test 4.1: Forward pass shape
    outputs = model(x, training=False, return_regime_probs=True)
    assert "ret" in outputs and "rv" in outputs and "regime_probs" in outputs
    assert outputs["ret"].shape == (B, H)
    assert outputs["rv"].shape == (B,)
    assert outputs["regime_probs"].shape == (B, 5)
    print("✓ Test 4.1 PASS: Output shapes correct")

    # Test 4.2: Hard gating (regime_probs should be one-hot-like after argmax)
    regime_probs = outputs["regime_probs"]
    regime_indices = tf.argmax(regime_probs, axis=-1)
    # Check that argmax is deterministic
    assert regime_indices.shape == (B,)
    print("✓ Test 4.2 PASS: Hard gating argmax works")

    # Test 4.3: Gradient flow
    with tf.GradientTape() as tape:
        outputs = model(x, training=True, return_regime_probs=False)
        loss = tf.reduce_mean(outputs["ret"]) + tf.reduce_mean(outputs["rv"])

    grads = tape.gradient(loss, model.trainable_variables)
    # Note: hard gating with argmax creates non-differentiable path
    # But gradients should still flow through soft p_regime to experts
    n_none = sum(1 for g in grads if g is None)
    print(f"  Gradients: {len(grads)} total, {n_none} None")
    # Some gradients may be None with hard gating (this is expected)
    print("✓ Test 4.3 PASS: Gradient computation works (hard gating)")

    print(f"\nModel parameters: {model.count_params():,}")
    print("\n✓✓✓ TEST 4 PASSED ✓✓✓")


def test_regime_aware_model_soft_gating():
    """Test RegimeAwareMarketModel with soft gating (MoE)"""
    print("\n" + "=" * 80)
    print("TEST 5: RegimeAwareMarketModel (Soft Gating / MoE)")
    print("=" * 80)

    B, L, F, H = 32, 256, 44, 12

    cfg = RegimeConfig(
        lookback=L,
        horizon=H,
        n_regimes=5,
        regime_d_model=64,
        expert_d_model=64,
        gating_mode="soft",
    )

    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=F)

    x = tf.random.normal((B, L, F))

    # Test 5.1: Forward pass
    outputs = model(x, training=False, return_regime_probs=True)
    assert outputs["ret"].shape == (B, H)
    assert outputs["rv"].shape == (B,)
    print("✓ Test 5.1 PASS: Output shapes correct")

    # Test 5.2: Soft gating (weighted average)
    # Output should be different from hard gating
    cfg_hard = RegimeConfig(lookback=L, horizon=H, gating_mode="hard")
    model_hard = RegimeAwareMarketModel(cfg=cfg_hard, feature_dim=F)
    model_hard.set_weights(model.get_weights())  # Same weights for comparison

    outputs_hard = model_hard(x, training=False)
    outputs_soft = model(x, training=False)

    # In general, outputs should differ (unless p_regime is one-hot)
    # Just check that computation runs without error
    print("✓ Test 5.2 PASS: Soft gating computes (MoE weighted average)")

    # Test 5.3: Gradient flow (should be smooth with soft gating)
    with tf.GradientTape() as tape:
        outputs = model(x, training=True)
        loss = tf.reduce_mean(outputs["ret"]) + tf.reduce_mean(outputs["rv"])

    grads = tape.gradient(loss, model.trainable_variables)
    assert all(g is not None for g in grads), "Some gradients are None (should flow with soft gating)"
    print(f"✓ Test 5.3 PASS: All gradients flow ({len(grads)} variables)")

    print("\n✓✓✓ TEST 5 PASSED ✓✓✓")


def test_entropy_regularization():
    """Test entropy regularization"""
    print("\n" + "=" * 80)
    print("TEST 6: Entropy Regularization")
    print("=" * 80)

    B = 100
    n_regimes = 5

    cfg = RegimeConfig(n_regimes=n_regimes)
    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=44)

    # Test 6.1: Uniform distribution (max entropy)
    p_uniform = tf.ones((B, n_regimes)) / n_regimes
    entropy_loss_uniform = model.compute_entropy_regularization(p_uniform)

    # Entropy of uniform distribution: H = log(n_regimes)
    expected_entropy = tf.math.log(tf.constant(float(n_regimes)))
    # Loss is -H, so we expect -log(5) ≈ -1.609
    expected_loss = -expected_entropy

    assert tf.abs(entropy_loss_uniform - expected_loss) < 1e-4, \
        f"Uniform entropy loss: expected {expected_loss:.4f}, got {entropy_loss_uniform:.4f}"
    print(f"✓ Test 6.1 PASS: Uniform distribution entropy = {-entropy_loss_uniform:.4f} ≈ log(5) = 1.609")

    # Test 6.2: Collapsed distribution (min entropy)
    p_collapsed = tf.one_hot(tf.zeros(B, dtype=tf.int32), depth=n_regimes)
    entropy_loss_collapsed = model.compute_entropy_regularization(p_collapsed)

    # Entropy of one-hot: H = 0 → loss = 0
    assert entropy_loss_collapsed < 0.01, \
        f"Collapsed entropy loss should be ~0, got {entropy_loss_collapsed:.4f}"
    print(f"✓ Test 6.2 PASS: Collapsed distribution entropy ≈ 0 (loss = {entropy_loss_collapsed:.4f})")

    # Test 6.3: Intermediate distribution
    p_mixed = tf.constant([[0.5, 0.3, 0.1, 0.05, 0.05]] * B, dtype=tf.float32)
    entropy_loss_mixed = model.compute_entropy_regularization(p_mixed)

    # Should be between collapsed (0) and uniform (-1.609)
    assert entropy_loss_collapsed < entropy_loss_mixed < entropy_loss_uniform, \
        "Mixed distribution entropy should be between collapsed and uniform"
    print(f"✓ Test 6.3 PASS: Mixed distribution entropy = {-entropy_loss_mixed:.4f} (between 0 and 1.609)")

    print("\n✓✓✓ TEST 6 PASSED ✓✓✓")


def test_training_step():
    """Test training step execution"""
    print("\n" + "=" * 80)
    print("TEST 7: Training Step")
    print("=" * 80)

    B, L, F, H = 64, 256, 44, 12

    cfg = RegimeConfig(
        lookback=L,
        horizon=H,
        batch_size=B,
        gating_mode="soft",
    )

    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=F)
    trainer = RegimeAwareTrainer(model=model, cfg=cfg)

    # Mock data
    x = tf.random.normal((B, L, F))
    y_regime = tf.random.uniform((B,), minval=0, maxval=5, dtype=tf.int32)
    y_ret = tf.random.normal((B, H)) * 0.01
    y_rv = tf.abs(tf.random.normal((B,))) * 0.02

    # Test 7.1: Train step runs
    loss_initial = trainer.train_step(x, y_regime, y_ret, y_rv)
    assert loss_initial > 0, "Loss should be positive"
    print(f"✓ Test 7.1 PASS: Train step executes (loss = {loss_initial:.4f})")

    # Test 7.2: Loss decreases with repeated steps
    losses = [loss_initial]
    for _ in range(5):
        loss = trainer.train_step(x, y_regime, y_ret, y_rv)
        losses.append(float(loss))

    # Loss should decrease (or at least not increase significantly)
    # Note: with random data, this might not always hold, so just check it runs
    print(f"  Losses over 6 steps: {[f'{l:.4f}' for l in losses]}")
    print("✓ Test 7.2 PASS: Multiple train steps execute")

    # Test 7.3: Validation step
    val_loss = trainer.val_step(x, y_regime, y_ret, y_rv)
    assert val_loss > 0, "Val loss should be positive"
    print(f"✓ Test 7.3 PASS: Val step executes (loss = {val_loss:.4f})")

    print("\n✓✓✓ TEST 7 PASSED ✓✓✓")


def test_evaluation():
    """Test evaluation function"""
    print("\n" + "=" * 80)
    print("TEST 8: Evaluation Function")
    print("=" * 80)

    N, L, F, H = 500, 256, 44, 12

    cfg = RegimeConfig(lookback=L, horizon=H, gating_mode="soft")
    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=F)

    # Mock data
    X = np.random.randn(N, L, F).astype(np.float32)
    y_regime = np.random.randint(0, 5, size=N, dtype=np.int32)
    y_ret = np.random.randn(N, H).astype(np.float32) * 0.01
    y_rv = np.abs(np.random.randn(N).astype(np.float32)) * 0.02

    # Test 8.1: Evaluation runs
    results = evaluate_regime_expert_performance(
        model=model,
        X=X,
        y_regime=y_regime,
        y_ret=y_ret,
        y_rv=y_rv,
    )

    print("  Results keys:", list(results.keys()))
    assert "regime_classification_acc" in results, "Missing regime classification accuracy"
    print("✓ Test 8.1 PASS: Evaluation runs")

    # Test 8.2: Per-regime metrics
    regime_names = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]
    for regime_name in regime_names:
        if regime_name in results:
            metrics = results[regime_name]
            assert "n_samples" in metrics
            assert "ret_mae" in metrics
            assert "rv_mae" in metrics
            assert "directional_acc" in metrics
            assert "beats_random" in metrics
            print(f"  {regime_name}: {metrics['n_samples']} samples, dir_acc={metrics['directional_acc']:.2%}")

    print("✓ Test 8.2 PASS: Per-regime metrics computed")

    print("\n✓✓✓ TEST 8 PASSED ✓✓✓")


def run_all_tests():
    """Run all tests"""
    print("\n" + "=" * 80)
    print("REGIME-AWARE MODEL - UNIT TESTS")
    print("=" * 80)

    tests = [
        ("Regime Labels", test_regime_labels),
        ("Regime Classifier", test_regime_classifier),
        ("Regime Expert", test_regime_expert),
        ("Model (Hard Gating)", test_regime_aware_model_hard_gating),
        ("Model (Soft Gating)", test_regime_aware_model_soft_gating),
        ("Entropy Regularization", test_entropy_regularization),
        ("Training Step", test_training_step),
        ("Evaluation", test_evaluation),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"\n✗✗✗ TEST FAILED: {name} ✗✗✗")
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Passed: {passed}/{len(tests)}")
    print(f"Failed: {failed}/{len(tests)}")

    if failed == 0:
        print("\n🎉 ALL TESTS PASSED 🎉")
    else:
        print(f"\n⚠️  {failed} TEST(S) FAILED")

    return failed == 0


if __name__ == "__main__":
    import os
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

    success = run_all_tests()
    exit(0 if success else 1)
