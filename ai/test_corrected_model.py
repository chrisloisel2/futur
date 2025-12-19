#!/usr/bin/env python3
"""
test_corrected_model.py
Vérifie que les corrections mathématiques fonctionnent correctement
"""

import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

import sys
import numpy as np
import tensorflow as tf
from collections import Counter

# Add parent to path
sys.path.insert(0, '/Users/christopher/Desktop/futur')

from ai.models.model import make_windows, TinyRecursiveMarketModel, TRMConfig, FEATURE_KEYS

def test_corrected_model():
    """Test complet des corrections"""

    print("=" * 80)
    print("TEST DU MODÈLE CORRIGÉ")
    print("=" * 80)
    print()

    # Test data
    np.random.seed(42)
    T = 2000
    F = len(FEATURE_KEYS)
    X = np.random.randn(T, F).astype(np.float32)
    y_ret = np.random.randn(T).astype(np.float32) * 0.01
    y_rv = np.abs(np.random.randn(T).astype(np.float32)) * 0.01

    # Generate windows
    print("1. GÉNÉRATION DES WINDOWS")
    print("-" * 80)

    Xw, y_ret_h, y_dir, y_rv_agg = make_windows(
        X, y_ret, y_rv,
        lookback=256, horizon=12, stride=12
    )

    # Check shapes
    print(f"   Xw shape:      {Xw.shape} (attendu: [N, 256, 44])")
    print(f"   y_ret_h shape: {y_ret_h.shape} (attendu: [N, 12])")
    print(f"   y_dir shape:   {y_dir.shape} (attendu: [N,])")
    print(f"   y_rv_agg shape: {y_rv_agg.shape} (attendu: [N,])")
    print()

    assert Xw.ndim == 3, "Xw doit être 3D!"
    assert y_ret_h.ndim == 2, "y_ret_h doit être 2D!"
    assert y_dir.ndim == 1, "y_dir doit être 1D!"
    assert y_rv_agg.ndim == 1, "RV doit être 1D (scalar)!"

    print("   ✅ Toutes les shapes sont correctes")
    print()

    # Check direction is binary
    print("2. VÉRIFICATION DIRECTION BINAIRE")
    print("-" * 80)

    unique_dirs = np.unique(y_dir)
    print(f"   Valeurs uniques: {unique_dirs}")

    assert set(unique_dirs) == {0, 1}, f"Direction doit être binaire {{0, 1}}, trouvé: {set(unique_dirs)}"
    print("   ✅ Direction est binaire (0=DOWN, 1=UP)")
    print()

    # Check balance
    print("3. ÉQUILIBRE DES CLASSES")
    print("-" * 80)

    counts = Counter(y_dir)
    n_total = len(y_dir)
    balance_ratio = max(counts.values()) / min(counts.values())

    print(f"   DOWN (0): {counts[0]:>6} ({100*counts[0]/n_total:5.1f}%)")
    print(f"   UP (1):   {counts[1]:>6} ({100*counts[1]/n_total:5.1f}%)")
    print(f"   Ratio:    {balance_ratio:.2f}:1")

    if balance_ratio < 1.5:
        print("   ✅ Classes bien équilibrées")
    else:
        print(f"   ⚠️  Déséquilibre modéré (ratio={balance_ratio:.2f})")
    print()

    # Verify direction labels match cumulative returns
    print("4. COHÉRENCE LABELS vs RETURNS CUMULÉS")
    print("-" * 80)

    cum_rets = np.sum(y_ret_h, axis=1)
    expected_dirs = (cum_rets >= 0).astype(np.int32)  # UP=1 si cum>=0, DOWN=0 sinon

    match_rate = np.mean(y_dir == expected_dirs) * 100
    print(f"   Match rate: {match_rate:.1f}%")

    if match_rate == 100.0:
        print("   ✅ Labels correspondent exactement aux returns cumulés")
    else:
        print(f"   ❌ {100-match_rate:.1f}% de labels incorrects!")
        return False
    print()

    # Test model
    print("5. TEST DU MODÈLE")
    print("-" * 80)

    cfg = TRMConfig(
        lookback=256, horizon=12, stride=12,
        d_model=128, n_heads=4, d_ff=256, dropout=0.15,
        mem_dim=128, mem_update_iters=2,
        batch_size=32, lr=0.0003,
        w_ret=1.0, w_dir=0.8, w_rv=0.3
    )

    model = TinyRecursiveMarketModel(cfg=cfg, feature_dim=F)

    # Forward pass
    sample = Xw[:32]
    outputs = model(sample, training=False)

    print(f"   Outputs:")
    print(f"     ret: {outputs['ret'].shape} (attendu: [32, 12])")
    print(f"     dir: {outputs['dir'].shape} (attendu: [32, 2])")
    print(f"     rv:  {outputs['rv'].shape} (attendu: [32,])")
    print()

    assert outputs['ret'].shape == (32, 12), f"Returns shape incorrect: {outputs['ret'].shape}"
    assert outputs['dir'].shape == (32, 2), f"Direction doit être [B, 2] pour binaire, trouvé: {outputs['dir'].shape}"
    assert outputs['rv'].shape == (32,), f"RV doit être [B,] scalaire, trouvé: {outputs['rv'].shape}"

    print("   ✅ Outputs du modèle corrects")
    print()

    # Verify direction outputs sum to 1 (probabilities)
    dir_probs = outputs['dir'].numpy()
    prob_sums = np.sum(dir_probs, axis=1)
    print(f"   Somme des probabilités direction:")
    print(f"     Min: {prob_sums.min():.6f}, Max: {prob_sums.max():.6f}")

    if np.allclose(prob_sums, 1.0, atol=1e-5):
        print("   ✅ Probabilités valides (somme=1)")
    else:
        print("   ❌ Probabilités invalides!")
        return False
    print()

    # Compile test
    print("6. TEST DE COMPILATION")
    print("-" * 80)

    optimizer = tf.keras.optimizers.AdamW(learning_rate=0.0003)

    losses = {
        "ret": tf.keras.losses.Huber(delta=1.0),
        "dir": tf.keras.losses.BinaryCrossentropy(),  # Binary
        "rv": tf.keras.losses.Huber(delta=0.01),
    }

    metrics = {
        "ret": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
        "dir": [tf.keras.metrics.BinaryAccuracy(name="acc")],  # Binary
        "rv": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
    }

    try:
        model.compile(optimizer=optimizer, loss=losses, metrics=metrics)
        print("   ✅ Modèle compilé avec succès")
    except Exception as e:
        print(f"   ❌ Échec de compilation: {e}")
        raise
    print()

    # Test one training step
    print("7. TEST D'UN STEP D'ENTRAÎNEMENT")
    print("-" * 80)

    # Create small dataset
    ds = tf.data.Dataset.from_tensor_slices((
        Xw[:64],
        {
            "ret": y_ret_h[:64],
            "dir": y_dir[:64],
            "rv": y_rv_agg[:64]
        }
    )).batch(32)

    try:
        history = model.fit(ds, epochs=1, verbose=0)

        print("   Losses après 1 epoch:")
        for key, value in history.history.items():
            print(f"     {key}: {value[0]:.6f}")

        # Check losses are not NaN or Inf
        all_finite = all(np.isfinite(v[0]) for v in history.history.values())

        if all_finite:
            print("   ✅ Training step réussi (pas de NaN/Inf)")
        else:
            print("   ❌ NaN/Inf détecté dans les losses!")
            return False

    except Exception as e:
        print(f"   ❌ Échec training step: {e}")
        raise
    print()

    # Baseline direction accuracy test
    print("8. BASELINE DIRECTION ACCURACY")
    print("-" * 80)

    # Random predictor should get ~50% for balanced binary
    y_pred_random = np.random.randint(0, 2, size=len(y_dir))
    acc_random = np.mean(y_pred_random == y_dir) * 100

    print(f"   Random predictor: {acc_random:.1f}% (attendu: ~50%)")

    if 40 <= acc_random <= 60:
        print("   ✅ Random baseline dans la plage attendue")
    else:
        print(f"   ⚠️  Random baseline hors plage")

    # Statistical threshold
    n = len(y_dir)
    stat_threshold = 0.5 + 1.96 * np.sqrt(0.25 / n)
    min_acceptable = max(0.53, stat_threshold)

    print(f"   Seuil statistique (95%): {stat_threshold:.1%}")
    print(f"   Minimum acceptable:      {min_acceptable:.1%}")
    print()

    # Final summary
    print("=" * 80)
    print("✅ TOUS LES TESTS RÉUSSIS - MODÈLE CORRIGÉ!")
    print("=" * 80)
    print()
    print("Corrections appliquées:")
    print("  ✓ Direction: Binaire (UP/DOWN), suppression classe FLAT")
    print("  ✓ RV: Agrégée scalaire (RMS)")
    print("  ✓ Losses: BinaryCrossentropy + Huber avec clipping")
    print("  ✓ Architecture: 2 classes pour direction, scalar pour RV")
    print()
    print("Prochaine étape:")
    print("  python3 ai/train_advanced.py --config ai/configs/train_corrected.yaml")
    print()

    return True


if __name__ == "__main__":
    success = test_corrected_model()
    sys.exit(0 if success else 1)
