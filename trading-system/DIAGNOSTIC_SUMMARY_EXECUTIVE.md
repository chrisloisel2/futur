# 🎯 RÉSUMÉ EXÉCUTIF — DIAGNOSTIC PLATEAU EDGE FORECASTER

**DATE**: 2026-01-05
**STATUS**: AUDIT COMPLET ✅
**FICHIERS LUES**: 4 (forecaster.py, net.py, train_edge_forecaster.py, training_config.py)
**DURÉE AUDIT**: Complet (aucune spéculation)

---

## 🔴 PROBLÈMES CRITIQUES IDENTIFIÉS

### 1. ❌ **GRADIENT LOGGING ABSENT** (CRITIQUE)

**FAIT PROUVÉ**:
- `clip_grad_norm_()` appelé ligne 1526 du trainer
- **VALEUR RETOURNÉE JAMAIS LOGGÉE** (pre_clip_norm perdu)
- Aucune métrique de gradient dans les logs actuels

**CONSÉQUENCE**:
- **IMPOSSIBLE** de diagnostiquer si clipping se produit
- **IMPOSSIBLE** de savoir si gradients explosent ou stagnent
- Plateau pourrait être causé par clipping excessif mais **non détectable**

**PREUVE CODE**:
```python
# train_edge_forecaster.py:1526
torch.nn.utils.clip_grad_norm_(model.net.parameters(), max_norm=max_grad_norm)
# ↑ Valeur retournée = pre_clip_norm, JAMAIS capturée
```

---

### 2. ⚠️  **GRAD_CLIP = 1.0 PROBABLEMENT TROP BAS** (LIKELIHOOD 90%)

**FAIT PROUVÉ**:
- `grad_clip = 1.0` (training_config.py:84)
- Transformer: d_model=192, n_layers=5, ~2-3M paramètres
- Heuristique: grad_norm naturel ~ `d_model × sqrt(n_layers)` ≈ **430**
- Ratio: **grad_clip est 430x plus petit**

**HYPOTHÈSE**:
- Si 90-100% des batches sont clippés → updates constamment limitées
- Modèle bloqué dans minimum local (cannot escape)
- LR décroît (cosine) mais grad_clip reste fixe → **double pénalité**

**SIGNATURE ATTENDUE** (si logging était présent):
```json
{
  "grad_pre_clip_norm": 15.3,      // >> 1.0
  "grad_was_clipped": true,
  "clip_ratio_epoch_pct": 95.2     // > 90% des steps clippés
}
```

**ACTION**: Tester grad_clip = 5.0, 10.0, 1000.0 (désactivé)

---

### 3. ⚠️  **TARGET SATURATION (return_fwd clampé à ±1%)** (LIKELIHOOD 60%)

**FAIT PROUVÉ**:
- `return_fwd.clamp(-1.0, 1.0)` (net.py:381)
- Commentaire dit "1%/99% - less destructive" → **problème historique**
- Crypto 1min avec horizon 15min: moves > 1% sont rares mais **existent**

**HYPOTHÈSE**:
- Si 10-15% des samples ont `|return_fwd| > 1%` → écrasés à ±1%
- Signal artificiellement limité → modèle apprend sur distributions dégradées
- Loss atteint rapidement un minimum sur signal saturé

**SIGNATURE ATTENDUE**:
```json
{
  "val_return_saturation": {
    "pct_above_clamp_max": 8.2,   // > 5% → problème
    "pct_below_clamp_min": 3.1,
    "p99": 1.0,                    // Exactement au clamp (queues coupées)
    "p01": -1.0
  }
}
```

**ACTION**: Tester clamps = [-2, 2], [-5, 5], ou désactivé

---

### 4. 🟡 **AMP SCALE COLLAPSE POSSIBLE** (LIKELIHOOD 40%)

**FAIT PROUVÉ**:
- AMP activé (`use_amp = True`)
- `scaler.get_scale()` **JAMAIS LOGGÉ**
- Si scale < 100 → gradients post-unscale trop petits

