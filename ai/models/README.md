# Regime-Aware Market Model - Unified Architecture

**Architecture à détection de régimes avec experts spécialisés pour séries temporelles financières.**

---

## 📁 Structure (3 fichiers principaux)

```
ai/models/
├── config.py          # Configuration unique (hyperparamètres, S3, features)
├── regime_model.py    # Architecture complète (régimes, modèle, évaluation)
├── train.py           # Pipeline d'entraînement unifié
│
├── model.py           # Infrastructure existante (scaler, S3 loader)
└── README.md          # Ce fichier
```

---

## 🚀 Quickstart

### 1. Configuration

Éditer `config.py` ou définir les variables d'environnement :

```bash
export S3_BUCKET="your-bucket"
export S3_PREFIX="btc/1m/"
export AWS_PROFILE="default"  # optionnel
```

### 2. Entraînement

```bash
python train.py
```

**Outputs :**
- `output/model.keras` - Modèle complet
- `output/best_weights.h5` - Meilleurs poids (early stopping)
- `output/results.json` - Métriques d'évaluation
- `output/scaler.pkl` - Scaler (pour inférence)

### 3. Inférence

```python
import pickle
import numpy as np
import tensorflow as tf

# Charger modèle et scaler
model = tf.keras.models.load_model("output/model.keras")
with open("output/scaler.pkl", "rb") as f:
    scaler = pickle.load(f)

# Préparer features (derniers 256 timesteps)
x = features_history[-256:]  # [256, 44]
x = scaler.transform(x)
x = np.expand_dims(x, axis=0)  # [1, 256, 44]

# Prédire
outputs = model(x, training=False, return_regime_probs=True)

regime_probs = outputs["regime_probs"].numpy()[0]  # [5]
ret_pred = outputs["ret"].numpy()[0]  # [12]
rv_pred = outputs["rv"].numpy()[0]  # scalar

# Interpréter
from config import REGIME_NAMES
current_regime = REGIME_NAMES[np.argmax(regime_probs)]
print(f"Régime: {current_regime} ({regime_probs.max():.2%})")
print(f"Return prédit (12 steps): {ret_pred.sum():.4f}")
print(f"Volatilité: {rv_pred:.4f}")
```

---

## ⚙️ Configuration

Tous les hyperparamètres dans **`config.py`** :

### Data
- `lookback: int = 256` - Fenêtre d'entrée
- `horizon: int = 12` - Steps futurs à prédire
- `batch_size: int = 256`

### Régimes
- `n_regimes: int = 5` - {TREND, MEAN_REVERT, HIGH_VOL, LOW_VOL, RANGE}
- `regime_d_model: int = 64` - Dimension classifier
- `regime_n_layers: int = 3`

### Experts
- `expert_d_model: int = 64` - Dimension expert (~1/3 du modèle global)
- `expert_n_layers: int = 2`
- `expert_type: str = "tcn"` - TCN ou Transformer

### Gating
- `gating_mode: str = "soft"` - "hard" (argmax) ou "soft" (MoE)
- `entropy_weight: float = 0.01` - Régularisation anti-collapse

### Training
- `lr: float = 3e-4`
- `epochs: int = 20`
- `w_regime: float = 0.3` - Poids loss régime
- `w_ret: float = 1.0` - Poids loss return
- `w_rv: float = 0.4` - Poids loss volatilité

### Évaluation
- `eval_direction_threshold: float = 0.25` - Seuil neutralité (× std)
- `eval_significance_level: float = 0.05` - p-value

---

## 🎯 Architecture

### Pipeline Complet

```
Input [B, L=256, F=44]
   ↓
RegimeClassifier (CNN, 3 layers)
   ↓
p_regime [B, 5]
   ↓
5 Experts (TCN, 2 layers each)
   ↓
Gating (soft MoE)
   ↓
Output: {ret: [B, 12], rv: [B]}
```

### Définition des Régimes

Calculés automatiquement (pas de labels manuels) :

| Régime | Critère | Indicateurs |
|--------|---------|-------------|
| **TREND** | Pente EMA forte + stabilité directionnelle | dist_ema_20, direction_changes |
| **MEAN_REVERT** | RSI extrême + anticorrélation | RSI, sign(dist) ≠ sign(ret) |
| **HIGH_VOL** | RV > Q₇₅ | rv_ann_60 |
| **LOW_VOL** | RV < Q₂₅ | rv_ann_60 |
| **RANGE** | Faible pente + faible écart EMA | dist_ema_20 |

