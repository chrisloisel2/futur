# 🚀 MODÈLE SSL AMÉLIORÉ - Documentation Complète

## 🎯 Vue d'ensemble

Le **modèle SSL amélioré** (`model_ssl_enhanced.py`) implémente un système flexible de Self-Supervised Learning avec :

### ✅ 3 Encoders au choix
1. **Transformer Encoder** - Architecture standard Transformer
2. **TimesNet Encoder** - Convolutions 2D dans le domaine fréquentiel
3. **MultiModal Encoder** - Compatible avec votre modèle existant

### ✅ Projection Head obligatoire
- **MLP** : `d_model → hidden → projection_dim (128)`
- Pour contrastive learning

### ✅ 3 Objectifs SSL au choix

#### A. Masked Modeling (MAE)
```python
- Masquer 20-40% des timesteps
- Reconstruction par MSE
- Decoder Transformer
```

#### B. Contrastive Learning (TS2Vec-style)
```python
- Augmentation 1 → Encoder → Projection
- Augmentation 2 → Encoder → Projection
- InfoNCE Loss (NT-Xent)
```

#### C. Next Patch Prediction
```python
- Découpage en patches séquentiels
- Prédiction du prochain patch
- MSE Loss
```

---

## 🏗️ Architecture

### Structure unifiée

```python
SSLModel(
    input_dim=8,
    d_model=256,
    encoder_type="transformer",      # Choix : transformer/timesnet/multimodal
    ssl_objective="contrastive",     # Choix : masked/contrastive/next_patch
    projection_dim=128,              # Pour contrastive
    mask_ratio=0.3,                  # Pour masked (20-40%)
    patch_len=16,                    # Pour next_patch
)
```

### Flow général

```
Input: [batch, seq_len, input_dim]
    ↓
ENCODER (Transformer/TimesNet/MultiModal)
    ↓
Embeddings: [batch, seq_len, d_model]
    ↓
┌─────────────┬─────────────┬─────────────┐
│  MASKED     │ CONTRASTIVE │ NEXT PATCH  │
└─────────────┴─────────────┴─────────────┘
```

---

## 📦 Composants détaillés

### 1. Feature Encoders

#### A. TransformerEncoder

**Architecture** :
```python
Input Projection (Linear)
    ↓
Positional Encoding (Sinusoidal)
    ↓
Transformer Encoder Layers (n_layers)
    ↓
Layer Normalization
    ↓
Output: [batch, seq_len, d_model]
```

**Usage** :
```python
encoder = TransformerEncoder(
    input_dim=8,
    d_model=256,
    n_heads=8,
    n_layers=6,
    dropout=0.1,
)

embeddings = encoder(x)  # [batch, seq_len, 256]
```

**Avantages** :
- ✅ Standard, bien compris
- ✅ Capture dépendances long-terme
- ✅ Parallélisable
- ✅ Compatible MPS/CUDA

---

#### B. TimesNetEncoder

**Architecture** :
```python
Input Projection (Linear)
    ↓
TimesNet Blocks (n_layers):
    FFT → 2D Conv (frequency domain) → IFFT
    ↓
Layer Normalization
    ↓
Output: [batch, seq_len, d_model]
```

**Usage** :
```python
encoder = TimesNetEncoder(
    input_dim=8,
    d_model=256,
    n_layers=4,
    kernel_size=3,
    dropout=0.1,
)

embeddings = encoder(x)  # [batch, seq_len, 256]
```

**Avantages** :
- ✅ Capture patterns multi-échelle
- ✅ Convolutions dans domaine fréquentiel
- ✅ Efficace pour séries temporelles périodiques
- ✅ État de l'art pour forecasting

**Spécificité** :
- Utilise FFT/IFFT pour transformer en 2D
- Convolutions 2D dans l'espace fréquentiel
- Particulièrement bon pour crypto (cycles)

---

#### C. MultiModalEncoder

**Architecture** :
```python
Input Projection (Linear)
    ↓
Positional Encoding
    ↓
Transformer Encoder
    ↓
Layer Normalization
    ↓
Output: [batch, seq_len, d_model]
```

**Usage** :
```python
encoder = MultiModalEncoder(
    input_dim=8,
    d_model=256,
    n_heads=8,
    n_layers=4,
    dropout=0.1,
)

embeddings = encoder(x)  # [batch, seq_len, 256]
```

**Avantages** :
- ✅ Compatible avec votre modèle TRAIN existant
- ✅ Architecture connue et testée
- ✅ Facile à intégrer

---

### 2. Projection Head

**Architecture** :
```python
MLP(
    Linear(d_model → hidden_dim),
    ReLU,
    Linear(hidden_dim → projection_dim)
)
```

**Usage** :
```python
proj_head = ProjectionHead(
    d_model=256,
    projection_dim=128,
)

# 2D input (pooled embeddings)
z = torch.randn(batch, 256)
proj = proj_head(z)  # [batch, 128]

# 3D input (sequence embeddings)
z = torch.randn(batch, seq_len, 256)
proj = proj_head(z)  # [batch, seq_len, 128]
```

