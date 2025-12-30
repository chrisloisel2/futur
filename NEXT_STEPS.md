# Prochaines Étapes Immédiates

Date : 2025-12-29

---

## ✅ Fait

- Architecture corrigée : régimes binaires {calm, reversal}
- Impulse réintroduit comme EVENT detector
- Gates production mis à jour
- Scripts shell adaptés (`train_regime.sh`, `train_all.sh`)
- Tests validés (5/5)
- Documentation complète

---

## 🚀 À Faire Maintenant

### 1) Adapter `scripts/train_regime_classifier.py` (PRIORITÉ)

**Fichier** : `trading-system/scripts/train_regime_classifier.py`

**Changements requis** :

```python
# 1. Ajouter flag --binary
parser.add_argument('--binary', action='store_true',
                    help='Use binary regime classification (calm vs reversal)')

# 2. Importer modules corrigés
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "ai/models/training/common"))

from regime_classifier_v2 import (
    train_calibrated_regime_classifier,
    evaluate_regime_classifier,
    production_gates,
)
from production_gates import RegimeClassifierGates

# 3. Filtrer labels si --binary
if args.binary:
    # Supprimer impulse (supposé label=1)
    mask = labels != 1
    features_df = features_df[mask]
    labels = labels[mask]
    labels = labels.replace(2, 1)  # Remapper reversal: 2→1
    class_names = ['calm', 'reversal']
else:
    class_names = ['calm', 'impulse', 'reversal']

# 4. Utiliser nouvelles fonctions
clf = train_calibrated_regime_classifier(
    X_train, y_train, class_names=class_names
)

metrics = evaluate_regime_classifier(
    clf, X_val, y_val, class_names=class_names
)

# 5. Valider avec nouvelles gates
gates = RegimeClassifierGates()
passed, reason = gates.validate(metrics)

if not passed:
    logger.error(f"Production gates failed: {reason}")
    # Save to failed/ directory
    sys.exit(1)
```

**Référence complète** : `trading-system/SCRIPT_UPDATES_REQUIRED.md`

---

### 2) Re-entraîner le Modèle (CRITIQUE)

```bash
cd trading-system

# Training binaire
./train_regime.sh

# Attendu :
# ✅ BINARY REGIME CLASSIFIER TRAINING COMPLETE
# Accuracy: >60% (vs 46% avant)
# Calm recall: >50%
# Reversal recall: >50%
# ECE: <0.10
```

**Si ça échoue** : Le script appelle `train_regime_classifier.py` avec `--binary`, donc il faut d'abord faire l'étape 1.

---

### 3) Générer Impulse Features (IMPORTANT)

Créer `scripts/add_impulse_features.py` :

```python
"""Add impulse event features to dataset."""
import sys
from pathlib import Path
import pandas as pd
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent / "ai/models/training/common"))
from impulse_detector import create_impulse_features_batch

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='Input parquet file')
    parser.add_argument('--output', required=True, help='Output parquet file')
    args = parser.parse_args()

    df = pd.read_parquet(args.input)
    df = create_impulse_features_batch(df)
    df.to_parquet(args.output)

    print(f"✅ Impulse features added: impulse_score, is_impulse")
    print(f"   Saved to: {args.output}")

if __name__ == '__main__':
    main()
```

**Utilisation** :
```bash
python scripts/add_impulse_features.py \
    --input data/processed/btcusdt_2019_2023.parquet \
    --output data/processed/btcusdt_2019_2023_impulse.parquet
```

---

### 4) Valider Impulse Gates (VALIDATION)

Créer `scripts/validate_impulse_gates.py` :

```python
"""Validate impulse event metrics."""
import sys
from pathlib import Path
import pandas as pd
import argparse

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
    df = df[(df['datetime'] >= args.start_date) &
            (df['datetime'] <= args.end_date)]

    detector = ImpulseDetector(threshold=0.7)
    total_days = (df['datetime'].max() - df['datetime'].min()).days

    # Compute metrics from is_impulse column (already computed)
    if 'is_impulse' in df.columns:
        detector.events = [
            {'timestamp': row['datetime'], 'score': row['impulse_score']}
            for _, row in df[df['is_impulse']].iterrows()
        ]

    metrics = detector.get_event_metrics(total_days=total_days)
    passed, report = validate_impulse_production(metrics)

    print(f"\nStatus: {'✅ PASSED' if passed else '❌ FAILED'}")
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

### 5) Backtest Complet (FINAL)

Après re-entraînement :

```bash
# Full backtest avec régimes binaires
python scripts/backtest_regime_binary.py \
    --start-date 2019-01-01 \
    --end-date 2023-12-31 \
    --model artifacts/models/regime/production_binary_v1.pkl

# Attendu :
# - Accuracy >65%
# - Calm/Reversal recall >60%
# - Sharpe ratio amélioration vs 3-class
```

---

## 📋 Checklist

### Phase 1 (Aujourd'hui)
- [ ] Modifier `scripts/train_regime_classifier.py` (ajouter --binary)
- [ ] Tester training : `./train_regime.sh`
- [ ] Valider accuracy >60%

### Phase 2 (Court terme)
- [ ] Créer `scripts/add_impulse_features.py`
- [ ] Créer `scripts/validate_impulse_gates.py`
- [ ] Générer impulse features sur données historiques
- [ ] Valider impulse gates

### Phase 3 (Moyen terme)
- [ ] Backtest complet 2019-2023
- [ ] Comparer performances vs 3-class
- [ ] Ajuster thresholds si nécessaire
- [ ] Paper trading 7-30 jours

---

## 🔍 Vérification Rapide

```bash
# Status actuel
bash CHECK_STATUS.sh

# Tests modules
cd ai/models/training/common
python3 test_integration.py  # 5/5 doivent passer

# Demo pipeline
python3 pipeline_integration_example.py
```

---

## 📖 Documentation

**Guide complet** : `ai/models/MIGRATION_GUIDE.md`
**Updates scripts** : `trading-system/SCRIPT_UPDATES_REQUIRED.md`
**Synthèse** : `FINAL_SUMMARY.md`

---

## 🎯 Objectifs

**Immédiat** :
- Training binaire fonctionnel
- Accuracy >60% (vs 46%)

**Court terme** :
- Impulse features intégrés
- Gates validés

**Moyen terme** :
- Production deployment
- Sharpe ratio amélioration mesurable

---

*Créé le 2025-12-29*
*Tout est prêt, il ne reste que l'adaptation des scripts Python*
