# Migration Guide: Régimes Binaires + Impulse Event

## Vue d'ensemble

Ce guide documente la refonte architecturale majeure du système de régimes de marché.

### Changement fondamental

**AVANT (incorrect) :**
```
Regime ∈ {calm, impulse, reversal}  ← ERREUR: impulse n'est pas un régime
```

**APRÈS (correct) :**
```
Regime ∈ {calm, reversal}           ← Régimes stationnaires
Impulse = EVENT detector             ← Événement non-stationnaire
```

---

## Justification technique

### Problème identifié (audit empirique 2019-2023)

| Métrique | Valeur | Problème |
|----------|--------|----------|
| Accuracy | 46% | Plafond structurel |
| Impulse recall | 31% | Confusion reversal/impulse |
| ECE | >0.10 | Calibration dégradée |

**Cause racine :** `impulse` n'est pas stationnaire → impossible à classifier correctement.

**Preuve :** Aucun modèle (logistic, SGD, calibré, équilibré) ne dépasse ce plafond.

### Solution

1. **Régimes binaires** : `{calm, reversal}` uniquement
   - Stationnaires
   - Séparables
   - Target accuracy >65%

2. **Impulse réintroduit comme event** :
   - Détection causale (pas de future leak)
   - Intégration dans MetaControl (downscale)
   - Intégration dans Execution (MAKER→TAKER)

---

## Fichiers modifiés

### 1. `regime_classifier_v2.py`

**Changements :**
```python
# AVANT
DEFAULT_CLASSES = ["calm", "impulse", "reversal"]

# APRÈS
DEFAULT_CLASSES = ["calm", "reversal"]  # BINARY
```

**Gates mis à jour :**
- Supprimé : `impulse_recall < 0.35`
- Ajouté : `accuracy < 0.60` (binaire)
- Ajouté : `calm_recall < 0.50` et `reversal_recall < 0.50`

### 2. `impulse_detector.py` (NOUVEAU)

**Responsabilités :**
- Détection événementielle (non-classification)
- Score ∈ [0,1] basé sur :
  - Return shock (|ret_1m| / rv_60)
  - Volume shock (z-score)
  - Spread expansion (optionnel)

**Formule :**
```python
z_ret = |ret_1m| / rv_60
z_vol = (volume - volume_ma) / volume_std
raw_score = 0.5*z_ret + 0.3*z_vol + 0.2*spread_z
impulse_score = sigmoid(raw_score - 2.0)
is_impulse = impulse_score > 0.7
```

**Métriques event-level :**
- Frequency per day
- Avg score
- Conditional PnL
- Execution cost

### 3. `impulse_gates.py` (NOUVEAU)

**Gates de production :**
1. **Frequency** : 0.5 < freq < 20/day
2. **PnL** : avg_pnl > -0.001
3. **Cost** : impulse_cost < 2x normal_cost
4. **Drawdown** : avg_dd < 0.01 (warning)

### 4. `meta_control.py` (NOUVEAU)

**Intégration impulse :**
```python
# Multiplicateurs (tous multiplicatifs)
regime_mult = 1.0 if calm else 0.7
impulse_mult = 0.3 if is_impulse else 1.0 - 0.7*impulse_score
cooldown_mult = 0.5 if in_cooldown else 1.0

final_size = base_size * regime_mult * impulse_mult * cooldown_mult
```

**Cooldown :**
- Déclenché après perte > -50bps
- Durée : 1h
- Multiplier : 0.5

### 5. `execution_engine.py` (NOUVEAU)

**Routing impulse-aware :**
```python
if is_impulse:
    order_type = MARKET           # TAKER
    cancel_all_open_orders()      # Éviter adverse selection
else:
    order_type = LIMIT_MAKER      # MAKER (rebate)
```

---

## Pipeline intégré

```
┌─────────────┐
│ Market Data │
└──────┬──────┘
       │
   ┌───┴───┬─────────────────┐
   │       │                 │
┌──▼──┐  ┌─▼────────┐  ┌────▼─────┐
│Regime│  │  Impulse │  │  Edge    │
│(bin) │  │  (event) │  │  (alpha) │
└──┬───┘  └─────┬────┘  └────┬─────┘
   │            │            │
   └────────┬───┴────────────┘
            │
      ┌─────▼──────┐
      │MetaControl │ ← impulse downscale
      └─────┬──────┘
            │
      ┌─────▼─────┐
      │ Execution │ ← MAKER/TAKER switch
      └───────────┘
```

