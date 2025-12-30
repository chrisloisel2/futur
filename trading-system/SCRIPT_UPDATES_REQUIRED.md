# Scripts Python Requis Mise à Jour

## Status : ⚠️ ACTION REQUISE

Les scripts shell ont été mis à jour pour régimes binaires, mais les scripts Python doivent être adaptés.

---

## 1. Scripts Shell Mis à Jour ✅

| Script | Status | Changements |
|--------|--------|-------------|
| `train_regime.sh` | ✅ Mis à jour | Messages binaires, gates accuracy>=0.60 |
| `train_all.sh` | ✅ Mis à jour | Pipeline v3.0, documentation binaire |

---

## 2. Scripts Python À Modifier ⚠️

### A) `scripts/train_regime_classifier.py`

**Changements requis** :

1. **Ajouter flag `--binary`**
```python
parser.add_argument(
    '--binary',
    action='store_true',
    help='Use binary regime classification (calm vs reversal)',
)
```

2. **Filtrer labels pour binaire**
```python
if args.binary:
    # Régimes binaires uniquement
    # Supposons label_policy encode: 0=calm, 1=impulse, 2=reversal
    # On garde 0 et 2, on supprime 1
    mask = labels != 1  # Remove impulse
    features_df = features_df[mask]
    labels = labels[mask]

    # Remapper: 0=calm, 2=reversal → 0=calm, 1=reversal
    labels = labels.replace(2, 1)

    class_names = ['calm', 'reversal']
    logger.info(f"Using BINARY regime classification: {class_names}")
else:
    class_names = ['calm', 'impulse', 'reversal']
```

3. **Utiliser nouvelles gates binaires**
```python
# Importer depuis le nouveau module
sys.path.insert(0, str(Path(__file__).parent.parent / "ai/models/training/common"))
from production_gates import RegimeClassifierGates

# Valider
gates = RegimeClassifierGates()
metrics_dict = {
    'accuracy': accuracy,
    'macro_f1': macro_f1,
    'brier': brier_score,
    'recall_per_class': recall_per_class,
    'ece': ece,
    'entropy': entropy,
}

passed, reason = gates.validate(metrics_dict)
if not passed:
    logger.error(f"Production gates failed: {reason}")
    # Save to failed/ directory
    sys.exit(1)
```

4. **Utiliser regime_classifier_v2.py**
```python
from regime_classifier_v2 import (
    train_calibrated_regime_classifier,
    evaluate_regime_classifier,
    production_gates,
)

# Au lieu de RegimeClassifierModel custom
clf = train_calibrated_regime_classifier(
    X_train, y_train,
    class_names=['calm', 'reversal'] if args.binary else ['calm', 'impulse', 'reversal']
)

metrics = evaluate_regime_classifier(
    clf, X_val, y_val,
    class_names=['calm', 'reversal'] if args.binary else ['calm', 'impulse', 'reversal']
)

passed, reason = production_gates(metrics)
```

---

### B) `pipeline/models/regime/classifier.py`

**Vérifier** si ce module utilise encore 3 classes :

```python
# Si ce fichier définit RegimeClassifierModel, vérifier:
# 1. Quels sont les class_names par défaut ?
# 2. Les métriques utilisent-elles impulse_recall ?
# 3. Les gates sont-elles cohérentes avec production_gates.py ?
```

**Action** :
- Si possible, migrer vers `regime_classifier_v2.py`
- Sinon, ajouter support binaire avec flag

---

### C) Scripts de génération de labels

**Fichier probable** : `scripts/generate_labels.py` ou équivalent

**Changements requis** :

```python
def generate_binary_regime_labels(df):
    """
    Generate BINARY regime labels (calm vs reversal).

    Impulse is NO LONGER a regime - use impulse_detector.py for events.
    """
    # CALM: low drift, moderate vol
    is_calm = (
        (abs(df['ret_60m']) < 0.002) &
        (df['rv_60'] < df['rv_60'].quantile(0.6))
    )

    # REVERSAL: momentum reversal
    ret_5m = df['ret_5m']
    ret_60m = df['ret_60m']
    is_reversal = (
        (np.sign(ret_5m) != np.sign(ret_60m)) &
        (abs(ret_5m) > 0.001)
    )

    # Binary encoding: 0=calm, 1=reversal, -1=ambiguous
    labels = np.where(is_calm, 0, np.where(is_reversal, 1, -1))

    # Drop ambiguous
    return labels[labels >= 0]
```

---

## 3. Nouveaux Scripts Requis 🆕

### A) `scripts/add_impulse_features.py`

