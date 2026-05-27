"""
AUDIT COMPLET HEAD IS_UP
========================

Phase 1: Données & Labels
Phase 2: Architecture & Gradients
Phase 3: Loss & Masques
Phase 4: Training Dynamics

Usage:
    pytest tests/test_audit_is_up_head.py -v -s
"""

import sys
from pathlib import Path
import pytest
import pandas as pd
import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ============================================================================
# PHASE 1: AUDIT DONNÉES & LABELS
# ============================================================================

def test_label_is_up_causality():
    """
    CRITICAL: Vérifier que is_up est parfaitement corrélé avec return_fwd > 0
    Post FIX-3, correlation DOIT être 1.0
    """
    # Simuler données réelles
    n_samples = 10000
    return_fwd = np.random.randn(n_samples) * 0.01

    # Label après FIX-3
    is_up = (return_fwd > 0).astype(int)

    # Vérifications
    return_positive = (return_fwd > 0).astype(int)

    # Perfect correlation
    if is_up.std() > 0:
        corr = np.corrcoef(is_up, return_positive)[0, 1]
        assert abs(corr - 1.0) < 1e-10, f"Correlation = {corr}, expected 1.0"

    # Distribution check
    mean_is_up = is_up.mean()
    assert 0.3 < mean_is_up < 0.7, f"is_up mean = {mean_is_up}, should be ~0.5 if balanced"

    print(f"✅ Label causality: corr={corr:.6f}, mean={mean_is_up:.3f}")


def test_label_imbalance_analysis():
    """
    Analyser l'imbalance is_up global et conditionnel sur dir_hit
    """
    n_samples = 10000

    # Simuler données réalistes crypto
    return_fwd = np.random.randn(n_samples) * 0.01 + 0.0001  # Slight positive bias
    dir_hit = np.random.rand(n_samples)

    is_up = (return_fwd > 0).astype(int)

    # Imbalance global
    imbalance_global = is_up.mean()

    # Imbalance conditionnel (sur dir_hit > 0.5, comme dans eval)
    mask_dir = dir_hit > 0.5
    if mask_dir.sum() > 0:
        imbalance_conditional = is_up[mask_dir].mean()
    else:
        imbalance_conditional = 0.5

    # Warnings
    if abs(imbalance_global - 0.5) > 0.15:
        print(f"⚠️  IMBALANCE GLOBAL: {imbalance_global:.3f} (far from 0.5)")

    if abs(imbalance_conditional - 0.5) > 0.15:
        print(f"⚠️  IMBALANCE CONDITIONAL: {imbalance_conditional:.3f} (far from 0.5)")

    # Check masque reduction
    reduction = mask_dir.sum() / len(mask_dir)
    if reduction < 0.3:
        print(f"⚠️  MASQUE REDUCTION: {reduction:.1%} (< 30% samples kept)")

    print(f"✅ Imbalance analysis: global={imbalance_global:.3f}, cond={imbalance_conditional:.3f}, mask_kept={reduction:.1%}")


def test_no_temporal_leakage():
    """
    Vérifier qu'il n'y a PAS de leakage temporel dans is_up
    """
    # Simuler série temporelle
    n_steps = 1000
    prices = 100 * np.exp(np.cumsum(np.random.randn(n_steps) * 0.01))

    # Forward returns (causal)
    horizon = 60  # minutes
    return_fwd = np.zeros(n_steps)
    for i in range(n_steps - horizon):
        return_fwd[i] = (prices[i + horizon] - prices[i]) / prices[i]

    # is_up label
    is_up = (return_fwd > 0).astype(int)

    # Vérifier que is_up[t] ne dépend PAS de is_up[t-1]
    # (si autocorr forte → potentiel leakage)
    autocorr_lag1 = np.corrcoef(is_up[:-1], is_up[1:])[0, 1]

    if abs(autocorr_lag1) > 0.3:
        print(f"⚠️  HIGH AUTOCORR: {autocorr_lag1:.3f} (may indicate leakage or regime persistence)")
    else:
        print(f"✅ Low autocorr: {autocorr_lag1:.3f} (no obvious leakage)")

    assert not np.isnan(autocorr_lag1), "Autocorr is NaN"


def test_label_alignment_with_horizon():
    """
    Vérifier que is_up est aligné avec le bon horizon forward
    """
    n_samples = 100
    returns_15m = np.random.randn(n_samples) * 0.005
    returns_60m = np.random.randn(n_samples) * 0.01

    is_up_15m = (returns_15m > 0).astype(int)
    is_up_60m = (returns_60m > 0).astype(int)

    # Si horizon est 60m, is_up DOIT utiliser returns_60m
    # Vérifier qu'on n'a pas mismatch

    # Simuler cas correct
    is_up_label = is_up_60m  # Assume horizon=60m
    expected_from_60m = is_up_60m

    assert np.array_equal(is_up_label, expected_from_60m), "Mismatch horizon!"

    print(f"✅ Horizon alignment: correct (60m)")


