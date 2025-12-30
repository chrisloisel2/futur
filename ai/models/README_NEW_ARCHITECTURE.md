# Architecture Corrigée : Régimes Binaires + Impulse Event

## TL;DR

**Problème** : Impulse classé comme régime → accuracy 46%
**Solution** : Régimes binaires {calm, reversal} + Impulse comme event detector
**Résultat** : Accuracy attendue >65%, architecture causale et interprétable

---

## Quick Start

### 1. Tests

```bash
cd ai/models/training/common
python3 test_integration.py
```

**Output attendu** :
```
TEST RESULTS: 5 passed, 0 failed
```

### 2. Demo Pipeline

```bash
python3 pipeline_integration_example.py
```

**Observe** :
- Régimes binaires (calm/reversal)
- Impulse detection (score + binary flag)
- Position downscale pendant impulse (mult=0.3)
- Switch MAKER→MARKET pendant impulse

---

## Architecture

```
Market Data
    ↓
┌───┴────┬──────────┐
│        │          │
Regime   Impulse   Edge
(bin)    (event)   (α)
│        │          │
└────────┼──────────┘
         ↓
    MetaControl
         ↓
    Execution
```

---

## Modules

| Module | Responsabilité | Status |
|--------|----------------|--------|
| [`regime_classifier_v2.py`](training/common/regime_classifier_v2.py) | Classification binaire calm/reversal | ✅ Modifié |
| [`impulse_detector.py`](training/common/impulse_detector.py) | Event detector (score causal) | ✅ Nouveau |
| [`impulse_gates.py`](training/common/impulse_gates.py) | Gates event-level | ✅ Nouveau |
| [`meta_control.py`](training/common/meta_control.py) | Position sizing + downscale | ✅ Nouveau |
| [`execution_engine.py`](training/common/execution_engine.py) | MAKER/TAKER routing | ✅ Nouveau |

---

## Formules Clés

### Impulse Score
```python
z_ret = |ret_1m| / rv_60
z_vol = (volume - volume_ma) / volume_std
impulse_score = sigmoid(0.5*z_ret + 0.3*z_vol + 0.2*spread_z - 2.0)
```

### Meta-Control
```python
size = base * regime_mult * impulse_mult * cooldown_mult

regime_mult   = 1.0 (calm) or 0.7 (reversal)
impulse_mult  = 0.3 (impulse active) or gradual blend
cooldown_mult = 0.5 (after loss) or 1.0
```

### Execution
```python
order_type = MARKET if impulse else LIMIT_MAKER
if impulse:
    cancel_all_open_orders()
```

---

## Métriques

### Régimes (Classification)
- Accuracy
- Recall per class (calm, reversal)
- ECE (calibration)
- Confusion matrix

### Impulse (Event-Level)
- Frequency per day
- Avg score
- Conditional PnL
- Execution cost ratio

---

## Gates Production

### Régimes
```python
accuracy > 0.60
calm_recall > 0.50
reversal_recall > 0.50
ece < 0.10
max(pred_dist) < 0.75
```

### Impulse
```python
0.5 < frequency < 20/day
avg_pnl > -0.001
cost_ratio < 2x
drawdown < 0.01 (warning)
```

---

## Usage Example

```python
from regime_classifier_v2 import train_calibrated_regime_classifier
from impulse_detector import ImpulseDetector
from meta_control import MetaControl
from execution_engine import ExecutionEngine

# Setup
regime_model = train_calibrated_regime_classifier(X, y, ['calm', 'reversal'])
impulse = ImpulseDetector(threshold=0.7)
meta = MetaControl()
exec_engine = ExecutionEngine()

# Process tick
regime = regime_model.predict([features])[0]
is_impulse, score = impulse.detect(ts, ret_1m, rv_60, vol, vol_ma, vol_std)

meta_out = meta.compute_position_size(
    base_size=1.0,
    regime='calm' if regime==0 else 'reversal',
    impulse_score=score,
    is_impulse=is_impulse,
)

order = exec_engine.place_order(
    size=meta_out.position_size,
    regime=meta_out.regime,
    impulse_active=is_impulse,
    impulse_score=score,
    mid_price=price,
)
```

---

## Documentation Complète

| Document | Description |
|----------|-------------|
| [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) | Guide détaillé de migration |
| [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md) | Synthèse technique complète |
| `README_NEW_ARCHITECTURE.md` | Ce fichier (quick ref) |

---

## Migration Checklist

- [x] Modules créés et testés
- [ ] **Re-entraîner modèle sur labels binaires** ← PRIORITÉ
- [ ] Backtest 2019-2023
- [ ] Validation gates
- [ ] Paper trading 30j
- [ ] Production rollout

---

## Breaking Changes

⚠️ **Les modèles 3-classes sont incompatibles**

Avant :
```python
if regime == "impulse":  # ❌ INCORRECT
    ...
```

Après :
```python
if is_impulse:  # ✅ CORRECT (event, not regime)
    ...
```

---

## FAQ

**Q: Pourquoi binaire ?**
A: Impulse n'est pas stationnaire → impossible à classifier (acc 46%)

**Q: Accuracy attendue ?**
A: >65% (vs 46% avant) sur régimes binaires

**Q: Comment tester impulse ?**
A: Métriques event-level (frequency, PnL), PAS accuracy/recall

**Q: Obligatoire ?**
A: Non. Si freq <0.5/day, désactiver ou ajuster threshold

---

## Support

1. Lire [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)
2. Run `python3 test_integration.py`
3. Run `python3 pipeline_integration_example.py`
4. Consulter docstrings des modules

---

*Architecture corrigée implémentée le 2025-12-29*
