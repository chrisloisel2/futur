# 🧠 SELF_SUPERVISED - Self-Supervised Learning for Time Series

## Vue d'ensemble

Module de **Self-Supervised Learning (SSL)** pour l'apprentissage de représentations sur séries temporelles crypto **sans labels**.

Permet d'apprendre des features de haute qualité sur vos données non-labelisées, puis de les réutiliser pour des tâches supervisées.

## 🎯 Pourquoi le Self-Supervised Learning ?

### Problème
- Vous avez des **millions de données** de prix crypto (MongoDB)
- Mais **peu de labels** pour l'entraînement supervisé
- Les labels sont coûteux à obtenir

### Solution
- **Pré-entraînement SSL** : Apprendre des représentations sur données non-labelisées
- **Fine-tuning** : Affiner sur votre tâche avec peu de labels
- **Transfer learning** : Réutiliser les représentations apprises

### Avantages
✅ Utilise **toutes vos données** (pas besoin de labels)
✅ Apprend des **features génériques** de haute qualité
✅ **Moins de données** nécessaires pour le fine-tuning
✅ **Meilleures performances** sur tâches downstream
✅ **Robustesse** aux changements de distribution

---

## 🏗️ Architecture du module

```
SELF_SUPERVISED/
├── model_ssl.py              # Modèles SSL (TS2Vec, MAE, SimCLR)
├── pretrain.py               # Scripts de pré-entraînement
├── dataloader_ssl.py         # DataLoader pour SSL
├── masking_strategies.py     # Stratégies de masking
├── contrastive.py            # Losses contrastives et augmentations
├── mae.py                    # Components MAE (encoder/decoder)
├── config_ssl.yaml           # Configuration
├── example_usage.py          # Exemples d'utilisation
└── README.md                 # Ce fichier
```

---

## 🤖 Modèles implémentés

### 1. **TS2Vec** (Recommandé) ⭐

**Principe** : Contrastive learning avec contraste hiérarchique temporel et instance-wise.

**Architecture** :
- Dilated Convolutional Encoder
- Multi-scale temporal contrasting
- Contrastive loss (InfoNCE)

**Avantages** :
- ✅ Spécialisé pour séries temporelles
- ✅ Capture multi-échelle (court/long terme)
- ✅ État de l'art sur benchmarks
- ✅ Rapide à entraîner

**Utilisation** :
```python
from SELF_SUPERVISED import pretrain_ts2vec, TS2VecModel

# Pré-entraînement
model = pretrain_ts2vec(config, train_loader, val_loader)

# Encodage
embeddings = model.encode(x, return_all=False)  # [batch, 320]
```

**Référence** : *TS2Vec: Towards Universal Representation of Time Series* (AAAI 2022)

---

### 2. **MAE (Masked Autoencoder)**

**Principe** : Masquer des portions de la série temporelle et les reconstruire.

**Architecture** :
- Transformer Encoder (sur patches visibles)
- Transformer Decoder (reconstruction)
- MSE loss sur patches masqués

**Avantages** :
- ✅ Simple et efficace
- ✅ Apprend à prédire le futur
- ✅ Bon pour séries temporelles longues
- ✅ Stratégies de masking flexibles

**Utilisation** :
```python
from SELF_SUPERVISED import pretrain_mae, MAEModel

# Pré-entraînement
model = pretrain_mae(config, train_loader, val_loader)

# Encodage
embeddings = model.encode(x)  # [batch, seq_len, d_model]
```

**Référence** : Inspiré de *Masked Autoencoders Are Scalable Vision Learners* (CVPR 2022)

---

### 3. **SimCLR**

**Principe** : Contrastive learning avec augmentations et NT-Xent loss.

**Architecture** :
- Dilated Conv Encoder
- MLP Projection Head
- NT-Xent loss

**Avantages** :
- ✅ Framework simple et général
- ✅ Facile à implémenter
- ✅ Bon baseline

**Utilisation** :
```python
from SELF_SUPERVISED import pretrain_simclr, SimCLRModel

# Pré-entraînement
model = pretrain_simclr(config, train_loader, val_loader)

# Encodage
embeddings = model.encode(x)  # [batch, hidden_dim]
```

