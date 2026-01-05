# 🚀 OPTIMISATIONS ENTRAÎNEMENT MODÈLE

Ce document décrit les optimisations appliquées au système d'entraînement du modèle Transformer (d_model=192, n_layers=5).

## 📊 MODIFICATIONS APPLIQUÉES

### 1. 🎯 Fix Gradient Clipping
- **Avant**: `grad_clip=1.0`
- **Après**: `grad_clip=5.0`
- **Raison**: Valeur plus sûre pour ce Transformer, évite la troncature excessive des gradients

### 2. 📈 Fix Learning Rate + Scheduler
- **LR**: `2e-4` → `3e-4` (compensation de l'augmentation du grad clip)
- **Warmup**: `0.05` → `0.10` (warmup plus long pour stabilité)
- **Scheduler**: OneCycleLR conservé avec ordre correct (`optimizer.step()` puis `scheduler.step()`)

### 3. 🛡️ Stabilisation Training
- **Patience**: `10` → `15` (plus de tolérance avant early stopping)
- **Min Delta**: `1e-4` → `5e-5` (seuil plus fin pour détecter amélioration)
- **Protection NaN/Inf**: Détection automatique + sauvegarde d'urgence si divergence

### 4. 🔬 Debug Overfit Mode
- **Activation**: `--debug-overfit` ou script `train_optimized.sh overfit`
- **Dataset**: Limité à 256 samples (derniers échantillons)
- **Config**:
  - `dropout=0.0`
  - `weight_decay=0.0`
  - `grad_clip=1000.0`
  - `lr=1e-3`
  - Epochs limitées
- **Objectif**: Loss doit tendre vers 0 en 500-1000 steps

## 🛠️ UTILISATION

### Mode Normal (Optimisé)
```bash
# Via script utilitaire
./train_optimized.sh normal

# Ou directement
python train.py --config config_optimized.json --run-id optimized_v1
```

### Mode Debug Overfit
```bash
# Via script utilitaire
./train_optimized.sh overfit

# Ou directement
python train.py --debug-overfit --config config_debug_overfit.json
```

### Mode Personnalisé
```bash
# Avec votre propre config JSON
./train_optimized.sh custom --config ma_config.json

# Ou avec symbole spécifique
./train_optimized.sh normal --symbol ETHUSDT --device cuda
```

## 📁 FICHIERS DE CONFIGURATION

### `config_optimized.json`
Configuration principale optimisée avec tous les paramètres recommandés.

### `config_debug_overfit.json`
Configuration spécifique pour le test de sanité d'overfitting.

## 🔍 DIAGNOSTIC

### Mode Debug Overfit - Critères de Succès
1. ✅ **Loss décroissante**: Doit diminuer régulièrement
2. ✅ **Convergence**: Loss < 0.01 en moins de 1000 epochs
3. ✅ **Pas de divergence**: Pas de NaN/Inf dans les gradients
4. ✅ **Overfitting confirmé**: Accuracy sur données d'entraînement → 100%

### Protection Anti-Divergence
- Détection automatique des NaN/Inf dans la loss
- Sauvegarde d'urgence du dernier état sain
- Arrêt immédiat avec rapport détaillé

## ⚙️ PARAMÈTRES CONFIGURABLES VIA JSON

Tous les paramètres clés sont configurables sans modification du code :

```json
{
  "edge": {
    "grad_clip": 5.0,        // Clipping des gradients
    "lr": 3e-4,              // Learning rate
    "warmup_pct": 0.10,      // Pourcentage de warmup
    "patience": 15,          // Early stopping patience
    "min_delta": 5e-5,       // Seuil amélioration minimale
    "epochs": 40,            // Nombre d'epochs
    "batch_size": 256,       // Taille des batches
    "dropout": 0.05,         // Dropout rate
    "weight_decay": 1e-5     // Weight decay L2
  }
}
```

## 🎯 RECOMMANDATIONS D'UTILISATION

1. **Développement**: Commencer par `train_optimized.sh overfit` pour vérifier que le modèle peut apprendre
2. **Production**: Utiliser `train_optimized.sh normal` avec config optimisée
3. **Expérimentation**: Créer des configs JSON personnalisées selon besoins
4. **Monitoring**: Surveiller les métriques de divergence et la stabilité des gradients

## 📞 RÉSOLUTION DE PROBLÈMES

### "Training diverged with loss=NaN"
- ✅ Protection activée correctement
- 🔍 Vérifier: qualité des données, learning rate trop élevé, problème de normalisation
- 💾 Checkpoint d'urgence sauvé dans `artifacts/emergency/`

### "Debug overfit mode ne converge pas"
- 🔍 Problème probable: architecture, implémentation, ou données corrompues
- 📝 Vérifier: feature engineering, targets bien définies, pas de data leakage

### "Early stopping trop précoce"
- ⚙️ Augmenter `patience` dans config JSON
- 📊 Réduire `min_delta` pour être plus permissif