**Rôle** :
- Projette embeddings vers espace de dimension réduite
- Utilisé pour contrastive learning
- Sépare représentation (encoder) de comparaison (projection)

---

## 🎯 SSL Objectives

### A. Masked Modeling (MAE)

**Principe** :
```
1. Masquer 20-40% des timesteps aléatoirement
2. Remplacer par mask token appris
3. Encoder avec mask
4. Decoder pour reconstruire
5. Loss MSE sur tokens masqués uniquement
```

**Implémentation** :
```python
model = SSLModel(
    input_dim=8,
    d_model=256,
    encoder_type="transformer",
    ssl_objective="masked",
    mask_ratio=0.3,  # 30% masking
)

outputs = model(x)
# {
#   'reconstructed': [batch, seq_len, input_dim],
#   'mask': [batch, seq_len],
#   'loss': scalar
# }
```

**Loss** :
```python
# Reconstruction loss (MSE sur masked tokens uniquement)
masked_tokens = ~mask
loss = MSE(reconstructed[masked_tokens], x[masked_tokens])
```

**Hyperparamètres** :
- `mask_ratio`: 0.2-0.4 (recommandé: 0.3)
- Plus le ratio est élevé, plus c'est difficile

**Avantages** :
- ✅ Apprend à prédire timesteps manquants
- ✅ Bon pour long terme
- ✅ Simple à implémenter

---

### B. Contrastive Learning

**Principe** :
```
1. Créer deux augmentations du même sample
2. Encoder les deux vues
3. Projeter avec MLP
4. Rapprocher projections (même sample)
5. Éloigner projections (samples différents)
```

**Implémentation** :
```python
model = SSLModel(
    input_dim=8,
    d_model=256,
    encoder_type="transformer",
    ssl_objective="contrastive",
    projection_dim=128,
)

# Deux vues augmentées
x_aug1 = augment(x)  # Jitter, scaling
x_aug2 = augment(x)  # Time warp, permutation

outputs = model(x_aug1, x_aug=x_aug2)
# {
#   'z1': [batch, seq_len, d_model],
#   'z2': [batch, seq_len, d_model],
#   'proj1': [batch, projection_dim],
#   'proj2': [batch, projection_dim],
# }
```

**Loss** :
```python
# NT-Xent (Normalized Temperature-scaled Cross Entropy)
from SELF_SUPERVISED.contrastive import NTXentLoss

criterion = NTXentLoss(temperature=0.5)
loss = criterion(proj1, proj2)
```

**Hyperparamètres** :
- `temperature`: 0.1-0.5 (recommandé: 0.2-0.5)
- Plus la température est basse, plus c'est strict
- `projection_dim`: 128 (standard)

**Avantages** :
- ✅ État de l'art en SSL
- ✅ Apprend représentations discriminatives
- ✅ Robuste aux augmentations

---

### C. Next Patch Prediction

**Principe** :
```
1. Découper série temporelle en patches
2. Utiliser tous les patches sauf le dernier comme contexte
3. Prédire le dernier patch
4. Loss MSE entre prédiction et vérité
```

**Implémentation** :
```python
model = SSLModel(
    input_dim=8,
    d_model=256,
    encoder_type="transformer",
    ssl_objective="next_patch",
    patch_len=16,  # Longueur de chaque patch
)

outputs = model(x)
# {
#   'predictions': [batch, patch_len, input_dim],
#   'targets': [batch, patch_len, input_dim],
#   'loss': scalar
# }
```

**Loss** :
```python
# MSE entre patch prédit et patch réel
loss = MSE(predictions, targets)
```

**Hyperparamètres** :
- `patch_len`: 8-32 (recommandé: 16)
- Plus le patch est long, plus c'est difficile

**Avantages** :
- ✅ Apprend à prédire le futur
- ✅ Utile pour forecasting
- ✅ Autorégressif naturel

---

## 🚀 Utilisation

### Configuration

Fichier `config_ssl_enhanced.yaml` :

```yaml
model:
  input_dim: 8
  d_model: 256
  encoder_type: "transformer"      # Choix d'encoder
  ssl_objective: "contrastive"     # Choix d'objectif
  n_heads: 8
  n_layers: 4
  projection_dim: 128
  mask_ratio: 0.3
  patch_len: 16

training:
  batch_size: 64
  epochs: 100
  lr: 0.001
```

---

### Example 1 : Masked Modeling avec Transformer

```bash
cd ai
python SELF_SUPERVISED/example_enhanced_usage.py \
    --objective masked \
    --encoder transformer \
    --epochs 100
```

**Résultat** : `checkpoints/ssl_masked_transformer.pt`

---

### Example 2 : Contrastive Learning avec TimesNet

```bash
python SELF_SUPERVISED/example_enhanced_usage.py \
    --objective contrastive \
    --encoder timesnet \
    --epochs 100
```

**Résultat** : `checkpoints/ssl_contrastive_timesnet.pt`

---

### Example 3 : Next Patch avec MultiModal