**Référence** : *A Simple Framework for Contrastive Learning of Visual Representations* (ICML 2020)

---

## 🔧 Composants techniques

### Masking Strategies

4 stratégies de masking implémentées :

#### 1. **Random Masking**
```python
from SELF_SUPERVISED import RandomMasking

masking = RandomMasking(mask_ratio=0.75)
mask = masking(batch_size=32, seq_len=100, device='cpu')
```
- Masque aléatoirement 75% des tokens
- Simple et efficace

#### 2. **Block Masking**
```python
from SELF_SUPERVISED import BlockMasking

masking = BlockMasking(mask_ratio=0.75, block_length=10)
mask = masking(batch_size=32, seq_len=100)
```
- Masque des blocs contigus de longueur fixe
- Plus difficile (prédire séquences longues)

#### 3. **Geometric Masking**
```python
from SELF_SUPERVISED import GeometricMasking

masking = GeometricMasking(mask_ratio=0.75, mean_block_length=10)
mask = masking(batch_size=32, seq_len=100)
```
- Longueurs de blocs variables (distribution géométrique)
- Plus réaliste

#### 4. **Structured Masking**
```python
from SELF_SUPERVISED import StructuredMasking

masking = StructuredMasking(period=24, mask_ratio=0.5)
mask = masking(batch_size=32, seq_len=100)
```
- Masque des périodes entières (ex: jours entiers)
- Apprend les patterns saisonniers

---

### Augmentations temporelles

6 augmentations pour contrastive learning :

```python
from SELF_SUPERVISED import (
    jitter,           # Bruit Gaussien
    scaling,          # Facteur d'échelle
    rotation,         # Flip vertical
    permutation,      # Permutation de segments
    time_warp,        # Warping temporel
    window_slice,     # Extraction de fenêtre
)

# Pipeline complet
from SELF_SUPERVISED import create_augmentations

augment = create_augmentations(
    augmentation_list=['jitter', 'scaling', 'time_warp'],
    jitter_sigma=0.03,
    scaling_sigma=0.1,
    warp_sigma=0.2,
)

x_augmented = augment(x)
```

---

### Contrastive Losses

3 losses contrastives :

#### 1. **TS2Vec Loss**
```python
from SELF_SUPERVISED import TS2VecLoss

criterion = TS2VecLoss(temperature=0.2, temporal_unit=0)
loss = criterion(z1, z2)
```
- Contraste hiérarchique temporel
- Instance-wise contrasting

#### 2. **NT-Xent Loss**
```python
from SELF_SUPERVISED import NTXentLoss

criterion = NTXentLoss(temperature=0.5)
loss = criterion(z1, z2)
```
- Loss standard de SimCLR
- Normalized Temperature-scaled Cross Entropy

#### 3. **Supervised Contrastive Loss**
```python
from SELF_SUPERVISED import SupConLoss

criterion = SupConLoss(temperature=0.5)
loss = criterion(features, labels)
```
- Utilise les labels si disponibles
- Maximise similarité intra-classe

---

## 🚀 Utilisation

### Étape 1 : Configuration

Éditez [config_ssl.yaml](config_ssl.yaml) :

```yaml
data:
  source: mongodb
  mongo_uri: "mongodb+srv://..."
  db_name: "trader2"
  collection_name: "historical_ohlcv"
  symbols: ["BTC/USDT", "ETH/USDT"]
  sequence_length: 100

training:
  batch_size: 64
  epochs: 100
  lr: 0.001
  device: "auto"

ts2vec:
  hidden_dim: 64
  output_dim: 320
  depth: 10
  temperature: 0.2
```

---

### Étape 2 : Pré-entraînement

#### Option A : TS2Vec (Recommandé)

```bash
cd ai/SELF_SUPERVISED
python example_usage.py --mode ts2vec
```

#### Option B : MAE

```bash
python example_usage.py --mode mae
```

#### Option C : SimCLR

```bash
python example_usage.py --mode simclr
```

---

### Étape 3 : Utilisation du modèle pré-entraîné

#### A. Extraction de features

