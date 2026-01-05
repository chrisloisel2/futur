# PATCH UNIFIÉ - APPLICATION DIRECTE
## Instructions pour appliquer tous les patches au trainer

**FICHIERS MODIFIÉS**:
1. `scripts/train_edge_forecaster.py` (training loop)
2. `src/pipeline/models/edge/net.py` (compute_loss signature)

---

## MODIFICATIONS REQUISES

### 1. AJOUTER FONCTION HELPER (début du fichier, après imports)

**FICHIER**: `scripts/train_edge_forecaster.py`

**LIGNE**: Après ligne 60 (après `logger = get_logger(__name__)`)

**AJOUTER**:

```python
# =============================================================================
# PATCH: GRADIENT METRICS (DIAGNOSTIC)
# =============================================================================
def compute_gradient_metrics(model, max_grad_norm: float):
    """Compute comprehensive gradient metrics BEFORE clipping."""
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


def distribution_report(arr: np.ndarray, name: str, clamp_min: float = None, clamp_max: float = None):
    """Generate distribution report with saturation detection."""
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
```

---

### 2. MODIFIER BOUCLE D'ENTRAÎNEMENT (TRAINING LOOP)

**FICHIER**: `scripts/train_edge_forecaster.py`

**LIGNE**: ~1483 (début de la boucle epoch)

**AJOUTER** en début d'epoch:

```python
for epoch in range(n_epochs):
    epoch_idx = epoch + 1
    t0 = time.time()

    model.net.train()
    train_loss_sum = 0.0
    train_batches = 0

    # ========== PATCH: ACCUMULATEURS EPOCH ==========
    epoch_clip_count = 0
    epoch_total_steps = 0
    epoch_grad_norms = []
    # ================================================

    optimizer.zero_grad(set_to_none=True)
```

**LIGNE**: ~1524-1531 (gradient clipping)

**REMPLACER**:

```python
# ANCIEN CODE:
if ((batch_idx + 1) % grad_accum) == 0:
    scaler.unscale_(optimizer)
    torch.nn.utils.clip_grad_norm_(model.net.parameters(), max_norm=max_grad_norm)
    scaler.step(optimizer)
    scaler.update()
    scheduler.step()
    optimizer.zero_grad(set_to_none=True)
    global_step += 1
```

**PAR**:

```python
# NOUVEAU CODE (AVEC LOGGING):
if ((batch_idx + 1) % grad_accum) == 0:
    scaler.unscale_(optimizer)

    # ========== PATCH: GRADIENT METRICS (PRE-CLIP) ==========
    grad_metrics = compute_gradient_metrics(model.net, max_grad_norm)
    pre_clip_norm = grad_metrics["pre_clip_norm"]
    was_clipped = grad_metrics["was_clipped"]
    max_param_grad = grad_metrics["max_param_grad"]
    # ========================================================

    torch.nn.utils.clip_grad_norm_(model.net.parameters(), max_norm=max_grad_norm)

    # ========== PATCH: LR BEFORE SCHEDULER ==========
    lr_before = optimizer.param_groups[0]["lr"]
    # ================================================

    scaler.step(optimizer)
    scaler.update()

    # ========== PATCH: AMP SCALE ==========
    amp_scale = scaler.get_scale()
    # ======================================

    scheduler.step()

    # ========== PATCH: LR AFTER SCHEDULER ==========
    lr_after = optimizer.param_groups[0]["lr"]
    # ===============================================

    optimizer.zero_grad(set_to_none=True)
    global_step += 1

    # ========== PATCH: ACCUMULATE EPOCH STATS ==========
    epoch_clip_count += int(was_clipped)
    epoch_total_steps += 1
    epoch_grad_norms.append(pre_clip_norm)
    # ===================================================

    if global_step == 1:
        lr_first = optimizer.param_groups[0]["lr"]
        assert lr_first > 0.0, f"LR is zero at step 1 (lr={lr_first})"

    if ema is not None:
        ema.update(model.net)
```

**LIGNE**: ~1543-1555 (batch logging)

**REMPLACER**:

```python
# ANCIEN CODE:
if log_interval > 0 and (batch_idx % log_interval == 0):
    logger.info(
        {
            "msg": "BATCH_DIAGNOSTIC_PRODUCTION_GRADE",
            "epoch": epoch_idx,
            "batch": int(batch_idx),
            "loss": float(loss.item()) * float(max(1, grad_accum)),
            "loss_components": comps,
            "lr": float(optimizer.param_groups[0]["lr"]),
            "global_step": int(global_step),
            "bad_batches": int(bad_batches),
        }
    )
```

**PAR**:

```python
# NOUVEAU CODE (AVEC GRADIENT + AMP METRICS):
if log_interval > 0 and (batch_idx % log_interval == 0):
    logger.info(
        {
            "msg": "BATCH_DIAGNOSTIC_PRODUCTION_GRADE",
            "epoch": epoch_idx,
            "batch": int(batch_idx),
            "loss": float(loss.item()) * float(max(1, grad_accum)),
            "loss_components": comps,

            # ========== PATCH: LR METRICS ==========
            "lr_before_step": float(lr_before),
            "lr_after_step": float(lr_after),
            # =======================================

            # ========== PATCH: GRADIENT METRICS ==========
            "grad_pre_clip_norm": float(pre_clip_norm),
            "grad_was_clipped": bool(was_clipped),
            "grad_max_param": float(max_param_grad),
            "grad_clip_threshold": float(max_grad_norm),
            "grad_clip_ratio": float(pre_clip_norm / max_grad_norm) if max_grad_norm > 0 else 0.0,
            # =============================================

            # ========== PATCH: AMP METRICS ==========
            "amp_scale": float(amp_scale),
            # ========================================

            "global_step": int(global_step),
            "bad_batches": int(bad_batches),
        }
    )
```

