# 🚨 EMERGENCY FIX - Gradient Explosion

**Date:** 2025-12-15 21:16
**Issue:** Gradient explosion at batch ~700 (norm=25.95 > 10.0)
**Root Cause:** Learning rate too high for recursive GRU architecture

---

## 🔴 Problem Analysis

### Training Log
```
Epoch 1, Batch 0:   grad_norm = 0.070  ✅ STABLE
Epoch 1, Batch ~70:  grad_norm = 2.08   ⚠️ WARNING (LR reduced to 5e-5)
Epoch 1, Batch ~700: grad_norm = 25.95  ❌ EXPLOSION
```

### Root Causes
1. **LR too high:** `1e-4` → caused gradients to accumulate
2. **No warmup:** Full LR from epoch 1 shocked the system
3. **Too many iterations:** 5 GRU iterations = 5x gradient backprop
4. **Clip threshold too loose:** `1.0` allowed gradients to creep up

---

## ✅ Emergency Patches Applied

### 1. Ultra-Low Initial LR
**File:** `config_stable.yaml`
```yaml
learning_rate: 1e-5  # DOWN from 1e-4 (10x reduction)
```

### 2. Aggressive Gradient Clipping
**File:** `config_stable.yaml`
```yaml
grad_clip_norm: 0.5  # DOWN from 1.0 (2x tighter)
```

### 3. Reduced Recursive Iterations
**File:** `config_stable.yaml`
```yaml
num_iterations: 3  # DOWN from 5 (40% reduction)
```

### 4. LR Warmup (2 epochs)
**File:** `trainer.py` lines 71-78
```python
self.warmup_epochs = 2
# Start with 10% LR, ramp to 100% over 2 epochs
self.optimizer = optim.AdamW(
    model.parameters(),
    lr=learning_rate * 0.1,  # Warmup LR
    weight_decay=weight_decay
)
```

**File:** `trainer.py` lines 416-427
```python
if self.current_epoch <= self.warmup_epochs:
    warmup_factor = self.current_epoch / self.warmup_epochs
    target_lr = self.learning_rate * warmup_factor
    for param_group in self.optimizer.param_groups:
        param_group['lr'] = target_lr
```

### 5. Aggressive LR Auto-Reduction
**File:** `trainer.py` lines 188-195
```python
# If grad_norm > 0.5 → cut LR in half
if grad_norm > 0.5:
    current_lr = self.optimizer.param_groups[0]['lr']
    new_lr = max(current_lr * 0.5, 1e-6)  # Floor at 1e-6
    for param_group in self.optimizer.param_groups:
        param_group['lr'] = new_lr
    logger.warning(f"[LR REDUCTION] grad_norm={grad_norm:.4f} > 0.5 → LR: {current_lr:.2e} → {new_lr:.2e}")
```

---

## 📊 Expected Behavior

### Epoch 1 (Warmup)
```
[WARMUP] Epoch 1/2: LR=5.00e-06  (50% of target)
Batch 0: grad_norm ~ 0.05
Batch 100: grad_norm ~ 0.3
Batch 1000: grad_norm < 0.5
```

### Epoch 2 (Warmup Complete)
```
[WARMUP] Epoch 2/2: LR=1.00e-05  (100% of target)
Batch 0: grad_norm ~ 0.1
Max grad_norm in epoch < 0.5
```

### Epoch 3+ (Normal Training)
```
LR gradually decreases via cosine annealing
If grad_norm > 0.5 → LR halved automatically
Training stable, no explosions
```

---

## 🧪 Test Command

```bash
cd /home/qbee/Bureau/Bourse/futur/ai/TRAIN/trm
python train_trm.py --config config_stable.yaml --epochs 5
```

---

## ✅ Success Criteria

- [ ] Epoch 1 completes without explosion
- [ ] grad_norm stays < 0.5 throughout
- [ ] LR warmup logs visible
- [ ] No "[LR REDUCTION]" warnings (ideally)
- [ ] Predictions stay in [-0.01, 0.01]
- [ ] Sharpe < 3.0 in validation

---

## ❌ If Still Fails

### Plan B: Further Reductions
1. **LR → 5e-6** (50% reduction)
2. **Iterations → 2** (only 2 GRU steps)
3. **Clip → 0.25** (4x tighter)
4. **Warmup → 5 epochs** (slower ramp)

### Plan C: Architectural Change
1. Replace GRU with simpler LSTM
2. Use single-iteration (no recursion)
3. Add LayerNorm after every GRU step

### Plan D: Nuclear Option
```yaml
learning_rate: 1e-6
num_iterations: 1
grad_clip_norm: 0.1
warmup_epochs: 10
```

---

## 📁 Modified Files

| File | Change |
|------|--------|
| `config_stable.yaml` | LR: 1e-5, clip: 0.5, iter: 3 |
| `trainer.py` | Warmup + aggressive LR reduction |

---

## 🎯 Next Steps

1. **Test with 5 epochs** using patched config
2. **Monitor grad_norm** in logs (should stay < 0.5)
3. **Verify warmup** logs appear
4. **Check final Sharpe** (realistic < 3.0)

If successful → Run full 100 epochs
If fails → Apply Plan B

---

**Status:** 🟡 EMERGENCY PATCHED - AWAITING VALIDATION
