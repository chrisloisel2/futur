"""
PATCH 1.1: GRADIENT INSTRUMENTATION COMPLÈTE
==============================================

Ajouter ce code dans la boucle d'entraînement (après scaler.unscale_, avant clip_grad_norm_)

CRITICAL: Mesure PRE-CLIP, WAS_CLIPPED, CLIP_RATIO, MAX_PARAM_GRAD, EFFECTIVE_UPDATE_NORM
"""

import torch
import numpy as np

# =============================================================================
# INSERT DANS train_one_epoch LOOP (APRÈS scaler.unscale_, AVANT clip_grad_norm_)
# =============================================================================

def compute_gradient_metrics(model, max_grad_norm: float):
    """
    Compute comprehensive gradient metrics.

    Returns:
        dict with:
            - pre_clip_norm: Total gradient norm BEFORE clipping
            - was_clipped: Boolean, True if clipping occurred
            - max_param_grad: Max gradient of any single parameter
            - n_params_with_grad: Number of params with gradients
    """
    # 1. Compute PRE-CLIP norm manually
    total_norm_sq = 0.0
    max_grad = 0.0
    n_params = 0

    for p in model.parameters():
        if p.grad is not None:
            param_norm_sq = p.grad.data.norm(2).item() ** 2
            total_norm_sq += param_norm_sq
            max_grad = max(max_grad, p.grad.abs().max().item())
            n_params += 1

    pre_clip_norm = float(total_norm_sq ** 0.5) if total_norm_sq > 0 else 0.0
    was_clipped = pre_clip_norm > max_grad_norm

    return {
        "pre_clip_norm": pre_clip_norm,
        "was_clipped": was_clipped,
        "max_param_grad": max_grad,
        "n_params_with_grad": n_params,
    }


# =============================================================================
# USAGE DANS TRAINING LOOP
# =============================================================================

# AVANT (ligne 1524-1531 actuelle):
"""
if ((batch_idx + 1) % grad_accum) == 0:
    scaler.unscale_(optimizer)
    torch.nn.utils.clip_grad_norm_(model.net.parameters(), max_norm=max_grad_norm)
    scaler.step(optimizer)
    scaler.update()
    scheduler.step()
    optimizer.zero_grad(set_to_none=True)
    global_step += 1
"""

# APRÈS (avec instrumentation):
"""
if ((batch_idx + 1) % grad_accum) == 0:
    scaler.unscale_(optimizer)

    # ========== PATCH 1.1: GRADIENT METRICS (PRE-CLIP) ==========
    grad_metrics = compute_gradient_metrics(model.net, max_grad_norm)
    pre_clip_norm = grad_metrics["pre_clip_norm"]
    was_clipped = grad_metrics["was_clipped"]
    max_param_grad = grad_metrics["max_param_grad"]
    # ============================================================

    # Clip (la valeur retournée est aussi pre_clip_norm, mais on l'a déjà)
    torch.nn.utils.clip_grad_norm_(model.net.parameters(), max_norm=max_grad_norm)

    scaler.step(optimizer)
    scaler.update()

    # ========== PATCH 1.1: LR BEFORE/AFTER SCHEDULER ==========
    lr_before = optimizer.param_groups[0]["lr"]
    # ============================================================

    scheduler.step()

    # ========== PATCH 1.1: LR AFTER SCHEDULER ==========
    lr_after = optimizer.param_groups[0]["lr"]
    # ===================================================

    # ========== PATCH 1.1: AMP SCALE ==========
    amp_scale = scaler.get_scale()
    # ==========================================

    optimizer.zero_grad(set_to_none=True)
    global_step += 1

    # ========== PATCH 1.1: ACCUMULATE EPOCH STATS ==========
    # Ajouter ces accumulateurs en début d'epoch:
    # epoch_clip_count = 0
    # epoch_total_steps = 0
    # epoch_grad_norms = []

    epoch_clip_count += int(was_clipped)
    epoch_total_steps += 1
    epoch_grad_norms.append(pre_clip_norm)
    # =======================================================

    if ema is not None:
        ema.update(model.net)

# ========== PATCH 1.1: BATCH LOG (UPDATED) ==========
if log_interval > 0 and (batch_idx % log_interval == 0):
    logger.info(
        {
            "msg": "BATCH_DIAGNOSTIC_PRODUCTION_GRADE",
            "epoch": epoch_idx,
            "batch": int(batch_idx),
            "loss": float(loss.item()) * float(max(1, grad_accum)),
            "loss_components": comps,

            # NEW: LR metrics
            "lr_before_step": float(lr_before),  # LR utilisé pour CE batch
            "lr_after_step": float(lr_after),    # LR qui sera utilisé au PROCHAIN batch

            # NEW: Gradient metrics
            "grad_pre_clip_norm": float(pre_clip_norm),
            "grad_was_clipped": bool(was_clipped),
            "grad_max_param": float(max_param_grad),
            "grad_clip_threshold": float(max_grad_norm),
            "grad_clip_ratio": float(pre_clip_norm / max_grad_norm) if max_grad_norm > 0 else 0.0,

            # NEW: AMP metrics
            "amp_scale": float(amp_scale),

            "global_step": int(global_step),
            "bad_batches": int(bad_batches),
        }
    )
# ====================================================

# ========== PATCH 1.1: EPOCH LOG (UPDATED) ==========
# À la fin de l'epoch, ajouter ces métriques au log EPOCH_END:

clip_ratio_epoch = float(epoch_clip_count / max(1, epoch_total_steps) * 100.0)
grad_norm_p50 = float(np.median(epoch_grad_norms)) if epoch_grad_norms else 0.0
grad_norm_p95 = float(np.percentile(epoch_grad_norms, 95)) if epoch_grad_norms else 0.0
grad_norm_max = float(np.max(epoch_grad_norms)) if epoch_grad_norms else 0.0

logger.info({
    "msg": "EPOCH_END",
    # ... metrics existantes ...

    # NEW: Gradient summary
    "gradient_summary": {
        "clip_ratio_epoch_pct": clip_ratio_epoch,  # % de steps clippés
        "grad_norm_median": grad_norm_p50,
        "grad_norm_p95": grad_norm_p95,
        "grad_norm_max": grad_norm_max,
        "grad_clip_threshold": float(max_grad_norm),
    },
})
# ====================================================
"""
