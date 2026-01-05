"""
PATCH 1.3: MODE DEBUG OVERFIT (INTÉGRÉ)
=========================================

Ajouter un mode --debug-overfit qui:
1. Prend 256 samples
2. Désactive shuffle
3. Désactive dropout
4. weight_decay = 0
5. grad_clip élevé (1000.0 = désactivé)
6. Run 500-1000 steps
7. Log loss curve détaillée

CRITICAL: Si le modèle ne peut pas overfit 256 samples → problème fondamental
"""

import argparse
import numpy as np
import torch
import matplotlib.pyplot as plt
from pathlib import Path

# =============================================================================
# AJOUTER À L'ARGPARSE
# =============================================================================

def add_overfit_args(parser: argparse.ArgumentParser):
    """Add debug overfit mode arguments"""
    parser.add_argument(
        "--debug-overfit",
        action="store_true",
        help="Debug mode: overfit on 256 samples (disables shuffle, dropout, weight_decay)"
    )
    parser.add_argument(
        "--debug-overfit-samples",
        type=int,
        default=256,
        help="Number of samples for overfit test (default: 256)"
    )
    parser.add_argument(
        "--debug-overfit-steps",
        type=int,
        default=1000,
        help="Number of training steps for overfit test (default: 1000)"
    )


# =============================================================================
# OVERFIT MODE LOGIC
# =============================================================================

def run_overfit_test(
    model,
    X_train_np: np.ndarray,
    y_train_np: np.ndarray,
    n_samples: int = 256,
    n_steps: int = 1000,
    lr: float = 3e-4,
    device: str = "cuda",
    output_dir: Path = None,
):
    """
    Run overfit test on small dataset.

    Args:
        model: EdgeForecasterModel instance
        X_train_np: (N, seq_len, n_features) training sequences
        y_train_np: (N, 5) training targets
        n_samples: Number of samples to use (default: 256)
        n_steps: Number of training steps (default: 1000)
        lr: Learning rate (default: 3e-4, slightly higher than prod)
        device: torch device
        output_dir: Where to save plots

    Returns:
        dict with metrics
    """
    import torch
    from torch.utils.data import TensorDataset, DataLoader

    logger.info({
        "msg": "OVERFIT_TEST_START",
        "n_samples": n_samples,
        "n_steps": n_steps,
        "lr": lr,
        "device": device,
    })

    # 1. Subsample dataset (first n_samples)
    X_sub = X_train_np[:n_samples]
    y_sub = y_train_np[:n_samples]

    logger.info({
        "msg": "OVERFIT_SUBSET",
        "X_shape": list(X_sub.shape),
        "y_shape": list(y_sub.shape),
    })

    # 2. Create dataloader (NO SHUFFLE, batch_size=32 for stability)
    X_t = torch.from_numpy(X_sub).float()
    y_t = torch.from_numpy(y_sub).float()
    dataset = TensorDataset(X_t, y_t)
    loader = DataLoader(dataset, batch_size=32, shuffle=False, drop_last=False)

    device_torch = torch.device(device)

    # 3. Disable dropout (set model to train but override dropout)
    model.net.train()
    for m in model.net.modules():
        if isinstance(m, torch.nn.Dropout):
            m.p = 0.0  # Disable dropout

    # 4. Optimizer: NO WEIGHT DECAY, higher LR
    optimizer = torch.optim.AdamW(
        model.net.parameters(),
        lr=lr,
        weight_decay=0.0,  # NO REGULARIZATION
        betas=(0.9, 0.95),
    )

    # 5. Training loop
    loss_history = []
    step = 0

    logger.info({"msg": "OVERFIT_TRAINING_START"})

    while step < n_steps:
        for X_b, y_b in loader:
            if step >= n_steps:
                break

            X_b = X_b.to(device_torch)
            y_b = y_b.to(device_torch)

            # Forward
            optimizer.zero_grad()
            out = model.net(X_b, regime_vec=None)
            loss = model.net.compute_loss(out, y_b, label_smoothing=0.0)  # NO LABEL SMOOTHING

            # Backward (NO GRAD CLIP)
            loss.backward()

            # Optional: log grad norm (but don't clip)
            total_norm = 0.0
            for p in model.net.parameters():
                if p.grad is not None:
                    total_norm += p.grad.data.norm(2).item() ** 2
            grad_norm = total_norm ** 0.5

            optimizer.step()

            loss_val = float(loss.item())
            loss_history.append(loss_val)
            step += 1

            # Log every 50 steps
            if step % 50 == 0 or step == 1:
                logger.info({
                    "msg": "OVERFIT_STEP",
                    "step": step,
                    "loss": loss_val,
                    "grad_norm": grad_norm,
                })

    logger.info({"msg": "OVERFIT_TRAINING_END", "final_loss": loss_history[-1]})

    # 6. Evaluate final metrics
    model.net.eval()
    with torch.no_grad():
        X_full = X_t.to(device_torch)
        y_full = y_t.to(device_torch)

        out_final = model.net(X_full, regime_vec=None)
        loss_final = model.net.compute_loss(out_final, y_full, label_smoothing=0.0)

        q05, q50, q95, logits_dir, p_dir, rv_mean, sigma_tail = out_final

        # Compute metrics
        return_fwd = y_full[:, 0].cpu().numpy()
        q50_pred = q50.squeeze().cpu().numpy()

        mae = np.abs(return_fwd - q50_pred).mean()
        corr = np.corrcoef(return_fwd, q50_pred)[0, 1] if len(return_fwd) > 1 else 0.0

        dir_hit_true = y_full[:, 1].cpu().numpy()
        p_dir_pred = p_dir.squeeze().cpu().numpy()
        dir_acc = ((p_dir_pred > 0.5) == (dir_hit_true > 0.5)).mean()

    # 7. Save plot
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        plot_path = output_dir / "overfit_loss_curve.png"

        plt.figure(figsize=(10, 6))
        plt.plot(loss_history, label="Train Loss", alpha=0.7)
        plt.axhline(y=loss_history[0], color='r', linestyle='--', label=f"Initial: {loss_history[0]:.4f}")
        plt.axhline(y=loss_history[-1], color='g', linestyle='--', label=f"Final: {loss_history[-1]:.4f}")
        plt.xlabel("Step")
        plt.ylabel("Loss")
        plt.title(f"Overfit Test: {n_samples} samples, {n_steps} steps")
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(plot_path, dpi=150)
        plt.close()

        logger.info({"msg": "OVERFIT_PLOT_SAVED", "path": str(plot_path)})

    # 8. Return metrics
    result = {
        "n_samples": n_samples,
        "n_steps": n_steps,
        "initial_loss": float(loss_history[0]),
        "final_loss": float(loss_history[-1]),
        "loss_reduction": float(loss_history[0] - loss_history[-1]),
        "loss_reduction_pct": float((loss_history[0] - loss_history[-1]) / loss_history[0] * 100.0),
        "final_mae": float(mae),
        "final_corr": float(corr),
        "final_dir_acc": float(dir_acc),
        "loss_history": loss_history,
    }

    logger.info({
        "msg": "OVERFIT_TEST_COMPLETE",
        "result": {k: v for k, v in result.items() if k != "loss_history"},
    })

    # 9. SUCCESS CRITERIA
    success = True
    issues = []

    if result["loss_reduction_pct"] < 50.0:
        success = False
        issues.append(f"Loss reduction too small: {result['loss_reduction_pct']:.1f}% (expected > 50%)")

    if result["final_loss"] > 0.5 * result["initial_loss"]:
        success = False
        issues.append(f"Final loss too high: {result['final_loss']:.4f} (expected < {0.5 * result['initial_loss']:.4f})")

    if result["final_corr"] < 0.3:
        success = False
        issues.append(f"Correlation too low: {result['final_corr']:.3f} (expected > 0.3)")

    if success:
        logger.info({"msg": "OVERFIT_TEST_PASSED", "status": "SUCCESS"})
    else:
        logger.error({
            "msg": "OVERFIT_TEST_FAILED",
            "status": "FAILURE",
            "issues": issues,
            "diagnosis": "Model cannot overfit 256 samples → fundamental problem (architecture, loss, numerical instability)"
        })

    return result


