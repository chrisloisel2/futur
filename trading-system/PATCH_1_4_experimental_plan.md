# PLAN EXPÉRIMENTAL NIVEAU 2 (3 RUNS MAXIMUM)

**OBJECTIF**: Isoler la vraie cause du plateau et valider la correction

**STRATÉGIE**: Modifier UNE SEULE variable à la fois, mesurer l'impact

**DONNÉES**: 10% du dataset (train) pour runs rapides (~5-10min par run)

**DURÉE**: 3-5 epochs max par run (assez pour voir si loss descend)

---

## RUN 0: BASELINE (RÉFÉRENCE)

**Objectif**: Établir la baseline avec logging complet

**Config**:
```python
# Hyperparams (INCHANGÉS)
grad_clip = 1.0
lr = 2e-4
weight_decay = 1e-3
target_clamp = [-1.0, 1.0]
use_amp = True
epochs = 3  # Réduit pour vitesse
data_pct = 0.10  # 10% du dataset

# Logging (PATCHES 1.1 + 1.2 APPLIQUÉS)
log_interval = 50  # Log toutes les 50 batches
log_gradients = True
log_saturation = True
log_amp_scale = True
```

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

**Métriques à observer**:
1. `grad_pre_clip_norm` (p50, p95, max) par epoch
2. `grad_clip_ratio_epoch_pct` (% de steps clippés)
3. `saturation.return_fwd_raw.pct_above_clamp_max` + `pct_below_clamp_min` (total saturation %)
4. `amp_scale` (progression au fil des epochs)
5. `val_loss` par epoch (doit descendre si modèle sain)

**Critères de succès**:
- Run complète sans crash
- Tous les logs sont présents
- Baseline établie pour comparaison

**Temps estimé**: 5-10min

---

## RUN 1: SWEEP GRAD_CLIP (HYPOTHÈSE #1 - LIKELIHOOD 90%)

**Objectif**: Tester si grad_clip=1.0 est trop restrictif

**Hypothèse**: Si clip_ratio > 80%, alors grad_clip bride l'apprentissage

**Config**:
```python
# 3 valeurs de grad_clip en parallèle (ou séquentiel si pas de GPU multiple)
grad_clip_values = [1.0, 5.0, 1000.0]  # 1.0 = baseline, 5.0 = 5x, 1000.0 = désactivé

# Autres hyperparams INCHANGÉS
lr = 2e-4
weight_decay = 1e-3
target_clamp = [-1.0, 1.0]
use_amp = True
epochs = 5  # 5 epochs pour voir divergence
data_pct = 0.10
```

**Commandes**:
```bash
# Run 1a: grad_clip=1.0 (baseline, déjà fait en RUN 0)

# Run 1b: grad_clip=5.0
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_gradclip_5 \
  --epochs 5 \
  --data-pct 0.10 \
  --log-interval 50 \
  --grad-clip 5.0 \
  --lr 2e-4 \
  --device cuda \
  --amp 1

# Run 1c: grad_clip=1000.0 (désactivé)
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_gradclip_disabled \
  --epochs 5 \
  --data-pct 0.10 \
  --log-interval 50 \
  --grad-clip 1000.0 \
  --lr 2e-4 \
  --device cuda \
  --amp 1
```

**Métriques à comparer**:
| Metric                    | grad_clip=1.0 | grad_clip=5.0 | grad_clip=1000.0 |
|---------------------------|---------------|---------------|------------------|
| grad_clip_ratio_epoch     | ?%            | ?%            | ?%               |
| val_loss (epoch 5)        | ?             | ?             | ?                |
| train_loss (epoch 5)      | ?             | ?             | ?                |
| grad_norm_p95             | ?             | ?             | ?                |
| proxy_score (epoch 5)     | ?             | ?             | ?                |

**Critères de succès**:
1. **Si grad_clip=5.0 ou 1000.0 améliore val_loss de > 10%** → HYPOTHÈSE VALIDÉE
2. **Si grad_clip_ratio baisse de 80% → 20%** avec amélioration loss → CAUSÉ PAR CLIPPING
3. **Si grad_clip=1000.0 cause explosion (loss → NaN)** → grad_clip=5.0 est optimal

**Action si succès**:
- Utiliser grad_clip optimal (5.0 ou 10.0) pour production
- Re-train sur 100% data avec meilleur grad_clip