---

## Migration steps

### Phase 1 : Re-entraînement (CRITIQUE)

1. **Regénérer labels** (binaire uniquement)
```python
# Supprimer tous les labels "impulse"
labels = np.where(labels == 1, -1, labels)  # impulse → invalid
labels = labels[labels >= 0]  # drop invalid
```

2. **Re-entraîner modèle** sur labels binaires
```bash
python train_regime_classifier.py --classes calm reversal
```

3. **Valider accuracy >65%**

### Phase 2 : Intégration impulse

1. **Ajouter features impulse** au DataFrame
```python
from impulse_detector import create_impulse_features_batch
df = create_impulse_features_batch(df)
# Ajoute : impulse_score, is_impulse
```

2. **Intégrer dans meta-control**
```python
meta_output = meta_control.compute_position_size(
    base_size=base_size,
    regime=regime,
    impulse_score=df['impulse_score'].iloc[t],
    is_impulse=df['is_impulse'].iloc[t],
    recent_pnl=recent_pnl,
)
```

3. **Intégrer dans execution**
```python
order = execution_engine.place_order(
    size=meta_output.position_size,
    regime=regime,
    impulse_active=meta_output.impulse_active,
    impulse_score=meta_output.impulse_score,
    mid_price=mid_price,
)
```

### Phase 3 : Validation production

1. **Backtest complet** (2019-2023)
   - Metrics régimes : accuracy, recall per class
   - Metrics impulse : frequency, conditional PnL, cost

2. **Gates validation**
```python
# Régimes
passed, msg = production_gates(regime_metrics)
assert passed, f"Regime gates failed: {msg}"

# Impulse
from impulse_gates import validate_impulse_production
passed, report = validate_impulse_production(impulse_metrics)
assert passed, f"Impulse gates failed: {report['failures']}"
```

3. **Paper trading** (30 jours)
   - A/B test : impulse_mult ∈ {0.3, 0.5, 1.0}
   - Monitorer frequency, PnL, costs

---

## Compatibilité

### Breaking changes

⚠️ **Les modèles entraînés sur 3 classes sont INCOMPATIBLES**

Action requise :
- Re-entraîner tous les modèles de régimes
- Mettre à jour tous les pipelines consommant `regime_label`
- Mettre à jour logging / monitoring (supprimer `impulse` des dashboards)

### Code existant

Si vous avez du code utilisant `impulse` comme régime :

```python
# AVANT (incorrect)
if regime == "impulse":
    # ...

# APRÈS (correct)
if is_impulse:  # Event detector
    # ...
```

---

## FAQ

### Q: Pourquoi supprimer `impulse` comme régime ?

**R:** `impulse` n'est pas stationnaire. Les régimes doivent être stables sur plusieurs heures. `impulse` dure quelques minutes → c'est un événement, pas un état.

### Q: Les performances vont-elles s'améliorer ?

**R:** Oui. Régimes binaires :
- Accuracy attendue : 65-70% (vs 46%)
- Calibration : ECE <0.10 (vs >0.10)
- Meilleure stabilité (moins de confusion reversal/impulse)

### Q: Comment tester l'impulse detector ?

**R:** Métriques event-level (PAS de classification) :
```python
impulse_metrics = detector.get_event_metrics(total_days=30)
print(impulse_metrics['impulse_frequency_per_day'])  # Target: 1-10/day
print(impulse_metrics['avg_pnl_during_impulse'])     # Target: >0 or slightly negative
```

### Q: Impulse est-il obligatoire ?

**R:** Non. Si impulse_frequency <0.5/day, vous pouvez désactiver le module. Mais dans ce cas :
- Vérifier si threshold est trop élevé
- Ou accepter que les marchés sont calmes

---

## Support

Pour toute question :
1. Lire `pipeline_integration_example.py`
2. Vérifier les docstrings des modules
3. Consulter les tests unitaires (à venir)

## Checklist de migration

- [ ] Re-entraîner regime classifier (binaire)
- [ ] Valider accuracy >65%
- [ ] Intégrer impulse_detector dans pipeline
- [ ] Tester meta_control avec impulse downscale
- [ ] Tester execution_engine avec MAKER/TAKER switch
- [ ] Backtest complet (2019-2023)
- [ ] Valider gates (régimes + impulse)
- [ ] Paper trading 30j
- [ ] Production rollout