```python
from SELF_SUPERVISED import TS2VecModel
import torch

# Charger modèle pré-entraîné
device = torch.device('mps')
model = TS2VecModel(input_dim=8, hidden_dim=64, output_dim=320, depth=10).to(device)

checkpoint = torch.load("./checkpoints/ts2vec/ts2vec_final.pt", map_location=device)
model.load_state_dict(checkpoint['model_state_dict'])
model.eval()

# Encoder nouvelles données
with torch.no_grad():
    embeddings = model.encode(x, return_all=False)  # [batch, 320]
```

#### B. Transfer learning

```python
# Geler l'encoder
for param in model.parameters():
    param.requires_grad = False

# Ajouter une tête de prédiction
class DownstreamModel(nn.Module):
    def __init__(self, encoder, output_dim=1):
        super().__init__()
        self.encoder = encoder
        self.head = nn.Sequential(
            nn.Linear(320, 128),
            nn.ReLU(),
            nn.Linear(128, output_dim),
        )

    def forward(self, x):
        embeddings = self.encoder.encode(x, return_all=False)
        return self.head(embeddings)

downstream_model = DownstreamModel(model)
```

#### C. Fine-tuning complet

```python
# Dégeler l'encoder (fine-tuning)
for param in model.parameters():
    param.requires_grad = True

# Entraîner avec learning rate plus faible
optimizer = AdamW([
    {'params': model.encoder.parameters(), 'lr': 1e-5},
    {'params': downstream_model.head.parameters(), 'lr': 1e-3},
])
```

---

## 📊 Pipeline complet

### 1. Collecte de données (frontend_pipeline)
```bash
cd ../../frontend_pipeline
python mass_data_collector_v2.py
```

### 2. Pré-entraînement SSL (SELF_SUPERVISED)
```bash
cd ../ai/SELF_SUPERVISED
python example_usage.py --mode ts2vec
```

### 3. Fine-tuning supervisé (TRAIN)
```python
# Dans ai/TRAIN/train.py
from SELF_SUPERVISED import TS2VecModel

# Charger encoder pré-entraîné
encoder = TS2VecModel(...)
checkpoint = torch.load("../SELF_SUPERVISED/checkpoints/ts2vec/ts2vec_final.pt")
encoder.load_state_dict(checkpoint['model_state_dict'])

# Intégrer dans votre modèle de trading
class TradingModelWithSSL(nn.Module):
    def __init__(self, encoder):
        super().__init__()
        self.encoder = encoder
        self.trading_head = nn.Linear(320, 3)  # BUY/SELL/HOLD

    def forward(self, x):
        embeddings = self.encoder.encode(x, return_all=False)
        return self.trading_head(embeddings)
```

---

## 🎓 Concepts clés

### 1. **Contrastive Learning**

**Principe** : Rapprocher les représentations de vues augmentées du même échantillon, éloigner celles d'échantillons différents.

```
Même série temporelle → 2 augmentations → Embeddings similaires
Séries différentes → Embeddings dissimilaires
```

**Loss** : InfoNCE (Normalized Cross Entropy)

### 2. **Masked Modeling**

**Principe** : Masquer des parties de l'entrée et apprendre à les reconstruire.

```
Série complète → Masquer 75% → Encoder parties visibles → Reconstruire masquées
```

**Loss** : MSE sur tokens masqués

### 3. **Hierarchical Contrasting (TS2Vec)**

**Principe** : Contraster à plusieurs échelles temporelles.

```
Niveau 1 : Contraste timestep par timestep
Niveau 2 : Contraste sur windows de 2 timesteps
Niveau 3 : Contraste sur windows de 4 timesteps
...
```

---

## 📈 Métriques et évaluation

### Pendant le pré-entraînement

```python
# TS2Vec / SimCLR
Loss = Contrastive Loss (plus bas = mieux)

# MAE
Loss = MSE Reconstruction Loss (plus bas = mieux)
```

### Après le pré-entraînement

**Évaluation indirecte** : Performance sur tâche downstream

```python
# Linear probing
# 1. Fixer l'encoder
# 2. Entraîner un classifieur linéaire
# 3. Mesurer accuracy

# Fine-tuning
# 1. Dégeler l'encoder
# 2. Fine-tune sur tâche supervisée
# 3. Mesurer performance finale
```

---

## 🔬 Comparaison des méthodes