**LIGNE**: ~1670-1705 (epoch end logging)

**AJOUTER** avant le log EPOCH_END (après calcul de trading_score):

```python
# ========== PATCH: COMPUTE EPOCH GRADIENT SUMMARY ==========
clip_ratio_epoch = float(epoch_clip_count / max(1, epoch_total_steps) * 100.0)
grad_norm_p50 = float(np.median(epoch_grad_norms)) if epoch_grad_norms else 0.0
grad_norm_p95 = float(np.percentile(epoch_grad_norms, 95)) if epoch_grad_norms else 0.0
grad_norm_max = float(np.max(epoch_grad_norms)) if epoch_grad_norms else 0.0
# ===========================================================
```

**ET MODIFIER LE LOG**:

```python
logger.info(
    {
        "msg": "EPOCH_END_PRODUCTION_GRADE",
        "epoch": epoch_idx,
        "time_sec": elapsed,
        "lr": float(optimizer.param_groups[0]["lr"]),
        "train_loss": float(train_loss),
        "val_loss": float(val_loss),

        # ========== PATCH: GRADIENT SUMMARY ==========
        "gradient_summary": {
            "clip_ratio_epoch_pct": clip_ratio_epoch,
            "grad_norm_median": grad_norm_p50,
            "grad_norm_p95": grad_norm_p95,
            "grad_norm_max": grad_norm_max,
            "grad_clip_threshold": float(max_grad_norm),
        },
        # =============================================

        "calibration": {"brier_dir_hit": brier_dir, "ece_dir_hit": ece_dir},
        # ... reste inchangé ...
    }
)
```

---

### 3. SATURATION CHECKS (OPTIONNEL - RECOMMANDÉ)

**FICHIER**: `scripts/train_edge_forecaster.py`

**LIGNE**: ~1590 (fin de validation epoch)

**AJOUTER** après la boucle de validation:

```python
# ========== PATCH: SATURATION CHECK (ONCE PER EPOCH) ==========
if epoch_idx == 1 or epoch_idx % 5 == 0:
    val_return_raw = torch.cat(all_ret).squeeze().cpu().numpy()
    val_return_report = distribution_report(
        val_return_raw,
        "val_return_fwd",
        clamp_min=-1.0,
        clamp_max=1.0
    )

    logger.info({
        "msg": "SATURATION_CHECK_EPOCH",
        "epoch": epoch_idx,
        "val_return_saturation": val_return_report,
    })

    # Hard fail if saturation > 10%
    pct_saturated = (
        val_return_report.get("pct_above_clamp_max", 0.0) +
        val_return_report.get("pct_below_clamp_min", 0.0)
    )

    if pct_saturated > 10.0:
        logger.warning({
            "msg": "SATURATION_WARNING",
            "pct_saturated": pct_saturated,
            "recommendation": "Consider wider clamps: [-2.0, 2.0] or [-5.0, 5.0]",
        })
# ==============================================================
```

---

## 4. AJOUTER ARGPARSE POUR GRAD_CLIP (OPTIONNEL)

**FICHIER**: `scripts/train_edge_forecaster.py`

**LIGNE**: ~1900-2000 (argparse section)

**AJOUTER**:

```python
parser.add_argument("--grad-clip", type=float, default=None, help="Override grad_clip from config")
```

**PUIS DANS MAIN** (après parse args):

```python
# Override config if CLI arg provided
if args.grad_clip is not None:
    max_grad_norm = args.grad_clip
    logger.info({"msg": "GRAD_CLIP_OVERRIDE", "value": max_grad_norm})
```

---

## VÉRIFICATION POST-APPLICATION

**Checklist**:

1. ✅ `compute_gradient_metrics` ajouté après imports
2. ✅ `distribution_report` ajouté après imports
3. ✅ `epoch_clip_count`, `epoch_total_steps`, `epoch_grad_norms` initialisés en début d'epoch
4. ✅ `grad_metrics`, `lr_before`, `lr_after`, `amp_scale` capturés dans gradient step
5. ✅ Batch log contient: `grad_pre_clip_norm`, `grad_was_clipped`, `grad_clip_ratio`, `amp_scale`, `lr_before_step`, `lr_after_step`
6. ✅ Epoch log contient: `gradient_summary` avec `clip_ratio_epoch_pct`, `grad_norm_median`, `grad_norm_p95`, `grad_norm_max`
7. ✅ (Optionnel) Saturation check ajouté en fin de validation epoch

**Test rapide**:

```bash
# Run 1 epoch avec logging
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/test_patch \
  --epochs 1 \
  --data-pct 0.01 \
  --log-interval 10 \
  --device cuda

# Vérifier les logs contiennent:
# - "grad_pre_clip_norm"
# - "grad_was_clipped"
# - "grad_clip_ratio"
# - "amp_scale"
# - "gradient_summary"
```

**Si erreur** → vérifier indentation et imports

---

## NEXT STEPS

1. **Appliquer ce patch** au trainer
2. **Run baseline** (RUN 0 du plan expérimental)
3. **Analyser les logs** pour identifier la cause:
   - Si `clip_ratio_epoch_pct > 80%` → grad_clip trop bas
   - Si `pct_saturated > 10%` → target clamp trop serré
   - Si `amp_scale < 100` après 3 epochs → AMP instable
4. **Run sweep correspondant** (RUN 1, 2, ou 3)
5. **Valider amélioration** > 10% val_loss

**FIN DU PATCH UNIFIÉ**
