"""
PATCH 1.2: SATURATION CHECKS (RUNTIME HISTOGRAM)
=================================================

Ajouter ce code pour détecter la saturation des targets et outputs.

CRITICAL: Log histogram/quantiles de return_fwd, dir_hit, tp/sl thresholds AVANT clamp
"""

import numpy as np
import torch

# =============================================================================
# HELPER: Distribution report
# =============================================================================

def distribution_report(arr: np.ndarray, name: str, clamp_min: float = None, clamp_max: float = None):
    """
    Generate distribution report with saturation detection.

    Args:
        arr: numpy array
        name: variable name
        clamp_min: if provided, compute % below this threshold
        clamp_max: if provided, compute % above this threshold

    Returns:
        dict with quantiles + saturation metrics
    """
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return {"name": name, "n": 0, "error": "no_finite_values"}

    q = np.percentile(arr, [1, 5, 10, 25, 50, 75, 90, 95, 99])

    report = {
        "name": name,
        "n": len(arr),
        "p01": float(q[0]),
        "p05": float(q[1]),
        "p10": float(q[2]),
        "p25": float(q[3]),
        "p50": float(q[4]),
        "p75": float(q[5]),
        "p90": float(q[6]),
        "p95": float(q[7]),
        "p99": float(q[8]),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }

    # Saturation detection
    if clamp_min is not None:
        pct_below = float((arr < clamp_min).sum() / len(arr) * 100.0)
        pct_at_min = float((np.abs(arr - clamp_min) < 1e-6).sum() / len(arr) * 100.0)
        report["pct_below_clamp_min"] = pct_below
        report["pct_at_clamp_min"] = pct_at_min

    if clamp_max is not None:
        pct_above = float((arr > clamp_max).sum() / len(arr) * 100.0)
        pct_at_max = float((np.abs(arr - clamp_max) < 1e-6).sum() / len(arr) * 100.0)
        report["pct_above_clamp_max"] = pct_above
        report["pct_at_clamp_max"] = pct_at_max

    return report


# =============================================================================
# INSERT DANS compute_loss (net.py)
# =============================================================================

# AVANT (net.py:378-383):
"""
def compute_loss(self, x_seq, targets, label_smoothing: float = 0.0, regime_vec=None):
    torch, nn, F = _lazy_import_torch()
    q05, q50, q95, logits_dir, _p_dir, rv_mean, _sigma_tail = self.forward(x_seq, regime_vec=regime_vec)

    targets = torch.nan_to_num(targets, nan=0.0, posinf=0.0, neginf=0.0)

    return_fwd = targets[:, 0:1].clamp(-1.0, 1.0)
    dir_hit = targets[:, 1:2].clamp(0.0, 1.0)
    rv_fwd_mean = targets[:, 4:5].clamp(0.0, 1.0)
    ...
"""

# APRÈS (avec diagnostic):
"""
def compute_loss(self, x_seq, targets, label_smoothing: float = 0.0, regime_vec=None, diagnostic_mode: bool = False):
    torch, nn, F = _lazy_import_torch()
    q05, q50, q95, logits_dir, _p_dir, rv_mean, _sigma_tail = self.forward(x_seq, regime_vec=regime_vec)

    targets = torch.nan_to_num(targets, nan=0.0, posinf=0.0, neginf=0.0)

    # ========== PATCH 1.2: SAVE RAW TARGETS BEFORE CLAMP ==========
    return_fwd_raw = targets[:, 0:1].clone()  # BEFORE clamp
    dir_hit_raw = targets[:, 1:2].clone()
    rv_fwd_mean_raw = targets[:, 4:5].clone()
    # ===============================================================

    return_fwd = targets[:, 0:1].clamp(-1.0, 1.0)
    dir_hit = targets[:, 1:2].clamp(0.0, 1.0)
    rv_fwd_mean = targets[:, 4:5].clamp(0.0, 1.0)

    # ========== PATCH 1.2: DIAGNOSTIC MODE ==========
    if diagnostic_mode:
        # Convert to numpy for analysis
        return_raw_np = return_fwd_raw.detach().float().cpu().numpy().reshape(-1)
        return_clamp_np = return_fwd.detach().float().cpu().numpy().reshape(-1)
        dir_raw_np = dir_hit_raw.detach().float().cpu().numpy().reshape(-1)
        rv_raw_np = rv_fwd_mean_raw.detach().float().cpu().numpy().reshape(-1)

        q50_np = q50.detach().float().cpu().numpy().reshape(-1)
        logits_dir_np = logits_dir.detach().float().cpu().numpy().reshape(-1)

        saturation_report = {
            "return_fwd_raw": distribution_report(return_raw_np, "return_fwd_raw", clamp_min=-1.0, clamp_max=1.0),
            "return_fwd_clamped": distribution_report(return_clamp_np, "return_fwd_clamped"),
            "dir_hit": distribution_report(dir_raw_np, "dir_hit", clamp_min=0.0, clamp_max=1.0),
            "rv_fwd_mean": distribution_report(rv_raw_np, "rv_fwd_mean", clamp_min=0.0, clamp_max=1.0),
            "q50_pred": distribution_report(q50_np, "q50_pred"),
            "logits_dir_pred": distribution_report(logits_dir_np, "logits_dir_pred", clamp_min=-50.0, clamp_max=50.0),
        }

        # CRITICAL: Log this in training loop
        # We'll add a return value for this
    else:
        saturation_report = None
    # ================================================

    # ... rest of loss computation unchanged ...

    quantile losses, etc...

    # ========== PATCH 1.2: RETURN SATURATION REPORT ==========
    if saturation_report is not None:
        return total_loss, saturation_report
    else:
        return total_loss, None
    # =========================================================
"""

