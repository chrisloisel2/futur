# Quick Start - Edge Forecaster v2 (Calibrated)

## 🚀 Run Training (Default Settings)

```bash
cd /Users/christopher/Desktop/futur/trading-system

# Train avec paramètres optimisés (k_tp=2.0, m_sl=1.5)
python scripts/train_edge_forecaster.py \
    --start-date 2019-01-01 \
    --end-date 2023-12-31 \
    --symbol BTCUSDT \
    --horizon 60 \
    --epochs 50 \
    --device cpu \
    --output artifacts/models/edge/production_v2_calibrated.pt
```

## 📊 Monitorer tp_hit_rate

**APRÈS 1ER RUN**, chercher dans les logs:

```json
{
  "msg": "Forward labels generated (PRODUCTION v2 - CALIBRATION)",
  "tp_hit_rate_overall": "XX.XX%",  // ← CETTE VALEUR
  "tp_hit_rate_by_vol": {...}
}
```

### Ajustement Empirique

| tp_hit_rate_overall | Action |
|---------------------|--------|
| **> 0.50 (50%)** | Augmenter `--k-tp 2.5` ou `3.0` (TP plus difficile) |
| **0.45-0.50** | OK, mais légèrement facile → `--k-tp 2.2` |
| **0.40-0.45** | ✅ **CIBLE OPTIMALE** → garder défauts |
| **0.35-0.40** | OK, mais difficile → `--k-tp 1.8` |
| **< 0.35 (35%)** | Baisser `--k-tp 1.5` ou `1.2` (TP plus facile) |

### Re-run avec Ajustement

```bash
# Exemple: tp_hit_rate=52% → trop facile
python scripts/train_edge_forecaster.py \
    --start-date 2019-01-01 \
    --end-date 2023-12-31 \
    --symbol BTCUSDT \
    --horizon 60 \
    --k-tp 2.5 \  # Augmenté de 2.0 → 2.5
    --m-sl 1.5 \
    --output artifacts/models/edge/production_v2_k25.pt
```

## 📈 Métriques Critiques (Fin Training)

Chercher dans output final:

```
EDGE FORECASTER TRAINING RESULTS (CALIBRATED)
Brier Score (p_hit UNCALIBRATED): 0.XXXX
Brier Score (p_hit CALIBRATED): 0.XXXX      ← doit être < 0.20
ECE (BEFORE calibration): 0.XXXX
ECE (AFTER calibration): 0.XXXX             ← doit être < 0.15
MAE (q50): 0.XXXX                           ← doit être < 0.005
Sharpe (predictions): 0.XXXX                ← doit être > 0.5
Trading Metric Composite: 0.XXXX            ← doit être > 0.3
```

### Cibles de Performance

| Métrique | Cible | Excellent | Inacceptable |
|----------|-------|-----------|--------------|
| **Brier (calibrated)** | < 0.20 | < 0.15 | > 0.25 |
| **ECE (after)** | < 0.15 | < 0.10 | > 0.20 |
| **MAE (q50)** | < 0.005 | < 0.003 | > 0.010 |
| **Sharpe (pred)** | > 0.5 | > 1.0 | < 0.3 |
| **Trading Composite** | > 0.0 | > 0.3 | < -0.2 |

## 🔍 Troubleshooting

### Problème: Brier (calibrated) > 0.25
**Cause**: Modèle pas assez expressif ou labels encore trop faciles

**Solutions**:
1. Augmenter `--k-tp` (TP plus difficile)
2. Augmenter epochs: `--epochs 100`
3. Augmenter model capacity (éditer script: `d_model=256`, `n_layers=4`)

### Problème: Sharpe < 0.3
**Cause**: Modèle ne trouve pas de signal prédictif

**Solutions**:
1. Vérifier distribution labels (`tp_hit_rate_by_vol`) → doit varier par quantile
2. Feature engineering (ajouter indicators dans data pipeline)
3. Vérifier fuites de données (forward-looking bias)

### Problème: ECE (after) > 0.20
**Cause**: Calibration Platt insuffisante

**Solutions**:
1. Essayer Isotonic: éditer script ligne 962 → `calibration_method="isotonic"`
2. Plus de données (augmenter plage dates)
3. Split temporel biaisé (vérifier `--test-size 0.2`)

### Problème: JSON crash encore
**Cause**: `to_jsonable()` raté un type

**Debug**:
```python
# Dans script, après ligne 1011, ajouter:
print(type(metrics_extended))
print({k: type(v) for k, v in metrics_extended.items()})
```

## 📂 Fichiers Générés

Après training réussi:

```
artifacts/models/edge/
├── production_v2_calibrated.pt              # Modèle PyTorch
├── production_v2_calibrated_best_checkpoint.pt  # Checkpoint complet
├── production_v2_calibrated_calibrator.pkl  # Calibrateur Platt (NOUVEAU!)
└── production_v2_calibrated_metrics.json    # Métriques JSON (NOUVEAU!)
```

### Charger en Inference

```python
import pickle
import torch
from pipeline.models.edge.forecaster import EdgeForecasterModel

# Load model
model = EdgeForecasterModel.load("artifacts/models/edge/production_v2_calibrated.pt")

# Load calibrator
with open("artifacts/models/edge/production_v2_calibrated_calibrator.pkl", "rb") as f:
    calibrator = pickle.load(f)

# Inference
predictions = model.predict(df_features)  # Returns dict with q05, q50, q95, p_hit, ...

# Calibrate p_hit
p_hit_raw = predictions["p_hit"]
p_hit_calibrated = calibrator.predict_proba(p_hit_raw.reshape(-1, 1))[:, 1]

# Use calibrated probabilities
print(f"p_hit raw: {p_hit_raw.mean():.3f}")
print(f"p_hit calibrated: {p_hit_calibrated.mean():.3f}")
```

## 🎯 Workflow Complet

1. **Run training** avec défauts → noter `tp_hit_rate_overall`
2. **Ajuster k_tp** si besoin (cible 40-45%)
3. **Re-run** avec nouveau k_tp → vérifier métriques finales
4. **Valider**:
   - Brier < 0.20 ✅
   - ECE < 0.15 ✅
   - Sharpe > 0.5 ✅
   - Trading Composite > 0.3 ✅
5. **Déployer** si toutes cibles atteintes

## 📞 Support

Si problème persiste après ajustements:
1. Vérifier logs complets (`logger.info` output)
2. Inspecter `*_metrics.json` pour distribution détaillée
3. Vérifier données S3 (coverage, qualité)

---

**Dernière mise à jour**: 2025-12-30
**Version**: v2.0 (Calibrated)