| Méthode | Avantages | Inconvénients | Recommandation |
|---------|-----------|---------------|----------------|
| **TS2Vec** | ⭐ Spécialisé TS<br>⭐ Multi-échelle<br>⭐ État de l'art | ⚠️ Complexe | **Recommandé pour crypto** |
| **MAE** | ✅ Simple<br>✅ Prédit le futur<br>✅ Flexible masking | ⚠️ Coûteux (Transformer) | Bon pour long terme |
| **SimCLR** | ✅ Simple<br>✅ Baseline solide | ⚠️ Moins performant que TS2Vec | Bon point de départ |

---

## 💡 Best Practices

### 1. Choix du modèle
- **Crypto trading** : TS2Vec (capture multi-échelle)
- **Prédiction long terme** : MAE
- **Baseline rapide** : SimCLR

### 2. Hyperparamètres
```yaml
# TS2Vec
temperature: 0.2      # Plus bas = contraste plus dur
output_dim: 320       # Plus grand = plus expressif
depth: 10             # Plus profond = plus de capacité

# MAE
mask_ratio: 0.75      # Standard (75% masqué)
masking: 'random'     # Commencer simple
```

### 3. Data augmentation
```python
# Légère pour crypto (volatilité importante)
augmentations = ['jitter', 'scaling']
jitter_sigma = 0.03   # 3% de bruit
scaling_sigma = 0.1   # 10% d'échelle
```

### 4. Training
- **Batch size** : 64-256 (plus grand = meilleur contrastive)
- **Epochs** : 100-200 (convergence lente)
- **Learning rate** : 1e-3 (Adam/AdamW)
- **Scheduler** : Cosine annealing

---

## 🚨 Troubleshooting

### Problème : Loss ne descend pas

**Solutions** :
```yaml
# 1. Augmenter batch size
batch_size: 128

# 2. Ajuster temperature
temperature: 0.1  # Plus strict

# 3. Augmenter learning rate
lr: 0.005

# 4. Vérifier normalisation des données
```

### Problème : Out of memory

**Solutions** :
```yaml
# 1. Réduire batch size
batch_size: 32

# 2. Réduire sequence length
sequence_length: 50

# 3. Réduire model size
hidden_dim: 32
depth: 6
```

### Problème : Embeddings non-discriminatifs

**Solutions** :
```python
# 1. Augmenter la difficulté des augmentations
jitter_sigma = 0.05
scaling_sigma = 0.2

# 2. Augmenter la température
temperature = 0.5

# 3. Pre-train plus longtemps
epochs = 200
```

---

## 📚 Références

### Papers

1. **TS2Vec**
   - *TS2Vec: Towards Universal Representation of Time Series*
   - AAAI 2022
   - [Paper](https://arxiv.org/abs/2106.10466)

2. **SimCLR**
   - *A Simple Framework for Contrastive Learning of Visual Representations*
   - ICML 2020
   - [Paper](https://arxiv.org/abs/2002.05709)

3. **MAE**
   - *Masked Autoencoders Are Scalable Vision Learners*
   - CVPR 2022
   - [Paper](https://arxiv.org/abs/2111.06377)

### Repositories

- [TS2Vec Official](https://github.com/yuezhihan/ts2vec)
- [SimCLR Official](https://github.com/google-research/simclr)
- [MAE Official](https://github.com/facebookresearch/mae)

---

## 🎯 Prochaines étapes

Après le pré-entraînement SSL :

1. ✅ **Linear probing** : Évaluer qualité des embeddings
2. ✅ **Transfer learning** : Utiliser encoder dans TRAIN
3. ✅ **Fine-tuning** : Affiner sur tâche de trading
4. ✅ **Backtesting** : Tester performance en trading

---

## 🤝 Support

**Questions** : Voir [EXPLICATION_STRUCTURE.md](../../EXPLICATION_STRUCTURE.md)

**Logs** : `./checkpoints/{model}/training.log`

**Checkpoints** : `./checkpoints/{model}/*.pt`

---

**Votre module SSL est prêt ! 🚀**

Vous pouvez maintenant pré-entraîner des modèles sur vos millions de données crypto sans labels, puis les fine-tuner pour vos stratégies de trading.
