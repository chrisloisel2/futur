# Architecture à Détection de Régimes - Guide d'Implémentation

## 📋 Table des Matières

1. [Justification Mathématique](#justification)
2. [Architecture Détaillée](#architecture)
3. [Intégration avec model.py](#integration)
4. [Entraînement](#training)
5. [Critères de Succès](#success)
6. [FAQ Technique](#faq)

---

## 1. Justification Mathématique {#justification}

### Pourquoi la Direction Globale est Non-Stationnaire

La prédiction de direction globale sur marchés financiers viole l'hypothèse de stationnarité :

**Distribution conditionnelle non-constante :**
```
P(sign(r_{t+1}) | F_t) ≠ constante
```

Dans un **marché en trend**, la probabilité UP est élevée :
```
P(UP | trend) ≈ 0.65-0.75
```

Dans un **marché mean-reverting**, après une hausse :
```
P(UP | mean_revert, r_t > 0) ≈ 0.35-0.45
```

Un modèle global apprend une **moyenne** de ces distributions incompatibles :
```
P_global(UP) ≈ 0.50-0.55  (inutile, proche du hasard)
```

### Décomposition de la Variance

Pour une prédiction ŷ sur plusieurs régimes τ :

```
Var[ŷ] = E_τ[Var[ŷ | τ]] + Var_τ[E[ŷ | τ]]
         ︸─────────────︸   ︸───────────────︸
         intra-regime      inter-regime
         (bruit)           (signal incompatible)
```

**Problème du modèle global :**
- Apprend E[ŷ] en moyennant sur tous les régimes
- Maximise Var_τ[E[ŷ | τ]] → variance élevée → mauvaise généralisation

**Solution par régimes :**
- Chaque expert apprend E[ŷ | τ] dans un seul régime
- Minimise Var[ŷ | τ] car distribution homogène
- La variance inter-régimes est **gérée par le classifieur**, pas par l'expert

### Pourquoi le MoE Augmente la Capacité Effective

**Mixture of Experts (soft gating) :**
```
ŷ = Σᵢ p(τ=i | x) · Expert_i(x)
```

**Capacité effective :**
- Modèle global : C params, apprend 1 fonction moyennée
- MoE : k experts × (C/k) params = C params total, apprend k fonctions spécialisées

**Avantage :**
- Même budget de paramètres
- Chaque expert se spécialise → variance intra-régime plus faible
- Pas de sur-apprentissage si régularisation forte (dropout, entropy reg)

**Régularisation entropy :**
```
L_entropy = -H(p_regime) = Σᵢ pᵢ log pᵢ
```

Empêche le collapse où un seul expert est utilisé (retour au modèle global).

---

## 2. Architecture Détaillée {#architecture}

### Pipeline Complet

```
Input: [B, L, F]
    ↓
┌───────────────────────────────────────┐
│ MODULE 1: REGIME CLASSIFIER           │
│                                       │
│ Input [B, L, F]                       │
│   ↓                                   │
│ Dense(d_regime) + LayerNorm           │
│   ↓                                   │
│ CNN1D / TCN (3 layers, causal)        │
│   ↓                                   │
│ GlobalAveragePooling                  │
│   ↓                                   │
│ Dense(d_regime) → Dense(5) → Softmax  │
│   ↓                                   │
│ p_regime: [B, 5]                      │
└───────────────────────────────────────┘
    ↓
┌───────────────────────────────────────┐
│ MODULE 2: EXPERTS (×5)                │
│                                       │
│ Expert_i (i ∈ {0..4}):                │
│                                       │
│ Input [B, L, F]                       │
│   ↓                                   │
│ Dense(d_expert) + LayerNorm           │
│   ↓                                   │
│ TCN / Transformer (2 layers, causal)  │
│   ↓                                   │
│ GlobalAveragePooling                  │
│   ↓                                   │
│ Shared: Dense(d_expert)               │
│   ↓                                   │
│ ├─→ ret_head: Dense(H)                │
│ └─→ rv_head: Dense(1) + Softplus      │
│                                       │
│ Output: {ret: [B, H], rv: [B]}        │
└───────────────────────────────────────┘
    ↓
┌───────────────────────────────────────┐
│ GATING                                │
│                                       │
│ Hard:                                 │
│   regime = argmax(p_regime)           │
│   output = Expert_regime(x)           │
│                                       │
│ Soft (MoE):                           │
│   output = Σᵢ p_regime[i] · Expert_i(x) │
└───────────────────────────────────────┘
    ↓
Output: {ret: [B, H], rv: [B]}
```

### Définition Formelle des Régimes

Les régimes sont **dérivés automatiquement** des features observables, sans labels manuels.

#### Régime 0: TREND (Tendance Directionnelle)

**Critères mathématiques :**
```python
slope_ema = polyfit(dist_ema_20, degree=1)[0]  # Pente EMA
direction_stability = 1 - (n_direction_changes / lookback)

score_trend = (|slope| > Q₇₅(|slope|)) × direction_stability
```

**Interprétation :**
- Prix éloigné de l'EMA avec pente monotone
- Peu de changements de direction (faible variance directionnelle)

#### Régime 1: MEAN_REVERT (Retour à la Moyenne)

**Critères mathématiques :**
```python
rsi_extreme = (RSI < 30) OR (RSI > 70)
anticorrelation = mean(sign(dist_ema) ≠ sign(ret))

score_mean_revert = rsi_extreme × anticorrelation
```

**Interprétation :**
- RSI en zone de surachat/survente
- Prix revient vers EMA (anticorrélation distance/return)

#### Régime 2: HIGH_VOL (Volatilité Élevée)

**Critères mathématiques :**
```python
score_high_vol = (RV_current > Q₇₅(RV))
```

**Interprétation :**
- Volatilité réalisée au-dessus du 75ème percentile
- Mouvements amples, risque accru

#### Régime 3: LOW_VOL (Volatilité Faible)

**Critères mathématiques :**
```python
score_low_vol = (RV_current < Q₂₅(RV))
```

**Interprétation :**
- Volatilité réalisée sous le 25ème percentile
- Mouvements calmes, marché stable

#### Régime 4: RANGE (Consolidation)

**Critères mathématiques :**
```python
low_slope = |slope_ema| < Q₂₅(|slope|)
low_distance = |dist_ema| < Q₂₅(|dist|)

score_range = low_slope × low_distance
```

**Interprétation :**
- Prix oscille autour de l'EMA sans tendance claire
- Range-bound, marché indécis

**Attribution du régime :**
```python
regime = argmax([score_trend, score_mean_revert, score_high_vol, score_low_vol, score_range])
```

### Propriétés des Régimes

✅ **Pas de labels manuels** : calculés algorithmiquement
✅ **Calculables online** : pas de fuite temporelle (lookback only)
✅ **Stables temporellement** : quantiles sur fenêtre glissante
✅ **Mutuellement exclusifs** : argmax garantit un seul régime par timestep

---

## 3. Intégration avec model.py {#integration}

### Option A : Remplacement Complet

Remplacer `TinyRecursiveMarketModel` par `RegimeAwareMarketModel` dans `model.py`.

**Avantages :**
- Architecture complètement nouvelle
- Pas de dette technique de l'ancien modèle

**Inconvénients :**
- Perte de l'architecture Transformer + RecursiveMemory existante
- Nécessite réentraînement complet

### Option B : Pipeline Parallèle (Recommandé)

Garder `model.py` intact, créer `regime_pipeline.py` séparé.

**Avantages :**
- Comparaison A/B directe
- Possibilité d'ensemble (moyenne des deux modèles)

**Inconvénients :**
- Duplication de code (scaler, windowing)

### Fichier d'Intégration : `regime_pipeline.py`

```python
"""
Pipeline complet pour l'entraînement du modèle à régimes.
"""
import os
import numpy as np
import tensorflow as tf

from regime_aware_model import (
    RegimeConfig,
    RegimeAwareMarketModel,
    RegimeAwareTrainer,
    compute_regime_labels,
    compute_regime_statistics,
    evaluate_regime_expert_performance,
)

# Import from model.py (reuse scaler + windowing)
from model import (
    FEATURE_KEYS,
    RunningRobustScaler,
    iter_s3_jsonl,
    build_numpy_from_stream,
    make_windows,
    set_seed,
)


def train_regime_aware_model(
    Xw_train: np.ndarray,
    yret_train: np.ndarray,
    yrv_train: np.ndarray,
    Xw_val: np.ndarray,
    yret_val: np.ndarray,
    yrv_val: np.ndarray,
    X_train_full: np.ndarray,  # Needed for regime computation
    X_val_full: np.ndarray,
    cfg: RegimeConfig,
    out_dir: str = "regime_out",
):
    """
    Train regime-aware model end-to-end.

    Steps:
    1. Compute regime labels from full sequences
    2. Align regime labels with windows
    3. Two-phase training (optional)
    4. Evaluate per-regime performance
    """
    os.makedirs(out_dir, exist_ok=True)

    # 1) Compute regime labels
    print("Computing regime labels...")
    y_regime_train = compute_regime_labels(X_train_full, FEATURE_KEYS, lookback=cfg.lookback)
    y_regime_val = compute_regime_labels(X_val_full, FEATURE_KEYS, lookback=cfg.lookback)

    # Align with windows (take label at end of window)
    # make_windows uses X[s : s+lookback] → label at s+lookback
    # So y_regime_windows[i] corresponds to timestep s+lookback
    # We need to extract the correct indices

    # Simplified: if make_windows stride=1, window i ends at timestep lookback+i
    y_regime_train_windows = y_regime_train[cfg.lookback : cfg.lookback + len(Xw_train)]
    y_regime_val_windows = y_regime_val[cfg.lookback : cfg.lookback + len(Xw_val)]

    # Statistics
    print("\nTrain Regime Statistics:")
    stats_train = compute_regime_statistics(y_regime_train_windows)
    for k, v in stats_train.items():
        print(f"  {k}: {v:.2f}")

    print("\nValidation Regime Statistics:")
    stats_val = compute_regime_statistics(y_regime_val_windows)
    for k, v in stats_val.items():
        print(f"  {k}: {v:.2f}")

    # 2) Create model
    print("\nCreating regime-aware model...")
    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=Xw_train.shape[-1])

    # Build
    dummy = tf.zeros((1, cfg.lookback, Xw_train.shape[-1]), dtype=tf.float32)
    _ = model(dummy, training=False)
    print(f"Model parameters: {model.count_params():,}")

    # 3) Create trainer
    trainer = RegimeAwareTrainer(model=model, cfg=cfg)

    # 4) Training loop (simplified - replace with tf.data.Dataset for production)
    print("\nTraining...")

    # Convert to tf.data.Dataset
    ds_train = tf.data.Dataset.from_tensor_slices((
        Xw_train,
        y_regime_train_windows,
        yret_train,
        yrv_train,
    ))
    ds_train = ds_train.shuffle(10000).batch(cfg.batch_size).prefetch(2)

    ds_val = tf.data.Dataset.from_tensor_slices((
        Xw_val,
        y_regime_val_windows,
        yret_val,
        yrv_val,
    ))
    ds_val = ds_val.batch(cfg.batch_size).prefetch(2)

    best_val_loss = float('inf')
    patience_counter = 0
    max_patience = 5

    for epoch in range(cfg.epochs):
        print(f"\nEpoch {epoch+1}/{cfg.epochs}")

        # Train
        trainer.train_loss_tracker.reset_states()
        trainer.regime_acc_tracker.reset_states()

        for x_batch, regime_batch, ret_batch, rv_batch in ds_train:
            trainer.train_step(x_batch, regime_batch, ret_batch, rv_batch)

        train_loss = trainer.train_loss_tracker.result()
        regime_acc = trainer.regime_acc_tracker.result()

        # Validation
        trainer.val_loss_tracker.reset_states()

        for x_batch, regime_batch, ret_batch, rv_batch in ds_val:
            trainer.val_step(x_batch, regime_batch, ret_batch, rv_batch)

        val_loss = trainer.val_loss_tracker.result()

        print(f"  Train Loss: {train_loss:.4f} | Regime Acc: {regime_acc:.4f}")
        print(f"  Val Loss: {val_loss:.4f}")

        # Early stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            model.save_weights(os.path.join(out_dir, "best_weights.h5"))
        else:
            patience_counter += 1
            if patience_counter >= max_patience:
                print("Early stopping triggered")
                break

    # Load best weights
    model.load_weights(os.path.join(out_dir, "best_weights.h5"))

    # 5) Evaluate per-regime performance
    print("\n" + "="*80)
    print("EVALUATION: Per-Regime Expert Performance")
    print("="*80)

    results = evaluate_regime_expert_performance(
        model=model,
        X=Xw_val,
        y_regime=y_regime_val_windows,
        y_ret=yret_val,
        y_rv=yrv_val,
    )

    print("\nPer-Regime Metrics:")
    for regime, metrics in results.items():
        if regime == "regime_classification_acc":
            print(f"\n{'='*60}")
            print(f"Regime Classification Accuracy: {metrics:.2%}")
            print(f"{'='*60}")
        else:
            print(f"\n{regime}:")
            for k, v in metrics.items():
                if isinstance(v, float):
                    print(f"  {k}: {v:.4f}")
                else:
                    print(f"  {k}: {v}")

    # Save final model
    model.save(os.path.join(out_dir, "final_model.keras"))

    return model, results


def main():
    """Full pipeline matching model.py structure"""
    set_seed(RegimeConfig().seed)

    # Load from S3 (same as model.py)
    bucket = os.environ.get("S3_BUCKET", "").strip()
    prefix = os.environ.get("S3_PREFIX", "").strip()
    aws_profile = os.environ.get("AWS_PROFILE", "").strip() or None
    region = os.environ.get("AWS_REGION", "").strip() or None

    if not bucket or not prefix:
        raise RuntimeError("Set S3_BUCKET and S3_PREFIX environment variables")

    # 1) Fit scaler
    print("Loading data and fitting scaler...")
    scaler = RunningRobustScaler(feature_dim=len(FEATURE_KEYS), reservoir_size=200_000)
    stream1 = iter_s3_jsonl(bucket=bucket, prefix=prefix, region=region, aws_profile=aws_profile)
    X_all, y_ret, y_rv = build_numpy_from_stream(stream1, scaler=scaler, limit_rows=None)
    scaler.finalize()

    # 2) Transform
    X_all = scaler.transform(X_all)

    # 3) Windowing
    cfg = RegimeConfig()
    Xw, yret_h, ydir, yrv_h = make_windows(
        X_all, y_ret, y_rv,
        lookback=cfg.lookback,
        horizon=cfg.horizon,
        stride=1,
    )

    # 4) Temporal split
    n = Xw.shape[0]
    split = int(n * 0.9)

    Xw_train, Xw_val = Xw[:split], Xw[split:]
    yret_train, yret_val = yret_h[:split], yret_h[split:]
    yrv_train, yrv_val = yrv_h[:split], yrv_h[split:]

    # For regime computation, we need the full sequences (not windowed)
    # Split X_all at the same temporal point
    split_full = cfg.lookback + split
    X_train_full = X_all[:split_full]
    X_val_full = X_all[split_full:]

    # 5) Train
    model, results = train_regime_aware_model(
        Xw_train, yret_train, yrv_train,
        Xw_val, yret_val, yrv_val,
        X_train_full, X_val_full,
        cfg=cfg,
        out_dir="regime_out",
    )

    print("\n" + "="*80)
    print("TRAINING COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
```

---

## 4. Entraînement {#training}

### Option 1 : Entraînement Conjoint (Recommandé)

**Loss totale :**
```
L_total = w_regime · L_regime
        + w_ret · L_ret
        + w_rv · L_rv
        + w_entropy · L_entropy
```

**Composantes :**

1. **L_regime** : SparseCategoricalCrossentropy
   - Entraîne le classifieur de régime
   - Supervision par labels dérivés

2. **L_ret** : Huber(δ=1.0)
   - Régression des returns futurs
   - Agrégée sur tous les experts (MoE weighted)

3. **L_rv** : Huber(δ=0.01)
   - Régression de la volatilité
   - Clipping [1e-6, 1.0] pour stabilité

4. **L_entropy** : Regularization
   - Empêche collapse à un seul expert
   - `-H(p_regime) = Σ pᵢ log pᵢ`

**Pondération suggérée :**
```python
w_regime = 0.3
w_ret = 1.0
w_rv = 0.4
w_entropy = 0.01
```

### Option 2 : Entraînement en 2 Phases

**Phase 1 : Pre-train Regime Classifier (5 epochs)**
```python
# Freeze experts
for expert in model.experts:
    expert.trainable = False

# Train only classifier
L = L_regime
```

**Phase 2 : Joint Training (15 epochs)**
```python
# Unfreeze all
for expert in model.experts:
    expert.trainable = True

# Train full model
L = w_regime · L_regime + w_ret · L_ret + w_rv · L_rv + w_entropy · L_entropy
```

**Avantage :**
- Classifieur stable avant entraînement experts
- Experts partent d'une bonne attribution de régimes

**Inconvénient :**
- Plus long
- Risque d'overfitting du classifieur

### Métriques à Surveiller

**Durant l'entraînement :**

1. **train_loss / val_loss** : Standard
2. **regime_acc** : Accuracy de classification des régimes
   - Objectif : > 60% (bien au-dessus de 20% random pour 5 classes)
3. **entropy(p_regime)** : Entropie moyenne de la distribution
   - Objectif : proche de log(5) ≈ 1.6 (distribution uniforme)
   - Si < 0.5 → collapse vers un seul régime

**Post-entraînement :**

4. **Per-regime MAE** : MAE de ret/rv dans chaque régime
5. **Per-regime directional accuracy** : Signe du return cumulatif
   - Objectif : > 50% dans chaque régime (beat random)
6. **Regime switching rate** : Transitions / 1000 timesteps
   - Objectif : 50-200 (stable mais pas figé)

---

## 5. Critères de Succès {#success}

### Critère 1 : Stabilité Temporelle des Régimes

**Métrique :**
```python
switching_rate = n_regime_transitions / n_timesteps * 1000
```

**Seuil de succès :**
```
50 < switching_rate < 200
```

**Interprétation :**
- < 50 : régimes figés, sous-utilisation de la capacité
- 50-200 : bonne stabilité, régimes significatifs
- > 200 : switching trop rapide, régimes non-stables

### Critère 2 : Spécialisation des Experts

**Métrique :** Pour chaque régime τ :
```python
directional_acc_τ = accuracy(sign(Σ ret_pred), sign(Σ ret_true)) | regime = τ
```

**Seuil de succès :**
```
directional_acc_τ > 0.50 pour tout τ
```

**Interprétation :**
- = 0.50 : pas mieux que le hasard
- > 0.50 : expert a appris quelque chose de significatif dans son régime
- > 0.55 : bonne spécialisation

### Critère 3 : Pas de Direction Globale

**Métrique :**
```python
global_dir_acc = accuracy(sign(Σ ret_pred), sign(Σ ret_true))  # all samples
```

**Seuil de succès :**
```
0.48 < global_dir_acc < 0.52
```

**Interprétation :**
- Proche de 0.50 → modèle ne prédit PAS la direction globalement
- C'est **souhaitable** car direction globale est non-stationnaire
- La spécialisation par régime doit compenser

### Critère 4 : Classification des Régimes

**Métrique :**
```python
regime_acc = accuracy(argmax(p_regime), y_regime_true)
```

**Seuil de succès :**
```
regime_acc > 0.60
```

**Interprétation :**
- Random baseline : 0.20 (5 classes)
- > 0.60 : le classifieur identifie correctement les patterns de marché

---

## 6. FAQ Technique {#faq}

### Q1 : Pourquoi ne pas utiliser les labels de régime en production ?

**R :** Les labels sont calculés **pendant l'inférence** aussi.

```python
# Inference
regime_labels_current = compute_regime_labels(features_history, FEATURE_KEYS)
# Le classifieur apprend à prédire ces labels
```

Les labels ne sont **pas** manuels, donc calculables online.

### Q2 : Quid de la fuite temporelle dans compute_regime_labels ?

**R :** Aucune fuite si on utilise uniquement le lookback.

```python
# Pour prédire à t, on utilise features[t-lookback:t]
# Le régime est calculé sur cette fenêtre → pas d'accès au futur
```

### Q3 : Pourquoi TCN plutôt que Transformer pour les experts ?

**R :** Budget de paramètres.

| Component | Params |
|-----------|--------|
| Transformer (2 layers, d=64, heads=4) | ~80k |
| TCN (2 layers, d=64, k=3) | ~25k |

Avec 5 experts :
- TCN : 5 × 25k = 125k params
- Transformer : 5 × 80k = 400k params

TCN est plus léger → moins de sur-apprentissage.

### Q4 : Comment gérer les régimes déséquilibrés ?

**R :** Weighted loss sur L_regime.

```python
# Compute class weights
regime_counts = np.bincount(y_regime_train)
weights = 1.0 / (regime_counts + 1e-6)
weights = weights / weights.sum() * len(weights)  # Normalize

# Use in loss
loss_regime = tf.nn.sparse_softmax_cross_entropy_with_logits(
    labels=y_regime,
    logits=logits_regime,
)
loss_regime = loss_regime * tf.gather(weights, y_regime)
```

### Q5 : Peut-on ajouter un 6ème régime (ex: "crisis") ?

**R :** Oui, modifier :

```python
# Dans compute_regime_labels
score_crisis = (RV > Q₉₅(RV)) × (|ret| > Q₉₅(|ret|))
scores = [score_trend, ..., score_crisis]

# Dans RegimeConfig
n_regimes: int = 6
```

### Q6 : Comment interpréter les prédictions du modèle ?

**R :** Le modèle retourne :

```python
outputs = {
    "ret": [B, H],  # Returns futurs sur horizon H
    "rv": [B],      # Volatilité agrégée (RMS)
    "regime_probs": [B, 5]  # Distribution sur régimes (optionnel)
}
```

**Interprétation :**
- `regime_probs` : confiance du modèle sur le régime actuel
- `ret` : prédiction de return (non directionnelle globalement, mais spécialisée par régime)
- `rv` : incertitude (haute RV → éviter le trade ou réduire size)

**Exemple trading :**
```python
if regime_probs[HIGH_VOL] > 0.7:
    position_size *= 0.5  # Reduce size in high vol
elif regime_probs[TREND] > 0.6 and sum(ret_pred) > 0:
    position_size *= 1.2  # Increase in trend
```

---

## 7. Checklist de Validation

Avant de considérer le modèle en production :

- [ ] Régimes stables (50 < switching_rate < 200)
- [ ] Chaque expert bat le hasard dans son régime (dir_acc > 0.50)
- [ ] Direction globale proche du hasard (0.48 < global_acc < 0.52)
- [ ] Classification des régimes > 60%
- [ ] Pas de collapse (entropy > 1.0)
- [ ] Validation sur out-of-sample temporel (≥ 3 mois futurs)
- [ ] Backtest avec coûts de transaction réalistes

---

## Conclusion

Cette architecture résout le problème fondamental de **non-stationnarité** en :

1. **Décomposant** la variance en composantes intra-régime (apprenables) et inter-régime (gérées par le classifieur)
2. **Spécialisant** chaque expert dans un régime homogène
3. **Évitant** d'apprendre la direction globale (non-stationnaire)

Elle est **mathématiquement fondée**, **implémentable en production**, et **testable rigoureusement** via les critères de succès définis.
