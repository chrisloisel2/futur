# 📚 INDEX - Navigation du projet

## 🎯 Guides principaux

| Document | Description | Pour qui ? |
|----------|-------------|-----------|
| [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md) | Explication ultra claire de toute la structure | Débutants ⭐ |
| [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md) | Architecture complète avec SSL | Tous |
| [ai/SELF_SUPERVISED/QUICKSTART.md](ai/SELF_SUPERVISED/QUICKSTART.md) | Démarrage rapide SSL (5 min) | Impatients 🚀 |
| [ai/SELF_SUPERVISED/README.md](ai/SELF_SUPERVISED/README.md) | Documentation complète SSL | Développeurs |

---

## 📦 Modules du projet

### 1. FRONTEND_PIPELINE (Collecte de données)

**Localisation** : `/frontend_pipeline/`

**Fichiers clés** :
- `mass_data_collector_v2.py` - Collecteur principal
- `api_server.py` - API REST
- `mongo_utils.py` - Utilitaires MongoDB

**Documentation** :
- Section dans [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md#module-1--frontend_pipeline-collecte-de-données)

**Lancer** :
```bash
cd frontend_pipeline
python mass_data_collector_v2.py
```

---

### 2. SELF_SUPERVISED (Pré-entraînement SSL) ⭐ NOUVEAU

**Localisation** : `/ai/SELF_SUPERVISED/`

**Fichiers clés** :
- `model_ssl.py` - TS2Vec, MAE, SimCLR
- `pretrain.py` - Boucles d'entraînement
- `example_usage.py` - Exemples d'utilisation
- `test_ssl.py` - Tests unitaires

**Documentation** :
- [QUICKSTART.md](ai/SELF_SUPERVISED/QUICKSTART.md) - Démarrage rapide
- [README.md](ai/SELF_SUPERVISED/README.md) - Documentation complète
- Section dans [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md#module-2--self_supervised-pré-entraînement-ssl)

**Lancer** :
```bash
# Tester l'installation
cd ai
python SELF_SUPERVISED/test_ssl.py

# Pré-entraîner TS2Vec
python SELF_SUPERVISED/example_usage.py --mode ts2vec
```

---

### 3. TRAIN (Entraînement supervisé)

**Localisation** : `/ai/TRAIN/`

**Fichiers clés** :
- `train.py` - Script d'entraînement principal
- `models/multi_modal_trading.py` - Modèle Transformer
- `training/trainer.py` - Classe Trainer
- `data/pipeline.py` - DataPipeline

**Documentation** :
- [README.md](ai/TRAIN/README.md) - Documentation complète
- Section dans [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md#module-2--aitrain-entraînement-des-modèles)

**Lancer** :
```bash
cd ai/TRAIN
python train.py --config config/training_config.yaml --device auto
```

---

### 4. MODELS/PIPELINE (Preprocessing avancé)

**Localisation** : `/ai/models/pipeline/`

**Fichiers clés** :
- `preprocessor.py` - Fractional diff, normalisation
- `features.py` - Feature engineering (50+ indicateurs)
- `models/fusion.py` - Fusion multimodale
- `models/dlinear.py`, `timesnet.py` - Modèles séries temporelles

**Documentation** :
- Section dans [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md#module-3--aimodelspipeline-traitement-avancé)

---

## 🚀 Quick Start par use case

### Je veux : Démarrer rapidement avec SSL

1. **Installation** (5 min)
   ```bash
   cd ai/SELF_SUPERVISED
   pip install -r requirements.txt
   cd ..
   python SELF_SUPERVISED/test_ssl.py
   ```

2. **Configuration** (2 min)
   - Éditer `ai/SELF_SUPERVISED/config_ssl.yaml`

3. **Lancer** (1h)
   ```bash
   python SELF_SUPERVISED/example_usage.py --mode ts2vec
   ```

**Doc** : [QUICKSTART.md](ai/SELF_SUPERVISED/QUICKSTART.md)

---

### Je veux : Comprendre toute l'architecture

1. **Lire** : [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md)
2. **Approfondir** : [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md)
3. **SSL détails** : [ai/SELF_SUPERVISED/README.md](ai/SELF_SUPERVISED/README.md)

---

### Je veux : Collecter des données crypto

1. **Configuration**
   - Vérifier `frontend_pipeline/.env` (optionnel)

2. **Lancer**
   ```bash
   cd frontend_pipeline
   python mass_data_collector_v2.py
   ```

3. **Vérifier MongoDB**
   - Base : `trader2`
   - Collections : `historical_ohlcv`, etc.

**Doc** : [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md#module-1--frontend_pipeline-collecte-de-données)

---

### Je veux : Entraîner un modèle de trading

**Option A : Sans SSL (classique)**
```bash
cd ai/TRAIN
python train.py --config config/training_config.yaml
```

**Option B : Avec SSL (recommandé) ⭐**
```bash
# 1. Pré-entraîner
cd ai
python SELF_SUPERVISED/example_usage.py --mode ts2vec

# 2. Fine-tuner (modifier train.py pour charger encoder)
cd TRAIN
python train.py --config config/training_config.yaml
```

**Doc** : [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md#-utilisation-pratique)

---

## 📖 Glossaire

| Terme | Définition | Où en savoir plus |
|-------|------------|-------------------|
| **SSL** | Self-Supervised Learning | [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md#module-2--self_supervised-pré-entraînement-ssl) |
| **TS2Vec** | Modèle de contrastive learning pour séries temporelles | [README SSL](ai/SELF_SUPERVISED/README.md#1-ts2vec-recommandé-) |
| **MAE** | Masked Autoencoder | [README SSL](ai/SELF_SUPERVISED/README.md#2-mae-masked-autoencoder) |
| **Contrastive Learning** | Apprentissage par comparaison | [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md#1-contrastive-learning) |
| **Transfer Learning** | Réutiliser encoder pré-entraîné | [QUICKSTART SSL](ai/SELF_SUPERVISED/QUICKSTART.md#utiliser-le-modèle-pré-entraîné) |
| **Fine-tuning** | Affiner modèle sur tâche spécifique | [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md#étape-3--fine-tuning-supervisé-30min) |
| **Embeddings** | Représentations vectorielles apprises | [README SSL](ai/SELF_SUPERVISED/README.md#-composants-techniques) |

---

## 🗂️ Structure complète des fichiers

```
futur/
├── INDEX.md                          ← Vous êtes ici
├── EXPLICATION_STRUCTURE.md          ← Guide complet
├── ARCHITECTURE_COMPLETE.md          ← Architecture avec SSL
│
├── frontend_pipeline/                # Collecte de données
│   ├── mass_data_collector_v2.py
│   ├── api_server.py
│   └── mongo_utils.py
│
└── ai/
    ├── SELF_SUPERVISED/              # SSL ⭐ NOUVEAU
    │   ├── QUICKSTART.md             ← Démarrage rapide
    │   ├── README.md                 ← Doc complète
    │   ├── model_ssl.py              ← TS2Vec, MAE, SimCLR
    │   ├── pretrain.py               ← Boucles training
    │   ├── contrastive.py            ← Losses
    │   ├── masking_strategies.py     ← Masking
    │   ├── dataloader_ssl.py         ← DataLoader
    │   ├── mae.py                    ← Components MAE
    │   ├── config_ssl.yaml           ← Config
    │   ├── example_usage.py          ← Exemples
    │   ├── test_ssl.py               ← Tests
    │   └── requirements.txt
    │
    ├── TRAIN/                        # Supervised learning
    │   ├── README.md
    │   ├── train.py
    │   ├── models/
    │   ├── training/
    │   └── data/
    │
    └── models/pipeline/              # Preprocessing
        ├── preprocessor.py
        ├── features.py
        └── models/
```

---

## 🎓 Parcours d'apprentissage recommandé

### Niveau 1 : Débutant

1. ✅ Lire [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md)
2. ✅ Tester l'installation : `python SELF_SUPERVISED/test_ssl.py`
3. ✅ Lancer collecte : `python mass_data_collector_v2.py`

### Niveau 2 : Intermédiaire

1. ✅ Lire [QUICKSTART SSL](ai/SELF_SUPERVISED/QUICKSTART.md)
2. ✅ Pré-entraîner TS2Vec
3. ✅ Comprendre embeddings

### Niveau 3 : Avancé

1. ✅ Lire [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md)
2. ✅ Lire [README SSL complet](ai/SELF_SUPERVISED/README.md)
3. ✅ Implémenter fine-tuning
4. ✅ Backtesting et optimisation

---

## 🔍 Recherche rapide

**Je cherche :** → **Où aller :**

| Quoi | Où |
|------|-----|
| Comprendre SSL | [README SSL](ai/SELF_SUPERVISED/README.md) |
| Démarrer SSL rapidement | [QUICKSTART SSL](ai/SELF_SUPERVISED/QUICKSTART.md) |
| Comprendre architecture globale | [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md) |
| Comprendre collecte données | [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md#module-1--frontend_pipeline-collecte-de-données) |
| Feature engineering | [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md#-features---feature-engineering) |
| Modèles séries temporelles | [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md#-fusion-model---architecture-avancée) |
| Configuration MongoDB | [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md#mongodb) |
| Tests unitaires SSL | `ai/SELF_SUPERVISED/test_ssl.py` |
| Exemples d'utilisation | `ai/SELF_SUPERVISED/example_usage.py` |
| Troubleshooting SSL | [README SSL](ai/SELF_SUPERVISED/README.md#-troubleshooting) |

---

## 📞 Support et ressources

### Documentation

- **Guide débutant** : [EXPLICATION_STRUCTURE.md](EXPLICATION_STRUCTURE.md)
- **Architecture** : [ARCHITECTURE_COMPLETE.md](ARCHITECTURE_COMPLETE.md)
- **SSL** : [README SSL](ai/SELF_SUPERVISED/README.md)
- **Training** : [README TRAIN](ai/TRAIN/README.md)

### Code

- **Tests SSL** : `ai/SELF_SUPERVISED/test_ssl.py`
- **Exemples SSL** : `ai/SELF_SUPERVISED/example_usage.py`
- **Config SSL** : `ai/SELF_SUPERVISED/config_ssl.yaml`

### Logs

- **Collecte** : `frontend_pipeline/datasets/alpha_trading/collection.log`
- **SSL** : `ai/SELF_SUPERVISED/checkpoints/{model}/training.log`
- **Training** : `ai/TRAIN/checkpoints/training.log`

---

## ✅ Checklist de démarrage

### Installation

- [ ] Installer dépendances : `pip install -r ai/SELF_SUPERVISED/requirements.txt`
- [ ] Tester installation : `python ai/SELF_SUPERVISED/test_ssl.py`
- [ ] Vérifier MongoDB : Connexion disponible
- [ ] Vérifier device : MPS/CUDA/CPU

### Configuration

- [ ] Éditer `ai/SELF_SUPERVISED/config_ssl.yaml`
- [ ] Vérifier MongoDB URI
- [ ] Choisir symbols à analyser
- [ ] Ajuster hyperparamètres

### Premier run

- [ ] Collecter données : `python frontend_pipeline/mass_data_collector_v2.py`
- [ ] Vérifier MongoDB : Données présentes
- [ ] Pré-entraîner SSL : `python ai/SELF_SUPERVISED/example_usage.py --mode ts2vec`
- [ ] Vérifier checkpoint : `checkpoints/ts2vec/ts2vec_final.pt`

### Utilisation

- [ ] Charger encoder pré-entraîné
- [ ] Tester extraction embeddings
- [ ] Implémenter fine-tuning
- [ ] Backtester stratégie

---

## 🎯 Prochaines étapes

Après avoir terminé la checklist :

1. **Évaluer** : Tester qualité des embeddings (linear probing)
2. **Intégrer** : Utiliser encoder dans module TRAIN
3. **Optimiser** : Hyperparameter tuning
4. **Déployer** : Production ready

---

**Navigation facilitée ! Tout est à portée de clic. 🚀**

## 📊 Statistiques du projet

- **Modules** : 4 (PIPELINE, SSL, TRAIN, PREPROCESSING)
- **Fichiers Python SSL** : 8 fichiers principaux
- **Modèles SSL** : 3 (TS2Vec, MAE, SimCLR)
- **Documentation** : 5 fichiers MD complets
- **Tests** : 7 tests unitaires (test_ssl.py)
- **Lignes de code SSL** : ~3000 lignes
- **Ready for production** : ✅

---

**Bon coding ! 🎉**
