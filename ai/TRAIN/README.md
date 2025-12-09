# 🧠 TRAIN - Model Training & Inference

## Vue d'ensemble

Le module **TRAIN** est responsable de l'entraînement, de l'évaluation et du déploiement des modèles de trading algorithmique. Il contient l'architecture des modèles, les stratégies de trading, et les outils d'entraînement.

## 🏗️ Architecture

```
TRAIN/
├── models/                      # Architectures de modèles
│   ├── multi_modal_trading.py  # Modèle multimodal principal
│   └── __init__.py
├── training/                    # Module d'entraînement
│   ├── trainer.py              # Classe Trainer
│   └── __init__.py
├── utils/                       # Utilitaires
│   ├── config.py               # Gestion de la configuration
│   ├── metrics.py              # Métriques d'évaluation
│   └── __init__.py
├── data/                        # Pipeline de données pour training
│   ├── pipeline.py             # DataPipeline et DataLoader
│   ├── alternative_sources.py  # Sources alternatives
│   └── __init__.py
├── config/                      # Fichiers de configuration
│   └── training_config.yaml
├── checkpoints/                 # Points de contrôle des modèles
├── train.py                     # Script d'entraînement principal
├── main.py                      # Point d'entrée alternatif
├── trading_strategy_example.py # Exemples de stratégies
├── alpha_signal_analyzer.py    # Analyse des signaux alpha
├── visualize_signals.py        # Visualisation des signaux
└── requirements.txt            # Dépendances spécifiques
```

## 🚀 Démarrage rapide

### 1. Installation

```bash
cd TRAIN
pip install -r requirements.txt
```

### 2. Configuration

Créez ou modifiez `config/training_config.yaml` :

```yaml
model:
  type: "multi_modal"
  params:
    d_model: 512
    n_heads: 8
    n_layers: 6
    dropout: 0.1

training:
  epochs: 100
  batch_size: 32
  learning_rate: 0.001
  device: "auto"  # auto, mps, cpu, cuda

data:
  sources: []
  sequence_length: 60
  train_split: 0.7
  val_split: 0.15
```

### 3. Entraînement

```bash
python train.py --config config/training_config.yaml --device auto
```

Options disponibles :
- `--config` : Chemin vers le fichier de configuration
- `--device` : Device (auto, mps, cpu, cuda)
- `--log_level` : Niveau de log (INFO, DEBUG, WARNING)
- `--debug_mode` : Mode debug (run léger)
- `--fast_dev_run` : Sanity check rapide (1 epoch)
- `--use_alternative_data` : Activer les sources alternatives

### 4. Évaluation

```python
from training.trainer import TradingTrainer

# Charger un checkpoint
trainer = TradingTrainer.load_checkpoint("checkpoints/model_20231215_1430.pt")

# Évaluer sur test set
test_metrics = trainer.evaluate(test_loader)
print(test_metrics)
```

## 📊 Modèles disponibles

### 1. Multi-Modal Trading Model

Modèle principal qui combine plusieurs modalités :
- Données OHLCV (séries temporelles)
- Indicateurs techniques
- Sentiment de marché
- Données on-chain
- Métriques macro-économiques

```python
from models.multi_modal_trading import MultiModalTradingModel

model = MultiModalTradingModel(config)
predictions = model(input_data)
```

### 2. Modèles de séries temporelles (depuis PIPELINE)

Importés depuis le package `pipeline` :
- **DLinear** : Linéaire avec décomposition
- **TimesNet** : Architecture temporelle avancée
- **Transformer** : Transformer non-stationnaire
- **FT-Transformer** : Feature Tokenizer Transformer
- **TabNet** : Tabular Network

## 🔧 Composants principaux

### Trainer

```python
from training.trainer import TradingTrainer

trainer = TradingTrainer(
    model=model,
    config=training_config,
    device="mps",
    metrics_callback=custom_metrics_fn
)

# Entraînement
trainer.fit(train_loader, val_loader)

# Évaluation
metrics = trainer.evaluate(test_loader)

# Sauvegarde
trainer.save_checkpoint("checkpoints/my_model.pt")
```

### Data Pipeline

```python
from data.pipeline import DataPipeline

pipeline = DataPipeline(data_config)
train_loader, val_loader, test_loader = pipeline.get_data_loaders()
```

### Métriques

```python
from utils.metrics import ModelMetrics

metrics = ModelMetrics.track_all(predictions, targets)
# Retourne : accuracy, precision, recall, f1, sharpe_ratio, max_drawdown, etc.
```

## 📈 Stratégies de trading

### Exemple de stratégie simple

```python
from trading_strategy_example import SimpleMovingAverageCrossover

strategy = SimpleMovingAverageCrossover(
    short_window=20,
    long_window=50
)

signals = strategy.generate_signals(price_data)
backtest_results = strategy.backtest(signals, price_data)
```

### Analyse des signaux alpha

```python
from alpha_signal_analyzer import AlphaSignalAnalyzer

analyzer = AlphaSignalAnalyzer()
alpha_signals = analyzer.analyze(model_predictions, market_data)
analyzer.plot_signals()
```

## 🎯 Optimisation pour Apple Silicon (MPS)

Le code est optimisé pour les Mac avec Apple Silicon :

```python
# Automatique avec device="auto"
python train.py --config config.yaml --device auto
```

Optimisations incluses :
- Détection automatique du MPS
- Gestion de la mémoire MPS
- Batch size adaptatif
- Gradient accumulation pour grands modèles

## 📊 Monitoring et visualisation

### TensorBoard

```bash
tensorboard --logdir=checkpoints/tensorboard
```

### Weights & Biases

```python
import wandb

wandb.init(project="crypto-trading")
trainer.fit(train_loader, val_loader, use_wandb=True)
```

### Visualisation des signaux

```bash
python visualize_signals.py --checkpoint checkpoints/model.pt
```

## 🔄 Workflow complet

1. **Collecte de données** (depuis PIPELINE)
```bash
cd ../PIPELINE
python mass_data_collector_v2.py
```

2. **Préparation des données**
```python
from data.pipeline import DataPipeline
pipeline = DataPipeline(config)
train_loader, val_loader, test_loader = pipeline.get_data_loaders()
```

3. **Entraînement**
```bash
python train.py --config config/training_config.yaml
```

4. **Évaluation**
```python
metrics = trainer.evaluate(test_loader)
```

5. **Déploiement**
```python
model = load_model("checkpoints/best_model.pt")
predictions = model.predict(new_data)
```

## 🐛 Debugging

### Mode debug

```bash
python train.py --config config.yaml --debug_mode
```

### Fast dev run (sanity check)

```bash
python train.py --config config.yaml --fast_dev_run
```

### Logs

Les logs sont sauvegardés dans `checkpoints/training.log`

## 🔗 Intégration avec PIPELINE

Les données proviennent du module PIPELINE :

```python
# Charger depuis MongoDB
from pipeline import load_from_mongodb
data = load_from_mongodb()

# Ou charger depuis Parquet
import pandas as pd
data = pd.read_parquet("../PIPELINE/datasets/alpha_trading/...")
```

## 📚 Documentation complète

- `models/` - Documentation des architectures
- `training/` - Documentation du trainer
- `config/training_config.yaml` - Configuration détaillée

## 🤝 Support

Pour toute question :
1. Consultez les logs dans `checkpoints/training.log`
2. Vérifiez votre configuration dans `config/`
3. Testez avec `--debug_mode` ou `--fast_dev_run`
