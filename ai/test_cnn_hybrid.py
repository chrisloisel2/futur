#!/usr/bin/env python3
"""
Test de l'architecture hybride CNN-Transformer
Vérifie les shapes et le nombre de paramètres
"""

import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

import sys
sys.path.insert(0, '/Users/christopher/Desktop/futur')

import numpy as np
import tensorflow as tf
from ai.models.model import TinyRecursiveMarketModel, TRMConfig, FEATURE_KEYS

print("="*80)
print("TEST ARCHITECTURE HYBRIDE CNN-TRANSFORMER")
print("="*80)

# Config
cfg = TRMConfig(
    lookback=256,
    horizon=12,
    d_model=128,
    n_heads=4,
    mem_dim=128,
    dropout=0.15
)

# Create model
model = TinyRecursiveMarketModel(cfg=cfg, feature_dim=len(FEATURE_KEYS))

# Dummy input
B, T, F = 32, 256, len(FEATURE_KEYS)
x_dummy = np.random.randn(B, T, F).astype(np.float32)

print(f"\n1. INPUT SHAPE:")
print(f"   x: {x_dummy.shape}")

# Forward pass
outputs = model(x_dummy, training=False)

print(f"\n2. OUTPUT SHAPES:")
print(f"   ret: {outputs['ret'].shape}  (expected: [{B}, {cfg.horizon}])")
print(f"   dir: {outputs['dir'].shape}  (expected: [{B}, 2])")
print(f"   rv:  {outputs['rv'].shape}  (expected: [{B}])")

# Assertions
assert outputs['ret'].shape == (B, cfg.horizon), f"Bad ret shape: {outputs['ret'].shape}"
assert outputs['dir'].shape == (B, 2), f"Bad dir shape: {outputs['dir'].shape}"
assert outputs['rv'].shape == (B,), f"Bad rv shape: {outputs['rv'].shape}"

print(f"\n✅ ALL SHAPES CORRECT!")

# Count parameters
total_params = model.count_params()
print(f"\n3. TOTAL PARAMETERS: {total_params:,}")

# Check CNN contribution
cnn_params = model.temporal_cnn.count_params()
print(f"   CNN parameters: {cnn_params:,} ({100*cnn_params/total_params:.1f}% of total)")

# Test training step
print(f"\n4. TEST TRAINING STEP:")
y_dummy = {
    'ret': np.random.randn(B, cfg.horizon).astype(np.float32) * 0.01,
    'dir': np.random.randint(0, 2, size=(B,)).astype(np.int32),
    'rv': np.abs(np.random.randn(B).astype(np.float32)) * 0.01
}

# Compile
optimizer = tf.keras.optimizers.AdamW(learning_rate=0.0003)
losses = {
    "ret": tf.keras.losses.Huber(delta=1.0),
    "dir": tf.keras.losses.SparseCategoricalCrossentropy(),
    "rv": tf.keras.losses.Huber(delta=0.01),
}
metrics = {
    "ret": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
    "dir": [tf.keras.metrics.SparseCategoricalAccuracy(name="acc")],
    "rv": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
}
model.compile(optimizer=optimizer, loss=losses, metrics=metrics)

# Create dataset
ds = tf.data.Dataset.from_tensor_slices((x_dummy, y_dummy)).batch(16)

# Train one step
print("   Running 1 training step...")
history = model.fit(ds, epochs=1, verbose=0)

print(f"   dir_acc: {history.history['dir_acc'][0]:.3f}")
print(f"   ret_mae: {history.history['ret_mae'][0]:.6f}")
print(f"   rv_mae: {history.history['rv_mae'][0]:.6f}")
print(f"   ✅ Training step successful")

print(f"\n✅ ARCHITECTURE TEST PASSED!")
print("="*80)
