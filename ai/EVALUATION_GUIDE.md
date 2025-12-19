# Guide d'Évaluation - Capacités Prédictives du Modèle

Ce guide explique comment interpréter les métriques et logs générés pendant l'entraînement pour évaluer la capacité du modèle à prédire les mouvements du Bitcoin.

## 📊 Vue d'Ensemble des Évaluations

Le système d'entraînement génère **plusieurs niveaux d'évaluation** pour vous permettre d'estimer précisément l'évolution et les capacités prédictives du modèle:

### 1. Évaluation en Temps Réel (Chaque Epoch)
### 2. Rapports Détaillés JSON (Sauvegardés sur disque)
### 3. Historique d'Évolution (CSV + Graphiques)
### 4. TensorBoard (Visualisation interactive)

---

## 🎯 Métriques Clés pour Évaluer la Prédiction

### A. Qualité de la Prédiction Directionnelle

**Direction Accuracy** (`direction_accuracy`)
- **Quoi**: Pourcentage de fois où le modèle prédit correctement la direction (UP/DOWN/FLAT)
- **Objectif**: > 55% (pour 3 classes, random = 33%)
- **Excellent**: > 60%
- **Interprétation**: Si 58%, le modèle prédit correctement la direction dans 58% des cas

**Exemple de sortie:**
```
Direction Accuracy: 58.3%
```

### B. Qualité de la Prédiction des Returns

**Correlation** (`correlation`)
- **Quoi**: Corrélation entre les returns prédits et réels
- **Range**: -1 à +1
- **Objectif**: > 0.15
- **Excellent**: > 0.30
- **Interprétation**:
  - 0.25 = Relation positive modérée entre prédictions et réalité
  - Plus c'est proche de 1, mieux c'est

**R² Score** (`r2_score`)
- **Quoi**: Coefficient de détermination (variance expliquée)
- **Range**: -∞ à 1
- **Objectif**: > 0.10
- **Excellent**: > 0.25
- **Interprétation**:
  - 0.20 = Le modèle explique 20% de la variance des returns
  - Négatif = Pire que la moyenne

**MAE (Mean Absolute Error)** (`mae_returns`)
- **Quoi**: Erreur absolue moyenne sur les returns
- **Objectif**: < 0.005 (0.5%)
- **Excellent**: < 0.003
- **Interprétation**: Erreur moyenne en points de pourcentage

### C. Performance Trading Simulée

**Sharpe Ratio** (`sharpe_ratio`)
- **Quoi**: Rendement ajusté au risque (annualisé)
- **Objectif**: > 1.0
- **Excellent**: > 2.0
- **Interprétation**:
  - 1.5 = On gagne 1.5 unités de rendement pour 1 unité de risque
  - < 1.0 = Stratégie pas assez rentable pour le risque pris
  - > 2.0 = Excellent ratio rendement/risque

**Sortino Ratio** (`sortino_ratio`)
- **Quoi**: Comme Sharpe, mais ne pénalise que la volatilité négative
- **Objectif**: > 1.2
- **Excellent**: > 2.5
- **Interprétation**: Généralement plus élevé que Sharpe (plus réaliste)

**Win Rate** (`win_rate`)
- **Quoi**: Pourcentage de trades gagnants
- **Objectif**: > 50%
- **Excellent**: > 55%
- **Interprétation**: 52% = 52% des positions sont profitables

**Max Drawdown** (`max_drawdown_pct`)
- **Quoi**: Plus grande perte cumulée (en %)
- **Objectif**: < -20%
- **Excellent**: < -10%
- **Interprétation**: -15% = Perte maximale de 15% depuis un pic

**Profit Factor** (`profit_factor`)
- **Quoi**: Ratio gains totaux / pertes totales
- **Objectif**: > 1.2
- **Excellent**: > 1.5
- **Interprétation**:
  - 1.3 = Gains totaux = 1.3× les pertes totales
  - < 1.0 = Pertes > Gains (mauvais)

**Total Return** (`total_return_pct`)
- **Quoi**: Return cumulé sur la période de validation
- **Interprétation**: +12.5% = Le modèle aurait généré 12.5% de profit

### D. Analyse par Horizon

**MAE per Horizon** (`mae_per_horizon`)
- **Quoi**: Erreur à chaque step de prédiction (t+1, t+2, ..., t+12)
- **Objectif**: Dégradation graduelle acceptable
- **Interprétation**:
  ```
  Horizon t+1:  MAE = 0.0025  ← Très bon (court terme)
  Horizon t+6:  MAE = 0.0038  ← Acceptable (moyen terme)
  Horizon t+12: MAE = 0.0052  ← Normal (long terme)
  ```
  - Si MAE explose rapidement → Modèle ne peut prédire qu'à court terme
  - Si MAE reste stable → Modèle prédit bien sur tout l'horizon