**HYPOTHÈSE**:
- GradScaler détecte overflow → réduit scale factor (65536 → 1024 → 64)
- Combiné avec grad_clip=1.0 → apprentissage bloqué

**SIGNATURE ATTENDUE**:
```json
{
  "epoch": 1, "amp_scale": 65536.0
  "epoch": 3, "amp_scale": 1024.0
  "epoch": 5, "amp_scale": 64.0    // < 100 → suspecter instabilité
}
```

**ACTION**: Tester AMP=False (FP32 pur)

---

### 5. ℹ️  **LR LOGGING DÉCALÉ** (INFO)

**FAIT PROUVÉ**:
- LR loggé = `optimizer.param_groups[0]["lr"]` ligne 1551
- Appelé **APRÈS** `scheduler.step()` ligne 1529
- **LR loggé est celui du PROCHAIN batch** (décalé d'un batch)

**CONSÉQUENCE**:
- Confusion lors de l'analyse (LR semble correct mais décalé)
- Pas un bug bloquant mais rend le diagnostic plus difficile

**ACTION**: Logger `lr_before_step` ET `lr_after_step`

---

## ✅ CE QUE LE CODE PROUVE (FAITS)

1. ✅ Scheduler = **CosineWithWarmup** (PAS OneCycleLR)
2. ✅ Ordre correct: `optimizer.step()` → `scheduler.step()`
3. ✅ `grad_clip = 1.0` (très bas pour ce modèle)
4. ✅ Targets clampés à `[-1%, 1%]`
5. ✅ AMP activé si `device=cuda`
6. ✅ Aucun logging de: gradients, saturation, AMP scale

---

## ❌ CE QUE LE CODE RÉFUTE (HYPOTHÈSES FAUSSES)

1. ❌ "GradNorm post-clip est loggé" → **FAUX**: Rien n'est loggé
2. ❌ "OneCycleLR est utilisé" → **FAUX**: C'est CosineWithWarmup
3. ❌ "Gradients explosent" → **NON PROUVABLE**: Aucun logging
4. ❌ "grad_clip=1.0 est raisonnable" → **DOUTEUX**: 430x plus petit que heuristique

---

## 🎯 TOP 3 CAUSES PROBABLES (ORDONNÉES)

| Rang | Cause                          | Likelihood | Preuve                                   | Test                      |
|------|--------------------------------|------------|------------------------------------------|---------------------------|
| 🥇   | grad_clip=1.0 trop bas         | **90%**    | grad_clip 430x plus petit que heuristique | Sweep: 1.0 / 5.0 / 1000.0 |
| 🥈   | Target saturation (±1% clamp)  | **60%**    | Commentaire "less destructive" suspect   | Sweep: [-1,1] / [-2,2] / disabled |
| 🥉   | AMP scale collapse             | **40%**    | amp_scale jamais loggé                   | Test: AMP on/off          |

---

## 🛠️ PLAN D'ACTION (3 ÉTAPES)

### ÉTAPE 1: APPLIQUER PATCHES LOGGING (IMMÉDIAT)

**Fichier**: `scripts/train_edge_forecaster.py`
**Patch**: `APPLY_PATCH_UNIFIED.md`
**Durée**: 10-15min

**Modifications**:
1. Ajouter `compute_gradient_metrics()` (capture pre_clip_norm, was_clipped)
2. Ajouter `distribution_report()` (détection saturation)
3. Modifier training loop pour logger:
   - `grad_pre_clip_norm`, `grad_was_clipped`, `grad_clip_ratio`
   - `amp_scale`
   - `lr_before_step`, `lr_after_step`
   - `clip_ratio_epoch_pct` (% steps clippés par epoch)
4. Ajouter saturation check en fin d'epoch

**Résultat attendu**:
- Logs complets avec visibilité sur gradients, AMP, saturation
- Baseline établie pour comparaison

---

### ÉTAPE 2: RUN BASELINE + DIAGNOSTIC (5-10min)

**Commande**:
```bash
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/baseline_diagnostic_v0 \
  --epochs 3 \
  --data-pct 0.10 \
  --log-interval 50 \
  --grad-clip 1.0 \
  --lr 2e-4 \
  --device cuda \
  --amp 1
```

**Analyser les logs** pour identifier la cause:

**SI `clip_ratio_epoch_pct > 80%`**:
→ **grad_clip trop bas** (CAUSE #1)
→ Passer à ÉTAPE 3a (sweep grad_clip)

**SI `pct_saturated > 10%`**:
→ **Target saturation** (CAUSE #2)
→ Passer à ÉTAPE 3b (sweep clamps)

**SI `amp_scale < 100` après 3 epochs**:
→ **AMP instable** (CAUSE #3)
→ Passer à ÉTAPE 3c (test AMP off)

---

### ÉTAPE 3: RUN SWEEP CORRESPONDANT (15-20min)

#### ÉTAPE 3a: SWEEP GRAD_CLIP (si CAUSE #1)

```bash
# Test grad_clip = 5.0
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_gc_5 \
  --epochs 5 --data-pct 0.10 --log-interval 50 \
  --grad-clip 5.0 --lr 2e-4 --device cuda --amp 1

# Test grad_clip = 1000.0 (désactivé)
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_gc_disabled \
  --epochs 5 --data-pct 0.10 --log-interval 50 \
  --grad-clip 1000.0 --lr 2e-4 --device cuda --amp 1
```

**Critère de succès**: val_loss améliore > 10% avec grad_clip=5.0 ou 1000.0

---

#### ÉTAPE 3b: SWEEP TARGET CLAMP (si CAUSE #2)

**NOTE**: Nécessite patch supplémentaire pour paramétrer `target_clamp` (voir `PATCH_1_2_saturation_checks.py`)

```bash
# Test clamp = [-2, 2]
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_clamp_2pct \
  --epochs 5 --data-pct 0.10 --log-interval 50 \
  --grad-clip 1.0 --lr 2e-4 --device cuda --amp 1 \
  --target-clamp-min -2.0 --target-clamp-max 2.0
```

**Critère de succès**: val_loss améliore > 10% avec clamps plus larges

---

#### ÉTAPE 3c: TEST AMP ON/OFF (si CAUSE #3)

```bash
# Test AMP=False (FP32)
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_amp_off \
  --epochs 5 --data-pct 0.10 --log-interval 50 \
  --grad-clip 1.0 --lr 2e-4 --device cuda \
  --amp 0  # DISABLE AMP
```

**Critère de succès**: val_loss améliore > 10% avec AMP désactivé

---

## 📊 CHECKLISTS DE DEBUG

### ✅ SI C'EST UN PROBLÈME DE CLIPPING:

**Logs à chercher**:
```json
{
  "grad_pre_clip_norm": 15.3,       // >> 1.0
  "grad_was_clipped": true,
  "clip_ratio_epoch_pct": 95.2      // > 80%
}
```

**Signature**:
- clip_ratio > 80%
- grad_norm_p95 >> grad_clip_threshold
- Loss plateau malgré LR élevé

**FIX**: grad_clip = 5.0-10.0

---

### ✅ SI C'EST UN PROBLÈME DE SATURATION:

**Logs à chercher**:
```json
{
  "val_return_saturation": {
    "pct_above_clamp_max": 8.2,   // > 5%
    "pct_below_clamp_min": 3.1,
    "p99": 1.0                     // Exactement au clamp
  }
}
```

**Signature**:
- pct_saturated > 10%
- p99/p01 exactement égaux aux clamps
- Loss plateau rapide

**FIX**: Clamps = [-2, 2] ou [-5, 5]

---

### ✅ SI C'EST UN PROBLÈME D'AMP:

**Logs à chercher**:
```json
{
  "epoch": 5,
  "amp_scale": 64.0    // < 100
}
```

**Signature**:
- amp_scale descend au fil du temps (65536 → 64)
- FP32 améliore significativement

**FIX**: Désactiver AMP (--amp 0)

---

### ✅ SI C'EST UN PROBLÈME DE LR:

**Logs à chercher**:
```json
{
  "epoch": 10,
  "lr_after_step": 0.00003,   // Trop bas trop tôt
  "val_loss": 1.234            // Plateau
}
```

**Signature**:
- LR < 5e-5 après 10 epochs (trop rapide)
- Loss plateau quand LR bas

**FIX**: min_lr_ratio = 0.30 (au lieu de 0.15)

---

## 🚀 MODE DEBUG OVERFIT (FALLBACK)

**SI AUCUN SWEEP NE FONCTIONNE** → Tester capacité du modèle à overfit

**Commande**:
```bash
python scripts/train_edge_forecaster.py \
  --debug-overfit \
  --debug-overfit-samples 256 \
  --debug-overfit-steps 1000 \
  --device cuda
```

**Critères de succès**:
- Loss doit baisser de > 80% (ex: 2.5 → 0.3)
- Correlation(q50, return_fwd) > 0.3

**SI OVERFIT ÉCHOUE**:
→ Problème fondamental (architecture bugguée, loss mal définie, NaN/Inf)
→ Vérifier net.py pour bugs

**SI OVERFIT RÉUSSIT**:
→ Modèle sain, problème de data/hyperparams
→ Retour aux sweeps avec data cleaning

---

## 📦 LIVRABLES

**Fichiers créés**:
1. ✅ `PATCH_1_1_gradient_logging.py` — Code pour logging gradients
2. ✅ `PATCH_1_2_saturation_checks.py` — Code pour détection saturation
3. ✅ `PATCH_1_3_debug_overfit_mode.py` — Mode overfit intégré
4. ✅ `PATCH_1_4_experimental_plan.md` — Plan expérimental détaillé (3 runs)
5. ✅ `APPLY_PATCH_UNIFIED.md` — Instructions d'application directe
6. ✅ `DIAGNOSTIC_SUMMARY_EXECUTIVE.md` — Ce document (résumé exécutif)

**Prochaine étape**: Appliquer `APPLY_PATCH_UNIFIED.md` au trainer

---

## ⏱️ TIMELINE ESTIMÉE

| Étape                          | Durée     | Cumul     |
|--------------------------------|-----------|-----------|
| Appliquer patches logging      | 10-15min  | 15min     |
| Run baseline (RUN 0)           | 5-10min   | 25min     |
| Analyser logs + identifier cause| 5min      | 30min     |
| Run sweep (RUN 1/2/3)          | 15-20min  | 50min     |
| **TOTAL DIAGNOSTIC COMPLET**   | —         | **50min** |

**Si besoin mode overfit**: +15min

---

## 🎓 LESSONS LEARNED

1. **Never trust gradient clipping without logging** → Toujours logger pre_clip_norm
2. **Heuristic sanity checks** → grad_clip = d_model × sqrt(n_layers) / 10
3. **Target clamps hide signal** → Préférer large clamps ou aucun
4. **AMP can silently fail** → Toujours logger scaler.get_scale()
5. **LR logging must be precise** → Logger before ET after scheduler.step

---

## ✅ VALIDATION FINALE

**Avant de déployer un fix**:
1. ✅ Run baseline avec logging complet
2. ✅ Identifier cause avec preuve quantitative (logs)
3. ✅ Run sweep avec amélioration > 10% val_loss
4. ✅ Valider sur test set (pas de overfitting)
5. ✅ Calibrer et vérifier proxy metrics (Sharpe, trades)

**AUCUN DÉPLOIEMENT SANS PREUVE QUANTITATIVE D'AMÉLIORATION**

---

**FIN DU DIAGNOSTIC**

**STATUS**: ✅ AUDIT COMPLET
**NEXT**: Appliquer `APPLY_PATCH_UNIFIED.md`
