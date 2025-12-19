#!/usr/bin/env python3
"""Quick test du modèle corrigé"""
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

import sys
sys.path.insert(0, '/Users/christopher/Desktop/futur')

import numpy as np
import tensorflow as tf
from ai.models.model import make_windows, TinyRecursiveMarketModel, TRMConfig, FEATURE_KEYS

# Create test data
np.random.seed(42)
T = 2000
F = len(FEATURE_KEYS)
X = np.random.randn(T, F).astype(np.float32)
y_ret = np.random.randn(T).astype(np.float32) * 0.01
y_rv = np.abs(np.random.randn(T).astype(np.float32)) * 0.01

# Generate windows
Xw, y_ret_h, y_dir, y_rv_agg = make_windows(X, y_ret, y_rv, 256, 12, 12)

print(f"Shapes: Xw={Xw.shape}, y_ret_h={y_ret_h.shape}, y_dir={y_dir.shape}, y_rv_agg={y_rv_agg.shape}")
print(f"Direction values: {np.unique(y_dir)}")
print(f"Direction balance: DOWN={np.sum(y_dir==0)}, UP={np.sum(y_dir==1)}")

# Create model
cfg = TRMConfig(d_model=128, n_heads=4, lr=0.0003, w_dir=0.8, w_rv=0.3)
model = TinyRecursiveMarketModel(cfg=cfg, feature_dim=F)

# Test forward pass
out = model(Xw[:10], training=False)
print(f"\nOutputs: ret={out['ret'].shape}, dir={out['dir'].shape}, rv={out['rv'].shape}")

# Create dataset
ds = tf.data.Dataset.from_tensor_slices((
    Xw[:64],
    {"ret": y_ret_h[:64], "dir": y_dir[:64], "rv": y_rv_agg[:64]}
)).batch(32)

# Compile with CORRECT losses
optimizer = tf.keras.optimizers.AdamW(learning_rate=0.0003)

losses = {
    "ret": tf.keras.losses.Huber(delta=1.0),
    "dir": tf.keras.losses.SparseCategoricalCrossentropy(),  # Works with 2 classes!
    "rv": tf.keras.losses.Huber(delta=0.01),
}

metrics = {
    "ret": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
    "dir": [tf.keras.metrics.SparseCategoricalAccuracy(name="acc")],
    "rv": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
}

model.compile(optimizer=optimizer, loss=losses, metrics=metrics)
print("\n✅ Model compiled")

# Try one training step
print("\nTesting training step...")
history = model.fit(ds, epochs=1, verbose=1)

print("\n✅ ALL TESTS PASSED!")
print(f"Final losses: {history.history}")