# =============================================================================
# INTEGRATION DANS MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    # ... existing args ...

    # ========== PATCH 1.3: ADD OVERFIT ARGS ==========
    add_overfit_args(parser)
    # =================================================

    args = parser.parse_args()

    # ... load data, create model ...

    # ========== PATCH 1.3: RUN OVERFIT TEST IF REQUESTED ==========
    if args.debug_overfit:
        logger.info({"msg": "DEBUG_OVERFIT_MODE_ACTIVATED"})

        output_dir = Path(args.output_dir) / "debug_overfit"

        overfit_result = run_overfit_test(
            model=model,
            X_train_np=X_train_np,
            y_train_np=y_train_np,
            n_samples=args.debug_overfit_samples,
            n_steps=args.debug_overfit_steps,
            lr=3e-4,  # Slightly higher than production
            device=args.device,
            output_dir=output_dir,
        )

        # Save result
        import json
        result_path = output_dir / "overfit_result.json"
        with open(result_path, "w") as f:
            json.dump({k: v for k, v in overfit_result.items() if k != "loss_history"}, f, indent=2)

        logger.info({"msg": "OVERFIT_TEST_SAVED", "path": str(result_path)})

        # Exit after overfit test (don't run full training)
        sys.exit(0 if overfit_result["loss_reduction_pct"] > 50.0 else 1)
    # ==============================================================

    # ... rest of main (normal training) ...


# =============================================================================
# USAGE EXAMPLES
# =============================================================================

# Run overfit test:
# python scripts/train_edge_forecaster.py --debug-overfit --debug-overfit-samples 256 --debug-overfit-steps 1000

# Expected output:
# - If model is healthy: loss should drop by > 80% (ex: 2.5 → 0.3)
# - If model is broken: loss will plateau or barely move

# If overfit test FAILS → do NOT run full training, fix the model first
"""