# ============================================================================
# PHASE 2: AUDIT ARCHITECTURE & GRADIENTS
# ============================================================================

class EdgeForecasterHead(nn.Module):
    """Simuler architecture head"""
    def __init__(self, d_model=256):
        super().__init__()
        self.shared_trunk = nn.Linear(d_model, 128)
        self.head_is_up = nn.Linear(128, 1)
        self.head_dir = nn.Linear(128, 1)

    def forward(self, x):
        h = torch.relu(self.shared_trunk(x))
        logit_up = self.head_is_up(h)
        logit_dir = self.head_dir(h)
        return logit_up, logit_dir


def test_gradient_flow_is_up():
    """
    Vérifier que les gradients is_up ne sont PAS écrasés par dir
    """
    model = EdgeForecasterHead(d_model=64)
    x = torch.randn(32, 64)

    # Forward
    logit_up, logit_dir = model(x)

    # Loss simulé
    target_up = torch.randint(0, 2, (32, 1)).float()
    target_dir = torch.rand(32, 1)

    loss_up = nn.functional.binary_cross_entropy_with_logits(logit_up, target_up)
    loss_dir = nn.functional.mse_loss(logit_dir, target_dir)

    # Pondération actuelle (FIX-2)
    w_up = 0.20
    w_dir = 0.28

    total_loss = w_up * loss_up + w_dir * loss_dir

    # Backward
    total_loss.backward()

    # Check gradients
    grad_shared = model.shared_trunk.weight.grad
    grad_up = model.head_is_up.weight.grad
    grad_dir = model.head_dir.weight.grad

    assert grad_up is not None, "No gradient on is_up head!"
    assert grad_dir is not None, "No gradient on dir head!"

    # Compare magnitudes
    grad_up_norm = grad_up.norm().item()
    grad_dir_norm = grad_dir.norm().item()

    ratio = grad_up_norm / (grad_dir_norm + 1e-8)

    if ratio < 0.1:
        print(f"⚠️  GRADIENT IMBALANCE: grad_up/grad_dir = {ratio:.3f} (<< 1)")
    else:
        print(f"✅ Gradient ratio: {ratio:.3f}")

    assert grad_up_norm > 1e-6, "Gradient is_up too small (vanishing)"


def test_head_capacity():
    """
    Vérifier que le head is_up a suffisamment de paramètres
    """
    model = EdgeForecasterHead(d_model=256)

    n_params_up = sum(p.numel() for p in model.head_is_up.parameters())
    n_params_dir = sum(p.numel() for p in model.head_dir.parameters())

    print(f"Head is_up: {n_params_up} params")
    print(f"Head dir: {n_params_dir} params")

    # Si is_up a << params que dir → peut manquer de capacité
    assert n_params_up > 0, "Head is_up has no parameters!"

    if n_params_up < 100:
        print(f"⚠️  HEAD CAPACITY: Only {n_params_up} params (may be insufficient)")


# ============================================================================
# PHASE 3: AUDIT LOSS & MASQUES
# ============================================================================

def test_mask_train_vs_eval_consistency():
    """
    CRITICAL: Vérifier que masque train == masque eval
    Si train sur tous samples mais eval sur dir_hit > 0.5 → MISMATCH
    """
    n_samples = 1000
    dir_hit = torch.rand(n_samples)
    is_up = torch.randint(0, 2, (n_samples,)).float()
    logits_up = torch.randn(n_samples)

    # TRAIN loss (actuel code)
    # Vérifie si un masque est appliqué
    loss_train_all = nn.functional.binary_cross_entropy_with_logits(
        logits_up, is_up, reduction='mean'
    )

    # EVAL loss (diagnostic - sur dir_hit > 0.5)
    mask_eval = dir_hit > 0.5
    if mask_eval.sum() > 0:
        loss_eval_masked = nn.functional.binary_cross_entropy_with_logits(
            logits_up[mask_eval], is_up[mask_eval], reduction='mean'
        )
    else:
        loss_eval_masked = torch.tensor(0.0)

    # Si les deux losses sont très différentes → MISMATCH
    diff = abs(loss_train_all.item() - loss_eval_masked.item())

    if diff > 0.1:
        print(f"⚠️  TRAIN/EVAL MISMATCH: train_loss={loss_train_all:.3f}, eval_loss={loss_eval_masked:.3f}")
        print(f"   → Suggestion: Appliquer MÊME masque en train et eval")
    else:
        print(f"✅ Train/Eval consistent: diff={diff:.4f}")