```python
"""
Add impulse event features to existing dataset.

Usage:
    python scripts/add_impulse_features.py \
        --input data.parquet \
        --output data_with_impulse.parquet
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "ai/models/training/common"))

from impulse_detector import create_impulse_features_batch

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    df = pd.read_parquet(args.input)

    # Add impulse features
    df = create_impulse_features_batch(df)

    # Save
    df.to_parquet(args.output)

    print(f"Added columns: impulse_score, is_impulse")
    print(f"Saved to: {args.output}")

if __name__ == '__main__':
    main()
```

---

### B) `scripts/validate_impulse_gates.py`

```python
"""
Validate impulse event metrics against production gates.

Usage:
    python scripts/validate_impulse_gates.py \
        --data data_with_impulse.parquet \
        --start-date 2023-01-01 \
        --end-date 2023-12-31
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "ai/models/training/common"))

from impulse_detector import ImpulseDetector
from impulse_gates import validate_impulse_production

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--start-date', required=True)
    parser.add_argument('--end-date', required=True)
    args = parser.parse_args()

    df = pd.read_parquet(args.data)
    df = df[(df['datetime'] >= args.start_date) & (df['datetime'] <= args.end_date)]

    # Compute metrics
    detector = ImpulseDetector(threshold=0.7)
    total_days = (df['datetime'].max() - df['datetime'].min()).days

    # Process events
    for idx in range(len(df)):
        if idx < 60:  # Need history
            continue
        detector.detect(
            timestamp=df['datetime'].iloc[idx],
            ret_1m=df['ret_1m'].iloc[idx],
            rv_60=df['rv_60'].iloc[idx],
            volume=df['volume'].iloc[idx],
            volume_ma=df['volume_ma'].iloc[idx],
            volume_std=df['volume_std'].iloc[idx],
        )

    metrics = detector.get_event_metrics(total_days=total_days)

    # Validate
    passed, report = validate_impulse_production(metrics)

    print("="*80)
    print("IMPULSE PRODUCTION VALIDATION")
    print("="*80)
    print(f"Status: {'✅ PASSED' if passed else '❌ FAILED'}")
    print(f"\nMetrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    if not passed:
        print(f"\nFailures:")
        for f in report['failures']:
            print(f"  - {f}")
        sys.exit(1)

if __name__ == '__main__':
    main()
```

---

## 4. Checklist de Migration

### Immédiat (P0)
- [ ] Modifier `scripts/train_regime_classifier.py` (ajouter --binary)
- [ ] Vérifier `pipeline/models/regime/classifier.py`
- [ ] Créer `scripts/add_impulse_features.py`
- [ ] Créer `scripts/validate_impulse_gates.py`

### Court terme (P1)
- [ ] Tester training binaire : `./train_regime.sh`
- [ ] Valider metrics : accuracy >60%, calm/reversal recall >50%
- [ ] Générer impulse features sur données historiques
- [ ] Valider impulse gates

### Moyen terme (P2)
- [ ] Backtest complet avec régimes binaires + impulse events
- [ ] Comparer performances avant/après
- [ ] Ajuster thresholds si nécessaire

---

## 5. Commandes de Test

### Test scripts shell
```bash
# Vérifier messages
./train_regime.sh --help  # Should mention BINARY
./train_all.sh --help     # Should mention v3.0 BINARY
```

### Test scripts Python (une fois modifiés)
```bash
# Training binaire
python scripts/train_regime_classifier.py \
    --start-date 2023-01-01 \
    --end-date 2023-12-31 \
    --symbol BTCUSDT \
    --output test_binary.pkl \
    --binary

# Impulse features
python scripts/add_impulse_features.py \
    --input data.parquet \
    --output data_impulse.parquet

# Impulse gates
python scripts/validate_impulse_gates.py \
    --data data_impulse.parquet \
    --start-date 2023-01-01 \
    --end-date 2023-12-31
```

---

## 6. Références

**Modules Core** :
- `ai/models/training/common/regime_classifier_v2.py` - Binary classifier
- `ai/models/training/common/impulse_detector.py` - Event detector
- `ai/models/training/common/production_gates.py` - Binary gates
- `ai/models/training/common/impulse_gates.py` - Event gates

**Documentation** :
- `ai/models/MIGRATION_GUIDE.md`
- `ai/models/ACTION_PLAN_IMMEDIATE.md`
- `ai/models/ACTIONS_COMPLETED.md`

---

*Document créé le 2025-12-29*
*Scripts shell mis à jour, scripts Python en attente*