---

## 📊 Évaluation

### Métriques Calculées

**Per-Horizon :**
- MAE (%) - Dénormalisé en pourcentage
- Corrélation (Pearson)
- R² (baseline sur train)

**Direction :**
- Accuracy avec seuil de neutralité (0.25σ)
- p-value (test binomial)
- Exclusion zones neutres

**Baselines :**
- Persistence : `ret_t+h = ret_t`
- Mean forecast : `ret_t+h = mean(ret_train)`

### Seuils de Performance

| Métrique | Minimum | Bon | Excellent |
|----------|---------|-----|-----------|
| MAE @ H=1 | <1.0% | <0.5% | <0.3% |
| Corr @ H=1 | >0.05 | >0.10 | >0.20 |
| Dir Acc | >50% (p<0.05) | >52% (p<0.01) | >55% (p<0.001) |
| Regime Acc | >60% | >70% | >80% |

---

## 🔧 Modifications Avancées

### Ajouter un 6ème Régime (ex: CRISIS)

**1. Modifier `config.py` :**
```python
n_regimes: int = 6
```

**2. Modifier `regime_model.py` (compute_regime_labels) :**
```python
# Ajouter score
score_crisis = (rv_current > np.percentile(window_rv, 99)) * \
               (np.abs(ret_current) > np.percentile(np.abs(window_ret), 99))

scores = np.zeros(6)
scores[5] = score_crisis  # CRISIS

# Modifier REGIME_NAMES
REGIME_NAMES = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE", "CRISIS"]
```

### Changer Expert Type (TCN → Transformer)

**Modifier `config.py` :**
```python
expert_type: str = "transformer"
```

### Désactiver Gating Soft (MoE → Hard)

**Modifier `config.py` :**
```python
gating_mode: str = "hard"
```

---

## 📚 Justification Mathématique

### Problème Résolu

Marchés financiers = **distributions conditionnelles non-stationnaires** :

```
P(r_t+1 | trend) ≈ N(0.02, 0.01²)     → Distribution positive
P(r_t+1 | mean_revert) ≈ N(0.00, 0.01²) → Distribution centrée
```

Modèle global apprend moyenne → variance élevée.

### Solution : Décomposition de la Variance

```
Var[ŷ] = E_τ[Var[ŷ | τ]] + Var_τ[E[ŷ | τ]]
         └─ intra-régime  └─ inter-régime
```

**Régimes spécialisés** : Minimisent `E_τ[Var[ŷ | τ]]` (variance intra-régime).

**Mixture of Experts** : Budget identique, mais k fonctions spécialisées au lieu de 1 moyenne.

---

## 🛠️ Troubleshooting

### Erreur : `FileNotFoundError: scaler.pkl`

**Cause :** Scaler non sauvegardé après fit.

**Solution :**
```python
# Dans config.py
save_scaler: bool = True
```

### Erreur : `regime_acc` stagne à 20%

**Cause :** Classifier ne converge pas.

**Solutions :**
1. Augmenter `regime_d_model` (64 → 128)
2. Augmenter `pretrain_regime_epochs` (5 → 10)
3. Réduire `regime_dropout` (0.15 → 0.10)

### Erreur : Direction accuracy ≈ 50%

**Cause :** Labels bruités ou seuil inadapté.

**Solutions :**
1. Augmenter `eval_direction_threshold` (0.25 → 0.50)
2. Vérifier distribution des régimes (stats)
3. Augmenter lookback (256 → 512)

---

## ✅ Checklist de Production

Avant déploiement :

- [ ] Entraînement sur ≥ 100k samples
- [ ] Validation out-of-sample (≥ 3 mois)
- [ ] Tous régimes ont dir_acc > 50%
- [ ] Regime acc > 60%
- [ ] Switching rate : 50-200/1000
- [ ] Baselines battues (MAE < persistence)
- [ ] Backtest avec coûts de transaction
- [ ] Scaler sauvegardé (`output/scaler.pkl`)

---

## 📖 Références

### Code
- **`config.py`** : Configuration unique
- **`regime_model.py`** : Architecture + Évaluation
- **`train.py`** : Pipeline d'entraînement

### Papers
- Shazeer et al. (2017) - "Outrageously Large Neural Networks"
- Bai et al. (2018) - "Temporal Convolutional Networks"
- Tsay (2010) - "Analysis of Financial Time Series"

---

**Version :** 2.0.0 (Unified)
**Date :** 2025-12-20
