# Guide d'optimisation mémoire

## Problème rencontré

L'entraînement a été tué par le système ("`Killed: 9`") lors du chargement des données. Cela indique un problème de mémoire (RAM).

## Analyse

Votre configuration QUICK chargeait :
- **5 symboles** (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT)
- **2 années** (2023-2024)
- **~5.2 millions de lignes** de données brutes
- **52 features** générées par ligne
- **Séquences de 100 timesteps**

Cela représente une charge mémoire énorme :
- Données brutes : ~400 MB
- Après feature engineering : ~2 GB
- Séquences + batches : ~4-6 GB
- **Total : 6-8 GB de RAM nécessaire**

## Solutions

### Solution 1 : Configuration LIGHT (recommandée)

Utilisez la nouvelle configuration optimisée :

```bash
cd ai
PYTHONPATH=TRAIN:$PYTHONPATH python3 train.py \
    --config configs/train_s3_light.yaml \
    --device mps
```

Cette config charge :
- **3 symboles** au lieu de 5
- **1 année** (2024) au lieu de 2
- **Batch size 16** au lieu de 32
- **Lookback 50** au lieu de 100
- **Modèle plus petit** (256 vs 512 dimensions)

**Mémoire requise : ~2-3 GB** ✅

### Solution 2 : Ajuster la config existante

Éditez `configs/train_s3.yaml` et réduisez :

```yaml
data:
  start_year: 2024        # Une seule année
  end_year: 2024
  symbols_filter:
    - "BTCUSDT"
    - "ETHUSDT"           # Seulement 2 symboles
  lookback_window: 50     # Réduit
  batch_size: 16          # Réduit
```

### Solution 3 : Mode DEBUG

Pour tester rapidement :

```bash
cd ai
PYTHONPATH=TRAIN:$PYTHONPATH python3 train.py \
    --config configs/train_s3_light.yaml \
    --device mps \
    --debug_mode
```

Le mode debug limite automatiquement :
- 1 epoch seulement
- Moins de batches traités

## Tableau de dimensionnement

| Configuration | Symboles | Années | Données | RAM requise | Recommandé pour |
|--------------|----------|--------|---------|-------------|----------------|
| **LIGHT** | 3 | 1 | 1.6M | 2-3 GB | MacBook 8GB RAM |
| **MEDIUM** | 3 | 2 | 3.2M | 4-5 GB | MacBook 16GB RAM |
| **STANDARD** | 5 | 2 | 5.3M | 6-8 GB | MacBook 16GB+ RAM |
| **FULL** | 8 | 5 | 21M | 15-20 GB | Workstation 32GB+ |

## Paramètres à ajuster selon la RAM

### Si vous avez 8 GB de RAM :
```yaml
symbols_filter: ["BTCUSDT", "ETHUSDT"]  # 2 symboles max
start_year: 2024
end_year: 2024                           # 1 an max
lookback_window: 50
batch_size: 16
d_model: 256
```

### Si vous avez 16 GB de RAM :
```yaml
symbols_filter: ["BTCUSDT", "ETHUSDT", "BNBUSDT"]  # 3 symboles
start_year: 2023
end_year: 2024                           # 2 ans
lookback_window: 100
batch_size: 32
d_model: 512
```

### Si vous avez 32 GB+ de RAM :
```yaml
symbols_filter: []                       # Tous les symboles
start_year: 2020
end_year: 2024                           # 5 ans
lookback_window: 200
batch_size: 64
d_model: 512
```

## Vérifier l'utilisation mémoire

Pendant l'entraînement, surveillez la RAM :

```bash
# Dans un autre terminal
watch -n 2 'ps aux | grep python | grep train.py'
```

Ou utilisez Activity Monitor (macOS) pour voir la consommation mémoire en temps réel.

## Optimisations supplémentaires

### 1. Gradient Accumulation

Permet d'utiliser un petit batch size sans perdre en performance :

```yaml
training:
  batch_size: 8
  gradient_accumulation_steps: 16  # Simule batch_size=128
```

### 2. Mixed Precision (si supporté par MPS)

```yaml
training:
  use_amp: true  # Automatic Mixed Precision
```

### 3. Checkpointing du gradient

Pour les très grands modèles, active le gradient checkpointing dans le code du modèle.

## Recommandations par ordre de priorité

1. **Commencez par LIGHT** : Testez d'abord avec la config light
2. **Vérifiez les ressources** : Surveillez RAM/CPU pendant l'entraînement
3. **Augmentez progressivement** : Si ça marche, ajoutez des symboles/années
4. **Optimisez avant d'agrandir** : Utilisez gradient accumulation

## Exemples de commandes

### Test rapide (< 1 GB RAM)
```bash
cd ai
PYTHONPATH=TRAIN:$PYTHONPATH python3 train.py \
    --config configs/train_s3_light.yaml \
    --device mps \
    --debug_mode
```

### Entraînement léger (2-3 GB RAM)
```bash
cd ai
PYTHONPATH=TRAIN:$PYTHONPATH python3 train.py \
    --config configs/train_s3_light.yaml \
    --device mps
```

### Si erreur mémoire persiste

Créez une config ultra-light :

```yaml
data:
  symbols_filter: ["BTCUSDT"]  # UN SEUL symbole
  start_year: 2024
  end_year: 2024
  lookback_window: 30
  batch_size: 8

model:
  params:
    d_model: 128
    n_layers: 2
```

## Monitoring

Pour voir l'utilisation mémoire en temps réel pendant l'entraînement :

```python
# Ajoutez dans train.py si nécessaire
import psutil
import os

process = psutil.Process(os.getpid())
print(f"Memory usage: {process.memory_info().rss / 1024 / 1024:.2f} MB")
```

## Résumé

**Pour votre système, utilisez :**

```bash
cd ai
PYTHONPATH=TRAIN:$PYTHONPATH python3 train.py \
    --config configs/train_s3_light.yaml \
    --device mps
```

Cette configuration devrait fonctionner sans problème de mémoire. ✅
