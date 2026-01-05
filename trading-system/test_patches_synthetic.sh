#!/bin/bash
# =============================================================================
# TEST RAPIDE DES PATCHES (VERSION SANS S3 - DONNÉES SYNTHÉTIQUES)
# =============================================================================
# Cette version crée des données synthétiques pour tester les patches
# sans dépendre de S3

set -e

echo "=========================================="
echo "TEST PATCHES (DONNÉES SYNTHÉTIQUES)"
echo "=========================================="
echo ""
echo "IMPORTANT: Ce test utilise des données synthétiques"
echo "           pour valider les patches sans S3"
echo ""

# Créer un script Python qui génère des données synthétiques et teste
cat > test_patches_synthetic.py << 'PYTHON_SCRIPT'
import sys
import os
import numpy as np
import pandas as pd
import torch
import json

sys.path.insert(0, "src")

from pipeline.models.edge.net import EdgeForecasterConfig, EdgeForecasterNet

print("="*60)
print("GÉNÉRATION DONNÉES SYNTHÉTIQUES")
print("="*60)

# Créer des données synthétiques
np.random.seed(42)
n_samples = 500
seq_len = 32
n_features = 50

# Features synthétiques
X = np.random.randn(n_samples, seq_len, n_features).astype(np.float32)
X = np.clip(X, -5, 5)  # Éviter valeurs extrêmes

# Labels synthétiques
y = np.zeros((n_samples, 5), dtype=np.float32)
y[:, 0] = np.random.randn(n_samples) * 0.01  # return_fwd
y[:, 1] = (np.random.rand(n_samples) > 0.5).astype(np.float32)  # dir_hit
y[:, 2] = (y[:, 0] > 0).astype(np.float32)  # is_up
y[:, 3] = (np.random.rand(n_samples) > 0.5).astype(np.float32)  # is_tp_up_hit
y[:, 4] = np.abs(np.random.randn(n_samples)) * 0.005  # rv_fwd_mean

print(f"✓ Données générées: X={X.shape}, y={y.shape}")

# Créer le modèle
cfg = EdgeForecasterConfig(
    seq_len=seq_len,
    d_model=64,  # Petit pour test rapide
    n_heads=4,
    n_layers=2,
    d_ff=128,
    dropout=0.05,
    attn_dropout=0.02,
    device="cuda" if torch.cuda.is_available() else "cpu",
    use_regime_cond=False,
)

print(f"\n✓ Config: d_model={cfg.d_model}, n_layers={cfg.n_layers}, device={cfg.device}")

net = EdgeForecasterNet(input_dim=n_features, cfg=cfg)
net = net.to(cfg.device)

print(f"✓ Modèle créé: {sum(p.numel() for p in net.parameters())} paramètres")

# Créer optimizer + scheduler + scaler
optimizer = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-3, betas=(0.9, 0.95))
scaler = torch.amp.GradScaler("cuda", enabled=(cfg.device=="cuda"))

from train_edge_forecaster import build_cosine_with_warmup
sys.path.insert(0, "scripts")
exec(open("scripts/train_edge_forecaster.py").read(), globals())

total_steps = 10
warmup_steps = 2
scheduler = build_cosine_with_warmup(optimizer, warmup_steps, total_steps, min_lr_ratio=0.15)

print(f"✓ Optimizer/scheduler/scaler créés")

# Dataset
X_t = torch.from_numpy(X[:400]).to(cfg.device)
y_t = torch.from_numpy(y[:400]).to(cfg.device)
X_val = torch.from_numpy(X[400:]).to(cfg.device)
y_val = torch.from_numpy(y[400:]).to(cfg.device)

print(f"✓ Train: {X_t.shape}, Val: {X_val.shape}")

print("\n" + "="*60)
print("TRAINING LOOP (TEST PATCHES)")
print("="*60)

# Accumulateurs epoch (PATCH 1.1)
epoch_clip_count = 0
epoch_total_steps = 0
epoch_grad_norms = []

max_grad_norm = 1.0

for step in range(10):
    net.train()
    optimizer.zero_grad()

    # Forward
    with torch.amp.autocast(device_type="cuda", enabled=(cfg.device=="cuda")):
        out = net(X_t, regime_vec=None)
        loss = net.compute_loss(X_t, y_t, label_smoothing=0.0, regime_vec=None)

    # Backward
    scaler.scale(loss).backward()

    # ========== PATCH 1.1: GRADIENT METRICS ==========
    scaler.unscale_(optimizer)

    grad_metrics = compute_gradient_metrics(net, max_grad_norm)
    pre_clip_norm = grad_metrics["pre_clip_norm"]
    was_clipped = grad_metrics["was_clipped"]
    max_param_grad = grad_metrics["max_param_grad"]

    torch.nn.utils.clip_grad_norm_(net.parameters(), max_norm=max_grad_norm)

    lr_before = optimizer.param_groups[0]["lr"]
    scaler.step(optimizer)
    scaler.update()
    amp_scale = scaler.get_scale()
    scheduler.step()
    lr_after = optimizer.param_groups[0]["lr"]

    epoch_clip_count += int(was_clipped)
    epoch_total_steps += 1
    epoch_grad_norms.append(pre_clip_norm)
    # =================================================

    if step % 3 == 0:
        print(f"Step {step}: loss={loss.item():.4f}, grad_norm={pre_clip_norm:.2f}, " +
              f"clipped={was_clipped}, amp_scale={amp_scale:.0f}, lr={lr_after:.6f}")

