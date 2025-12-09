# 🤖 Module AI - Intelligence Artificielle et Entraînement

Ce dossier contient toute la partie **modèles d'IA**, **entraînement** et **conception des stratégies de trading**.

## 📁 Structure

```
ai/
├── models/              # Modèles d'IA (transformers, DLinear, TimesNet, etc.)
│   ├── pipeline/        # Pipeline de modèles intégré
│   └── ...
├── training/            # Scripts et utilitaires d'entraînement
├── utils/               # Utilitaires pour métriques et configuration
├── data_pipeline/       # Pipeline de features pour l'entraînement
├── checkpoints/         # Checkpoints des modèles sauvegardés
├── TRAIN/               # Données et résultats d'entraînement
├── train.py             # Script principal d'entraînement
├── alpha_signal_analyzer.py    # Analyse des signaux alpha
├── trading_strategy_example.py # Exemples de stratégies
└── visualize_signals.py        # Visualisation des signaux
```

## 🎯 Objectif

Ce module se concentre sur:
- **Conception et entraînement des modèles** d'IA pour le trading
- **Génération de signaux alpha** à partir des données
- **Évaluation des performances** des modèles
- **Fine-tuning et optimisation** des architectures

## 🚀 Utilisation

### Entraîner un modèle

```bash
cd ai
python train.py
```

### Analyser les signaux alpha

```bash
python alpha_signal_analyzer.py
```

### Visualiser les résultats

```bash
python visualize_signals.py
```

## 🔗 Dépendances

Les modèles utilisent principalement:
- PyTorch / TensorFlow
- Transformers
- NumPy / Pandas
- Scikit-learn

---

Pour la partie **collecte de données** et **frontend**, voir le dossier [frontend_pipeline/](../frontend_pipeline/)