**Action si échec** (pas d'amélioration):
- grad_clip n'est PAS la cause
- Passer à RUN 2 (saturation)

**Temps estimé**: 15-20min (3 runs en parallèle si GPU multiple, sinon 45-60min séquentiel)

---

## RUN 2: SWEEP TARGET CLAMP (HYPOTHÈSE #2 - LIKELIHOOD 60%)

**Objectif**: Tester si target clamp à ±1% écrase le signal

**Hypothèse**: Si saturation > 10%, alors clamps dégradent la loss

**Pré-requis**: Run RUN 0 et vérifier `saturation.pct_above_clamp_max + pct_below_clamp_min`

**Config**:
```python
# Si saturation < 5% → SKIP CE RUN (pas de problème)
# Si saturation > 10% → TEST ces valeurs

target_clamp_values = [
    [-1.0, 1.0],   # Baseline (actuel)
    [-2.0, 2.0],   # 2x plus large
    [-100.0, 100.0],  # Désactivé (pas de clamp)
]

# Autres hyperparams INCHANGÉS
grad_clip = 1.0  # OU le meilleur de RUN 1 si RUN 1 a réussi
lr = 2e-4
weight_decay = 1e-3
use_amp = True
epochs = 5
data_pct = 0.10
```

**NOTE**: Nécessite modification du code pour paramétrer `target_clamp`

**Patch nécessaire** (net.py:381):
```python
# AVANT:
return_fwd = targets[:, 0:1].clamp(-1.0, 1.0)

# APRÈS (ajouter argument à compute_loss):
def compute_loss(self, x_seq, targets, label_smoothing=0.0, regime_vec=None,
                 target_clamp_min=-1.0, target_clamp_max=1.0, diagnostic_mode=False):
    ...
    return_fwd = targets[:, 0:1].clamp(target_clamp_min, target_clamp_max)
    ...
```

**Commandes**:
```bash
# Run 2a: clamp=[-1, 1] (baseline, déjà fait)

# Run 2b: clamp=[-2, 2]
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_clamp_2pct \
  --epochs 5 \
  --data-pct 0.10 \
  --log-interval 50 \
  --grad-clip 1.0 \
  --lr 2e-4 \
  --target-clamp-min -2.0 \
  --target-clamp-max 2.0 \
  --device cuda \
  --amp 1

# Run 2c: clamp désactivé
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_clamp_disabled \
  --epochs 5 \
  --data-pct 0.10 \
  --log-interval 50 \
  --grad-clip 1.0 \
  --lr 2e-4 \
  --target-clamp-min -100.0 \
  --target-clamp-max 100.0 \
  --device cuda \
  --amp 1
```

**Métriques à comparer**:
| Metric                    | clamp=[-1,1] | clamp=[-2,2] | clamp=disabled |
|---------------------------|--------------|--------------|----------------|
| saturation_pct            | ?%           | ?%           | 0%             |
| val_loss (epoch 5)        | ?            | ?            | ?              |
| train_loss (epoch 5)      | ?            | ?            | ?              |
| q50_mae                   | ?            | ?            | ?              |
| proxy_score (epoch 5)     | ?            | ?            | ?              |

**Critères de succès**:
1. **Si clamp=[-2,2] améliore val_loss de > 10%** → HYPOTHÈSE VALIDÉE
2. **Si saturation baisse de 15% → 3%** avec amélioration loss → CAUSÉ PAR SATURATION
3. **Si clamp désactivé améliore encore plus** → supprimer clamp entièrement

**Action si succès**:
- Utiliser clamps plus larges ([-2, 2] ou [-5, 5]) ou supprimer
- Re-train sur 100% data

**Action si échec**:
- Saturation n'est PAS la cause
- Passer à RUN 3 (AMP)

**Temps estimé**: 15-20min (si parallèle), sinon 45-60min

---

## RUN 3: TEST AMP ON/OFF (HYPOTHÈSE #3 - LIKELIHOOD 40%)

**Objectif**: Tester si AMP scale collapse bride l'apprentissage

**Hypothèse**: Si amp_scale < 100 après 3 epochs, alors AMP dégrade les gradients

**Pré-requis**: Run RUN 0 et vérifier `amp_scale` progression

**Config**:
```python
# 2 valeurs de AMP
use_amp_values = [True, False]

# Autres hyperparams INCHANGÉS
grad_clip = 1.0  # OU le meilleur de RUN 1
lr = 2e-4
weight_decay = 1e-3
target_clamp = [-1.0, 1.0]  # OU le meilleur de RUN 2
epochs = 5
data_pct = 0.10
```

**Commandes**:
```bash
# Run 3a: AMP=True (baseline, déjà fait)

# Run 3b: AMP=False (FP32 pur)
python scripts/train_edge_forecaster.py \
  --output artifacts/models/edge/sweep_amp_disabled \
  --epochs 5 \
  --data-pct 0.10 \
  --log-interval 50 \
  --grad-clip 1.0 \
  --lr 2e-4 \
  --device cuda \
  --amp 0  # DISABLE AMP
```

**Métriques à comparer**:
| Metric                    | AMP=True | AMP=False (FP32) |
|---------------------------|----------|------------------|
| amp_scale (epoch 5)       | ?        | N/A              |
| val_loss (epoch 5)        | ?        | ?                |
| train_loss (epoch 5)      | ?        | ?                |
| training_time (sec/epoch) | ?        | ? (probablement +30%) |
| proxy_score (epoch 5)     | ?        | ?                |

**Critères de succès**:
1. **Si AMP=False améliore val_loss de > 10%** → HYPOTHÈSE VALIDÉE
2. **Si amp_scale < 100 dans AMP=True** et FP32 meilleur → AMP instable
3. **Si AMP=False cause OOM** → revenir à AMP mais avec cast explicites

**Action si succès**:
- Désactiver AMP pour production (ou utiliser bfloat16 si disponible)
- Re-train sur 100% data sans AMP

**Action si échec**:
- AMP n'est PAS la cause
- Retour à l'analyse: soit bug dans architecture, soit problème de data

**Temps estimé**: 10-15min (1 seul run supplémentaire)

---

## RÉSUMÉ PLAN EXPÉRIMENTAL

| Run   | Variable testée  | Valeurs                    | Durée estimée | Action si succès                     |
|-------|------------------|----------------------------|---------------|--------------------------------------|
| RUN 0 | Baseline         | (logging complet)          | 5-10min       | Établir baseline                     |
| RUN 1 | grad_clip        | 1.0 / 5.0 / 1000.0         | 15-20min      | Utiliser grad_clip optimal           |
| RUN 2 | target_clamp     | [-1,1] / [-2,2] / disabled | 15-20min      | Élargir ou supprimer clamps          |
| RUN 3 | use_amp          | True / False               | 10-15min      | Désactiver AMP si instable           |

**TOTAL**: 45-65min (si runs séquentiels)
**TOTAL**: 15-30min (si runs parallèles avec GPU multiples)

---

## CRITÈRES DE RÉUSSITE GLOBAUX

**SUCCESS**: Au moins UN run montre amélioration > 10% de val_loss par rapport à baseline

**FAILURE**: Aucun run n'améliore val_loss → problème plus profond (architecture, data, loss function)

**NEXT STEPS SI SUCCESS**:
1. Combiner les meilleures config (ex: grad_clip=5.0 + target_clamp=[-2,2] + amp=False)
2. Run final sur 100% data, 40 epochs
3. Valider sur test set
4. Calibrer et déployer

**NEXT STEPS SI FAILURE**:
1. Run overfit test (PATCH 1.3) pour vérifier si modèle peut apprendre
2. Si overfit échoue → problème fondamental (architecture bugguée, loss mal définie, NaN/Inf)
3. Si overfit réussit → problème de data (labels bruités, distribution shift, leakage)

---

## AUTOMATION SCRIPT (OPTIONNEL)

```bash
#!/bin/bash
# sweep_all.sh - Run all experiments in sequence

BASE_CMD="python scripts/train_edge_forecaster.py --epochs 5 --data-pct 0.10 --log-interval 50 --device cuda"

# RUN 0: Baseline
echo "=== RUN 0: BASELINE ==="
$BASE_CMD --output artifacts/models/edge/baseline_v0 --grad-clip 1.0 --lr 2e-4 --amp 1

# RUN 1: Grad clip sweep
echo "=== RUN 1: GRAD CLIP SWEEP ==="
$BASE_CMD --output artifacts/models/edge/sweep_gc_5 --grad-clip 5.0 --lr 2e-4 --amp 1
$BASE_CMD --output artifacts/models/edge/sweep_gc_1000 --grad-clip 1000.0 --lr 2e-4 --amp 1

# RUN 2: Target clamp sweep (si patch appliqué)
echo "=== RUN 2: TARGET CLAMP SWEEP ==="
# $BASE_CMD --output artifacts/models/edge/sweep_clamp_2 --grad-clip 1.0 --lr 2e-4 --amp 1 --target-clamp-max 2.0
# $BASE_CMD --output artifacts/models/edge/sweep_clamp_100 --grad-clip 1.0 --lr 2e-4 --amp 1 --target-clamp-max 100.0

# RUN 3: AMP on/off
echo "=== RUN 3: AMP SWEEP ==="
$BASE_CMD --output artifacts/models/edge/sweep_amp_off --grad-clip 1.0 --lr 2e-4 --amp 0

echo "=== ALL RUNS COMPLETE ==="
echo "Check artifacts/models/edge/ for results"
```

---

## CHECKLISTS DE DEBUG (CE QU'ON DOIT VOIR DANS LES LOGS)

### ✅ SI C'EST UN PROBLÈME DE CLIPPING:

**Logs attendus**:
```json
{
  "msg": "BATCH_DIAGNOSTIC",
  "grad_pre_clip_norm": 15.3,         // >> grad_clip_threshold
  "grad_was_clipped": true,
  "grad_clip_threshold": 1.0,
  "grad_clip_ratio": 15.3,            // ratio > 10
}

{
  "msg": "EPOCH_END",
  "gradient_summary": {
    "clip_ratio_epoch_pct": 95.2,    // > 90% → presque tous les batches clippés
    "grad_norm_p95": 22.1,            // >> 1.0
    "grad_norm_median": 12.5,         // >> 1.0
  }
}
```

**Signature**:
- `clip_ratio_epoch_pct` > 80%
- `grad_norm_p95` >> `grad_clip_threshold`
- Loss plateau malgré LR encore élevé
- **FIX**: Augmenter `grad_clip` à 5.0-10.0

---

### ✅ SI C'EST UN PROBLÈME DE LR:

**Logs attendus**:
```json
{
  "msg": "BATCH_DIAGNOSTIC",
  "lr_before_step": 0.0002,
  "lr_after_step": 0.00019,          // Descend normalement (cosine)
}

{
  "msg": "EPOCH_END",
  "lr": 0.00012,                     // LR trop bas trop tôt
  "val_loss": 1.234,                 // Plateau
}
```

**Signature**:
- LR descend trop vite (cosine min_lr_ratio trop bas)
- Loss plateau quand LR < 5e-5
- Amélioration si LR reset ou augmenté
- **FIX**: Augmenter `min_lr_ratio` de 0.15 → 0.30, ou utiliser OneCycleLR

---

### ✅ SI C'EST UN PROBLÈME DE SATURATION:

**Logs attendus**:
```json
{
  "msg": "SATURATION_CHECK_EPOCH",
  "val_return_saturation": {
    "pct_above_clamp_max": 8.2,      // > 5% → signal écrasé
    "pct_below_clamp_min": 3.1,      // > 5%
    "p99": 1.0,                       // Exactement au clamp (suspect)
    "p01": -1.0,
  }
}
```

**Signature**:
- `pct_above_clamp_max + pct_below_clamp_min` > 10%
- `p99` ou `p01` exactement égaux aux clamps (queues coupées)
- Loss plateau rapide (modèle apprend sur signal dégradé)
- **FIX**: Élargir clamps à [-2, 2] ou [-5, 5], ou supprimer

---

### ✅ SI C'EST UN PROBLÈME D'AMP:

**Logs attendus**:
```json
{
  "msg": "BATCH_DIAGNOSTIC",
  "amp_scale": 512.0,                // Descend au fil du temps
}

{
  "msg": "EPOCH_END",
  "epoch": 5,
  "amp_scale": 64.0,                 // < 100 → suspecter instabilité
}
```

**Signature**:
- `amp_scale` descend de 65536 → 1024 → 64 (overflow détecté)
- Si scale < 100 → gradients post-unscale trop petits
- FP32 baseline améliore significativement
- **FIX**: Désactiver AMP ou utiliser bfloat16

---

## DÉCISION FINALE

**Après les 3 runs** :

1. **Si RUN 1 réussit** (grad_clip) → Utiliser grad_clip optimal, re-train
2. **Si RUN 2 réussit** (saturation) → Élargir/supprimer clamps, re-train
3. **Si RUN 3 réussit** (AMP) → Désactiver AMP, re-train
4. **Si AUCUN ne réussit** → Run overfit test (PATCH 1.3)
   - Si overfit échoue → BUG FONDAMENTAL (architecture/loss)
   - Si overfit réussit → PROBLÈME DE DATA (labels/distribution)

**FIN DU PLAN EXPÉRIMENTAL**