# =============================================================================
# USAGE DANS TRAINING LOOP
# =============================================================================

# DANS train_one_epoch, batch loop (ligne 1500-1504 actuelle):
"""
enable_diagnostic = (log_interval > 0) and (batch_idx % log_interval == 0)

with autocast(device_type="cuda", enabled=use_amp):
    out = model.net(X_b, regime_vec=None)

    # ========== PATCH 1.2: COMPUTE LOSS WITH DIAGNOSTIC ==========
    loss_result = model.net.compute_loss(
        X_b, y_b,
        label_smoothing=label_smoothing,
        regime_vec=None,
        diagnostic_mode=enable_diagnostic  # NEW
    )

    if enable_diagnostic and isinstance(loss_result, tuple):
        loss, saturation_report = loss_result
    else:
        loss = loss_result
        saturation_report = None
    # =============================================================

    loss = loss / float(max(1, grad_accum))

# ========== PATCH 1.2: LOG SATURATION IF AVAILABLE ==========
if log_interval > 0 and (batch_idx % log_interval == 0):
    log_dict = {
        "msg": "BATCH_DIAGNOSTIC_PRODUCTION_GRADE",
        "epoch": epoch_idx,
        "batch": int(batch_idx),
        "loss": float(loss.item()) * float(max(1, grad_accum)),
        "loss_components": comps,
        # ... autres metrics ...
    }

    # NEW: Add saturation report if available
    if saturation_report is not None:
        log_dict["saturation"] = saturation_report

    logger.info(log_dict)
# =============================================================
"""

# =============================================================================
# CRITICAL: HARD FAIL SI SATURATION TROP ÉLEVÉE
# =============================================================================

# AJOUTER APRÈS EPOCH END (validation):
"""
# Check saturation on validation set (once per epoch)
if epoch_idx == 1 or epoch_idx % 5 == 0:  # Check every 5 epochs
    # Compute saturation metrics on full val set
    val_return_fwd_raw = []

    for X_b, y_b in val_loader:
        return_raw = y_b[:, 0].detach().float().cpu().numpy()
        val_return_fwd_raw.extend(return_raw)

    val_return_fwd_raw = np.array(val_return_fwd_raw)
    val_return_report = distribution_report(val_return_fwd_raw, "val_return_fwd", clamp_min=-1.0, clamp_max=1.0)

    logger.info({
        "msg": "SATURATION_CHECK_EPOCH",
        "epoch": epoch_idx,
        "val_return_saturation": val_return_report,
    })

    # ========== PATCH 1.2: HARD FAIL IF SATURATION > 10% ==========
    pct_saturated = val_return_report.get("pct_above_clamp_max", 0.0) + val_return_report.get("pct_below_clamp_min", 0.0)

    if pct_saturated > 10.0:
        logger.error({
            "msg": "SATURATION_CRITICAL",
            "pct_saturated": pct_saturated,
            "action": "RECOMMEND_WIDER_CLAMPS",
            "current_clamp": "[-1.0, 1.0]",
            "recommendation": "Use [-2.0, 2.0] or [-5.0, 5.0] or remove clamp entirely",
        })
        # Optionally raise or just warn
        # raise RuntimeError(f"Target saturation too high: {pct_saturated:.1f}%")
    # ==============================================================
"""