**Direction Accuracy per Horizon** (`direction_accuracy_per_horizon`)
- **Quoi**: Précision directionnelle à chaque step
- **Interprétation**:
  ```
  Horizon t+1:  63.2%  ← Excellent
  Horizon t+6:  56.8%  ← Bon
  Horizon t+12: 52.1%  ← Acceptable (toujours mieux que random)
  ```

### E. Calibration des Prédictions

**Calibration Ratio** (`calibration_ratio`)
- **Quoi**: % de vraies valeurs tombant dans l'intervalle de confiance prédit
- **Objectif**: ~0.95 (pour 95% de confiance)
- **Range acceptable**: 0.90 - 0.97
- **Interprétation**:
  - 0.94 = Prédictions bien calibrées (94% des vraies valeurs dans IC)
  - 0.50 = Modèle trop confiant (sous-estime l'incertitude)
  - 0.99 = Modèle pas assez confiant (surestime l'incertitude)

---

## 📈 Sortie Console Pendant l'Entraînement

### Format par Epoch

```
================================================================================
EPOCH 5/20
================================================================================

  Training: 500/500 [==============================] - 720s

Training Metrics:
  loss:         0.0234
  ret_loss:     0.0156
  dir_loss:     0.0068
  rv_loss:      0.0010

Validation Metrics:
  val_loss:     0.0267
  val_ret_loss: 0.0178
  val_dir_loss: 0.0078
  val_rv_loss:  0.0011

  Direction Accuracy: 54.3%

Epoch 5 completed in 720.3s

================================================================================
DETAILED EVALUATION - Epoch 5
================================================================================

  Collecting predictions on validation set...
  Collected 44251 samples

=== PREDICTION QUALITY ===
  Direction Accuracy:      58.3%
  Correlation:             0.234
  R² Score:                0.167
  Direction Accuracy (Large Moves): 62.1%

=== HORIZON ANALYSIS ===
  Average MAE across horizons: 0.0045

  Per-Horizon MAE:
    t+1:  0.0025
    t+2:  0.0028
    t+3:  0.0032
    t+6:  0.0041
    t+12: 0.0058

  Per-Horizon Direction Accuracy:
    t+1:  63.2%
    t+6:  56.8%
    t+12: 52.1%

=== TRADING SIMULATION ===
  Initial Capital:         $10,000
  Final Capital:           $11,234
  Total Return:            +12.34%
  Max Drawdown:            -8.2%
  Sharpe Ratio:            1.456
  Sortino Ratio:           1.823
  Win Rate:                54.2%
  Total Trades:            8,850

=== CONFIDENCE INTERVALS ===
  Coverage (95% CI):       0.942
  Calibration Ratio:       0.942
  Mean Interval Width:     0.0082

Memory:
  RAM Usage:           13.2 GB / 16.0 GB (82.5%)
  GPU Memory:          8.1 GB / 12.0 GB (67.5%)

  ✓ Evaluation report saved to: training_output/evaluation/evaluation_epoch_005.json
```

### Interprétation de cet Exemple

**Direction Accuracy 58.3%** → Bon! Le modèle prédit correctement 58% du temps (vs 33% random)

**Correlation 0.234** → Modéré mais positif. Relation significative entre prédictions et réalité.

**R² 0.167** → Le modèle explique 16.7% de la variance. Acceptable pour du marché crypto 1-minute.

**Sharpe 1.456** → Bon! Rendement ajusté au risque supérieur à 1.

**Total Return +12.34%** → Si on avait tradé avec ce modèle, on aurait gagné 12.34%.

**Win Rate 54.2%** → Plus de trades gagnants que perdants.

**Max Drawdown -8.2%** → Excellent! Perte maximale limitée à 8.2%.

**Horizon Analysis** → Le modèle prédit bien à court terme (t+1: 63%), et dégrade graduellement mais reste prédictif à t+12 (52% > 33%)

**Calibration 0.942** → Excellente calibration! Intervalles de confiance fiables.

---

## 📁 Fichiers Générés

### 1. Rapports JSON Détaillés

**Emplacement**: `training_output/evaluation/evaluation_epoch_XXX.json`

Contient toutes les métriques pour chaque epoch:
```json
{
  "epoch": 5,
  "prediction_quality": {
    "direction_accuracy": 0.583,
    "correlation": 0.234,
    "r2_score": 0.167,
    "direction_accuracy_large_moves": 0.621
  },
  "horizon_analysis": {
    "avg_mae": 0.0045,
    "mae_per_horizon": [0.0025, 0.0028, ...],
    "direction_accuracy_per_horizon": [0.632, 0.598, ...]
  },
  "trading_simulation": {
    "initial_capital": 10000,
    "final_capital": 11234,
    "total_return_pct": 12.34,
    "sharpe_ratio": 1.456,
    "sortino_ratio": 1.823,
    "max_drawdown_pct": -8.2,
    "win_rate": 0.542,
    "total_trades": 8850
  },
  "confidence_intervals": {
    "coverage_95": 0.942,
    "calibration_ratio": 0.942,
    "mean_interval_width": 0.0082
  }
}
```

### 2. Historique d'Évolution

**Emplacement**: `training_output/evaluation/evolution_history.csv`

Suivi de l'évolution des métriques clés:
```csv
epoch,direction_accuracy,correlation,r2_score,sharpe_ratio,total_return_pct,max_drawdown_pct,win_rate
1,0.512,0.089,0.034,0.456,3.2,-15.4,0.498
2,0.534,0.145,0.098,0.876,6.8,-12.1,0.512
3,0.548,0.198,0.134,1.123,9.4,-9.8,0.528
4,0.561,0.221,0.156,1.345,11.2,-8.5,0.541
5,0.583,0.234,0.167,1.456,12.3,-8.2,0.542
```

**Utilisation**:
```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('training_output/evaluation/evolution_history.csv')

plt.figure(figsize=(15, 10))

# Direction Accuracy
plt.subplot(3, 3, 1)
plt.plot(df['epoch'], df['direction_accuracy'])
plt.title('Direction Accuracy Evolution')
plt.axhline(0.55, color='r', linestyle='--', label='Target')
plt.legend()

# Sharpe Ratio
plt.subplot(3, 3, 2)
plt.plot(df['epoch'], df['sharpe_ratio'])
plt.title('Sharpe Ratio Evolution')
plt.axhline(1.0, color='r', linestyle='--', label='Target')
plt.legend()

# Total Return
plt.subplot(3, 3, 3)
plt.plot(df['epoch'], df['total_return_pct'])
plt.title('Total Return %')

plt.tight_layout()
plt.savefig('model_evolution.png', dpi=150)
```

### 3. Résumé Final

**Emplacement**: `training_output/evaluation/evolution_history.json`

À la fin de l'entraînement:
```
================================================================================
TRAINING EVOLUTION SUMMARY
================================================================================

Direction Accuracy:
  Initial:  51.2%
  Final:    58.3%
  Best:     60.1%

Correlation:
  Initial:  0.0894
  Final:    0.2341
  Best:     0.2567

R² Score:
  Initial:  0.0345
  Final:    0.1678
  Best:     0.1892

Trading Performance:
  Initial Return:  +3.2%
  Final Return:    +12.3%
  Best Return:     +14.8%

Sharpe Ratio:
  Initial:  0.456
  Final:    1.456
  Best:     1.678
================================================================================
```

---

## 🎯 Comment Savoir si le Modèle est Bon?

### Checklist de Validation

✅ **Direction Accuracy > 55%**
- Si oui → Le modèle a un edge directionnel

✅ **Correlation > 0.15**
- Si oui → Prédictions alignées avec la réalité

✅ **Sharpe Ratio > 1.0**
- Si oui → Stratégie rentable ajustée au risque

✅ **Win Rate > 50%**
- Si oui → Plus de trades gagnants que perdants

✅ **Max Drawdown < -20%**
- Si oui → Risque de perte contrôlé

✅ **Calibration Ratio ≈ 0.95**
- Si oui → Intervalles de confiance fiables

✅ **Amélioration continue**
- Direction Accuracy augmente epoch après epoch
- Sharpe Ratio augmente
- Max Drawdown diminue (moins négatif)

### Signes d'Alerte 🚨

⚠️ **Direction Accuracy stagne à ~33%**
→ Modèle ne fait pas mieux que random, pas prédictif

⚠️ **Correlation proche de 0 ou négative**
→ Prédictions non alignées avec la réalité

⚠️ **Sharpe Ratio < 0.5**
→ Stratégie non rentable

⚠️ **Win Rate < 48%**
→ Trop de trades perdants

⚠️ **Max Drawdown < -30%**
→ Risque trop élevé

⚠️ **Metrics ne s'améliorent pas**
→ Modèle n'apprend pas, revoir architecture/hyperparamètres

---

## 📊 TensorBoard

Lancer TensorBoard:
```bash
tensorboard --logdir=training_output/tensorboard/ --port=6006
```

Puis ouvrir: `http://localhost:6006`

### Onglets à Surveiller:

1. **SCALARS → trading/**
   - sharpe_ratio (cible: croissant, > 1.0)
   - max_drawdown (cible: moins négatif)
   - win_rate (cible: > 0.50)

2. **SCALARS → classification/**
   - accuracy (cible: > 0.55)
   - macro_f1 (cible: > 0.45)

3. **SCALARS → regression/**
   - mae_returns (cible: décroissant, < 0.005)
   - r2_returns (cible: croissant, > 0.10)

4. **SCALARS → memory/**
   - ram_percent (cible: < 90%)
   - gpu_percent (cible: < 90%)

---

## 🔍 Analyse Approfondie Post-Training

### Charger les Rapports

```python
import json
import pandas as pd

# Charger un rapport d'epoch spécifique
with open('training_output/evaluation/evaluation_epoch_020.json') as f:
    report = json.load(f)

print(f"Direction Accuracy: {report['prediction_quality']['direction_accuracy']:.2%}")
print(f"Sharpe Ratio: {report['trading_simulation']['sharpe_ratio']:.4f}")

# Charger l'historique complet
df_evolution = pd.read_csv('training_output/evaluation/evolution_history.csv')
print(df_evolution.describe())

# Meilleur epoch pour chaque métrique
best_sharpe_epoch = df_evolution['sharpe_ratio'].idxmax()
best_accuracy_epoch = df_evolution['direction_accuracy'].idxmax()

print(f"\nBest Sharpe at epoch: {best_sharpe_epoch + 1}")
print(f"Best Accuracy at epoch: {best_accuracy_epoch + 1}")
```

### Analyser l'Horizon

```python
# Charger rapport final
with open('training_output/evaluation/evaluation_epoch_020.json') as f:
    report = json.load(f)

mae_per_h = report['horizon_analysis']['mae_per_horizon']
acc_per_h = report['horizon_analysis']['direction_accuracy_per_horizon']

# Plot dégradation
import matplotlib.pyplot as plt

fig, axes = plt.subplots(1, 2, figsize=(12, 4))

axes[0].plot(range(1, 13), mae_per_h, marker='o')
axes[0].set_xlabel('Horizon (minutes)')
axes[0].set_ylabel('MAE')
axes[0].set_title('MAE Degradation over Horizon')
axes[0].grid(True, alpha=0.3)

axes[1].plot(range(1, 13), [a*100 for a in acc_per_h], marker='o')
axes[1].axhline(50, color='r', linestyle='--', label='Better than random')
axes[1].set_xlabel('Horizon (minutes)')
axes[1].set_ylabel('Direction Accuracy (%)')
axes[1].set_title('Direction Accuracy over Horizon')
axes[1].legend()
axes[1].grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('horizon_analysis.png', dpi=150)
```

---

## 🚀 Prochaines Étapes

Après analyse des résultats:

### Si le modèle est BON (✅ checklist):
1. Charger le meilleur checkpoint: `training_output/checkpoints/best_val_loss.keras`
2. Backtester sur données 2024 complètes
3. Déployer en production ou live trading

### Si le modèle est MOYEN (quelques ✅):
1. Analyser quels horizons fonctionnent le mieux
2. Ajuster hyperparamètres (learning rate, model size)
3. Augmenter epochs ou données d'entraînement

### Si le modèle est MAUVAIS (❌ checklist):
1. Vérifier qualité des données (voir diagnose_data.py)
2. Revoir features engineering
3. Augmenter complexité du modèle (d_model, n_heads)
4. Changer architecture (ajouter layers)

---

## 💡 Tips d'Interprétation

1. **Ne pas se focaliser sur une seule métrique**
   - Un bon Sharpe avec une mauvaise accuracy peut indiquer surfit sur les gros mouvements
   - Une bonne accuracy avec un mauvais Sharpe peut indiquer des erreurs de magnitude

2. **Regarder l'évolution, pas seulement la valeur finale**
   - Tendance croissante = bon signe même si valeur finale moyenne
   - Stagnation = problème d'apprentissage

3. **Comparer horizons**
   - Si t+1 bon mais t+12 mauvais → Utiliser seulement prédictions court terme
   - Si stable → Modèle robuste sur tout l'horizon

4. **Valider calibration**
   - Si mal calibré → Ne pas faire confiance aux intervalles de confiance
   - Si bien calibré → Utiliser IC pour dimensionner positions

5. **Trading simulation ≠ Backtest réel**
   - Les métriques trading sont simulées avec modèle simple
   - Pour validation finale, faire backtest complet avec slippage, frais, etc.

---

**Bon entraînement et bonne analyse! 🚀**