```bash
python SELF_SUPERVISED/example_enhanced_usage.py \
    --objective next_patch \
    --encoder multimodal \
    --epochs 100
```

**Résultat** : `checkpoints/ssl_next_patch_multimodal.pt`

---

### Example 4 : Code Python direct

```python
from SELF_SUPERVISED.model_ssl_enhanced import create_ssl_model
import torch

# Configuration
config = {
    'input_dim': 8,
    'd_model': 256,
    'n_heads': 8,
    'n_layers': 4,
    'projection_dim': 128,
    'mask_ratio': 0.3,
}

# Créer modèle
model = create_ssl_model(
    config=config,
    encoder_type="transformer",
    ssl_objective="contrastive",
)

# Données
x = torch.randn(32, 100, 8)
x_aug = torch.randn(32, 100, 8)

# Forward
outputs = model(x, x_aug=x_aug)

# Extraire projections
proj1 = outputs['proj1']  # [32, 128]
proj2 = outputs['proj2']  # [32, 128]

# Loss contrastive
from SELF_SUPERVISED.contrastive import NTXentLoss
criterion = NTXentLoss(temperature=0.5)
loss = criterion(proj1, proj2)

# Backward
loss.backward()
```

---

## 🎯 Choix de configuration

### Quel encoder choisir ?

| Encoder | Avantages | Inconvénients | Recommandation |
|---------|-----------|---------------|----------------|
| **Transformer** | Standard, robuste | Coûteux en mémoire | ⭐ Défaut |
| **TimesNet** | Multi-échelle, FFT | Complexe | Crypto avec cycles |
| **MultiModal** | Compatible TRAIN | Moins spécialisé | Intégration facile |

---

### Quel objectif choisir ?

| Objectif | Avantages | Inconvénients | Recommandation |
|----------|-----------|---------------|----------------|
| **Masked** | Simple, prédit futur | Moins discriminatif | Forecasting |
| **Contrastive** | État de l'art | Besoin augmentations | ⭐ Trading |
| **Next Patch** | Autorégressif | Limité au court terme | Prédiction immédiate |

---

### Hyperparamètres recommandés

**Pour trading crypto** :

```yaml
# Configuration optimale pour crypto
model:
  encoder_type: "transformer"      # ou "timesnet" si cycles importants
  ssl_objective: "contrastive"     # meilleur pour discriminer
  d_model: 256
  n_heads: 8
  n_layers: 6                      # Plus profond = plus de capacité
  projection_dim: 128
  dropout: 0.1

training:
  batch_size: 128                  # Plus grand pour contrastive
  lr: 0.001
  epochs: 200                      # Convergence lente
```

---

## 📊 Tests et validation

### Tester l'installation

```bash
cd ai
python SELF_SUPERVISED/test_enhanced_model.py
```

**Output attendu** :
```
✅ TransformerEncoder
✅ TimesNetEncoder
✅ MultiModalEncoder
✅ ProjectionHead
✅ Masked Modeling
✅ Contrastive Learning
✅ Next Patch Prediction
```

---

## 🔧 Intégration avec TRAIN

### Charger encoder pré-entraîné

```python
from SELF_SUPERVISED.model_ssl_enhanced import SSLModel
import torch
import torch.nn as nn

# Charger modèle pré-entraîné
ssl_model = SSLModel(
    input_dim=8,
    d_model=256,
    encoder_type="transformer",
    ssl_objective="contrastive",
)

checkpoint = torch.load("checkpoints/ssl_contrastive_transformer.pt")
ssl_model.load_state_dict(checkpoint)

# Extraire encoder
encoder = ssl_model.encoder

# Geler encoder (optionnel)
for param in encoder.parameters():
    param.requires_grad = False

# Créer modèle downstream
class TradingModel(nn.Module):
    def __init__(self, encoder):
        super().__init__()
        self.encoder = encoder
        self.head = nn.Sequential(
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Linear(128, 3),  # BUY/SELL/HOLD
        )

    def forward(self, x):
        # Encode
        z = self.encoder(x)  # [batch, seq_len, 256]
        # Pool
        z_pooled = z.mean(dim=1)  # [batch, 256]
        # Predict
        return self.head(z_pooled)

model = TradingModel(encoder)
```

---

## 📚 Références

### Papers

1. **Masked Autoencoders (MAE)**
   - *Masked Autoencoders Are Scalable Vision Learners*
   - He et al., CVPR 2022

2. **Contrastive Learning (SimCLR)**
   - *A Simple Framework for Contrastive Learning*
   - Chen et al., ICML 2020

3. **TimesNet**
   - *TimesNet: Temporal 2D-Variation Modeling*
   - Wu et al., ICLR 2023

---

## ✅ Résumé

**Modèle SSL Enhanced** :
- ✅ 3 encoders (Transformer, TimesNet, MultiModal)
- ✅ Projection head (MLP → 128)
- ✅ 3 objectifs SSL (Masked, Contrastive, Next Patch)
- ✅ Tests unitaires validés
- ✅ Configuration flexible (YAML)
- ✅ Intégration TRAIN facile

**Prêt pour l'entraînement ! 🚀**