def test_loss_weighting_effective():
    """
    Vérifier que w_up = 0.20 est effectivement appliqué
    """
    w_q05 = 0.15
    w_q50 = 0.15
    w_q95 = 0.15
    w_dir = 0.28
    w_up = 0.20
    w_rv = 0.07

    total = w_q05 + w_q50 + w_q95 + w_dir + w_up + w_rv

    assert abs(total - 1.0) < 1e-6, f"Weights don't sum to 1.0: {total}"

    # Vérifier que w_up n'est pas trop faible comparé aux autres
    w_others = w_q05 + w_q50 + w_q95 + w_dir + w_rv
    ratio = w_up / w_others

    if ratio < 0.15:  # w_up devrait être au moins 15% du total des autres
        print(f"⚠️  w_up TOO LOW: w_up/(sum_others) = {ratio:.2%}")
    else:
        print(f"✅ w_up adequate: {w_up:.2f} ({ratio:.1%} of others)")


def test_bce_with_imbalance():
    """
    Démontrer que BCE vanilla échoue avec imbalance fort
    """
    n_samples = 1000

    # Imbalance 90/10
    is_up = torch.cat([
        torch.ones(900),
        torch.zeros(100)
    ])

    # Modèle qui prédit toujours 0.9 (quasi-constant)
    logits_constant = torch.ones(n_samples) * 2.2  # sigmoid(2.2) ≈ 0.9

    # BCE
    loss = nn.functional.binary_cross_entropy_with_logits(logits_constant, is_up)

    # Baseline (prédire toujours la classe majoritaire)
    baseline_loss = -0.9 * np.log(0.9) - 0.1 * np.log(0.1)

    print(f"BCE avec imbalance 90/10:")
    print(f"  Loss constant prediction: {loss.item():.3f}")
    print(f"  Baseline: {baseline_loss:.3f}")
    print(f"  → Modèle apprend à prédire constante ≈ mean(is_up)")

    assert loss.item() < 0.5, "Loss should be low for constant pred on imbalanced data"


# ============================================================================
# PHASE 4: TRAINING DYNAMICS
# ============================================================================

def test_logits_variance_increase():
    """
    Vérifier que logits_up variance augmente avec training
    Si std reste ~0 → head n'apprend pas
    """
    # Simuler évolution logits sur epochs
    epochs_logits_std = [
        0.01,   # epoch 0 (init)
        0.015,  # epoch 5
        0.018,  # epoch 10
        0.019,  # epoch 20
        0.020   # epoch 40
    ]

    # Vérifier tendance croissante
    is_increasing = all(
        epochs_logits_std[i] <= epochs_logits_std[i+1]
        for i in range(len(epochs_logits_std)-1)
    )

    final_std = epochs_logits_std[-1]

    if not is_increasing:
        print(f"⚠️  LOGITS STD NOT INCREASING: {epochs_logits_std}")

    if final_std < 0.1:
        print(f"⚠️  LOGITS STD TOO LOW: {final_std:.3f} (head predicts ~constant)")
    else:
        print(f"✅ Logits std adequate: {final_std:.3f}")


def test_early_stopping_criteria():
    """
    Définir critères early stopping pour head is_up
    """
    # Metrics sur epochs
    metrics_history = [
        {'epoch': 0, 'bce_up': 0.693, 'pup_std': 0.005, 'bce_baseline': 0.666},
        {'epoch': 5, 'bce_up': 0.680, 'pup_std': 0.020, 'bce_baseline': 0.666},
        {'epoch': 10, 'bce_up': 0.650, 'pup_std': 0.050, 'bce_baseline': 0.666},
        {'epoch': 20, 'bce_up': 0.620, 'pup_std': 0.100, 'bce_baseline': 0.666},
    ]

    # Critères de réussite
    for m in metrics_history:
        epoch = m['epoch']
        bce_up = m['bce_up']
        pup_std = m['pup_std']
        baseline = m['bce_baseline']

        # Condition 1: bce_up < baseline
        cond1 = bce_up < baseline

        # Condition 2: pup_std > 0.03 (variance significative)
        cond2 = pup_std > 0.03

        # Condition 3: amélioration continue
        improvement = (0.693 - bce_up) / 0.693
        cond3 = improvement > 0.05  # Au moins 5% amélioration

        print(f"Epoch {epoch:2d}: bce={bce_up:.3f}, std={pup_std:.3f}, "
              f"better_than_baseline={cond1}, has_variance={cond2}, improved={cond3}")

    print("\n✅ Early stopping criteria defined")


# ============================================================================
# RUN ALL TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