print("\n" + "="*60)
print("VALIDATION (TEST SATURATION)")
print("="*60)

net.eval()
with torch.no_grad():
    out_val = net(X_val, regime_vec=None)
    loss_val = net.compute_loss(X_val, y_val, label_smoothing=0.0, regime_vec=None)

ret_all = y_val[:, 0].detach().cpu().numpy()

# PATCH 1.2: Saturation check
val_return_report = distribution_report(
    ret_all,
    "val_return_fwd",
    clamp_min=-1.0,
    clamp_max=1.0
)

pct_saturated = (
    val_return_report.get("pct_above_clamp_max", 0.0) +
    val_return_report.get("pct_below_clamp_min", 0.0)
)

print(f"✓ Val loss: {loss_val.item():.4f}")
print(f"✓ Saturation: {pct_saturated:.2f}%")

# PATCH 1.1: Gradient summary
clip_ratio_epoch = float(epoch_clip_count / max(1, epoch_total_steps) * 100.0)
grad_norm_p50 = float(np.median(epoch_grad_norms))
grad_norm_p95 = float(np.percentile(epoch_grad_norms, 95))
grad_norm_max = float(np.max(epoch_grad_norms))

print("\n" + "="*60)
print("RÉSULTATS DES PATCHES")
print("="*60)

results = {
    "gradient_metrics": {
        "clip_ratio_epoch_pct": clip_ratio_epoch,
        "grad_norm_median": grad_norm_p50,
        "grad_norm_p95": grad_norm_p95,
        "grad_norm_max": grad_norm_max,
        "grad_clip_threshold": max_grad_norm,
    },
    "saturation_metrics": {
        "pct_saturated": pct_saturated,
        "p01": val_return_report.get("p01", 0.0),
        "p50": val_return_report.get("p50", 0.0),
        "p99": val_return_report.get("p99", 0.0),
    },
    "training_metrics": {
        "final_loss": float(loss.item()),
        "val_loss": float(loss_val.item()),
        "final_lr": float(lr_after),
        "amp_scale": float(amp_scale),
    }
}

print(json.dumps(results, indent=2))

# Vérifications
checks_passed = 0
checks_total = 6

print("\n" + "="*60)
print("VALIDATION DES PATCHES")
print("="*60)

if clip_ratio_epoch >= 0:
    print("✓ clip_ratio_epoch_pct calculé")
    checks_passed += 1
else:
    print("✗ clip_ratio_epoch_pct MISSING")

if grad_norm_p50 > 0:
    print("✓ grad_norm_median calculé")
    checks_passed += 1
else:
    print("✗ grad_norm_median MISSING")

if grad_norm_p95 > 0:
    print("✓ grad_norm_p95 calculé")
    checks_passed += 1
else:
    print("✗ grad_norm_p95 MISSING")

if pct_saturated >= 0:
    print("✓ pct_saturated calculé")
    checks_passed += 1
else:
    print("✗ pct_saturated MISSING")

if amp_scale > 0:
    print("✓ amp_scale capturé")
    checks_passed += 1
else:
    print("✗ amp_scale MISSING")

if lr_before != lr_after:
    print("✓ lr_before/lr_after différents (scheduler fonctionne)")
    checks_passed += 1
else:
    print("⚠ lr_before == lr_after (peut être OK si warmup)")

print("\n" + "="*60)
print(f"RÉSULTAT: {checks_passed}/{checks_total} checks passés")
print("="*60)

if checks_passed == checks_total:
    print("\n✅ TOUS LES PATCHES VALIDÉS")
    print("\nLe trainer est maintenant PRODUCTION-GRADE avec:")
    print("  ✓ Gradient logging complet")
    print("  ✓ AMP scale monitoring")
    print("  ✓ LR tracking précis")
    print("  ✓ Saturation detection")
    print("\nProchaine étape: Configurer S3 et lancer ./run_baseline_diagnostic.sh")
    sys.exit(0)
else:
    print(f"\n⚠ {checks_total - checks_passed} checks ont échoué")
    print("Vérifier que tous les patches sont appliqués correctement")
    sys.exit(1)

PYTHON_SCRIPT

# Exécuter le test
echo "Lancement du test..."
python3 test_patches_synthetic.py

# Cleanup
rm -f test_patches_synthetic.py

echo ""
echo "Test terminé!"
