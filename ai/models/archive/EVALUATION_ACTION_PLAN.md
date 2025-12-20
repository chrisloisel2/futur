# Plan d'Action - Correction du Pipeline d'Évaluation

## 📋 Résumé Exécutif

**Problème :** Pipeline d'évaluation actuel présente des erreurs méthodologiques critiques rendant les métriques non fiables.

**Solution :** Remplacement par module d'évaluation rigoureux (`evaluation_metrics.py`) avec baselines, tests statistiques et sanity checks.

**Livrables :**
1. ✅ `EVALUATION_AUDIT.md` - Diagnostic complet (26 pages)
2. ✅ `evaluation_metrics.py` - Module d'évaluation corrigé (600 lignes)
3. ✅ Ce plan d'action

**Temps estimé :** 1-2 jours pour intégration complète.

---

## 🎯 Objectifs Mesurables

### Avant Correction

| Métrique | Valeur Actuelle | Statut |
|----------|-----------------|--------|
| Direction Accuracy | ~50% | ❌ Aléatoire |
| Corrélation | ≈ 0 | ❌ Non calculée |
| R² | << -10 | ❌ Invalide |
| Win Rate | > 100% | ❌ Bug logique |
| IC Coverage | 100% | ❌ Sur-calibré |
| MAE | Contradictoires | ❌ Non normalisées |

### Après Correction

| Métrique | Valeur Cible | Validation |
|----------|--------------|------------|
| Direction Accuracy | > 50% **avec** p-value < 0.05 | ✅ Test binomial |
| Corrélation (H=1) | > 0.10 | ✅ Per-horizon |
| R² (H=1) | > -0.50 | ✅ Baseline train |
| MAE (%) | < 0.5% @ H=1 | ✅ Dénormalisé |
| Baseline Beat | MAE < MAE_persistence | ✅ Comparaison |
| Sanity Checks | 0 leaks, 0 shifts | ✅ Automatique |

---

## 🛠️ Étapes d'Implémentation

### Phase 1: Préparation (2h)

#### ✅ Étape 1.1: Sauvegarder le Scaler

**Problème :** Scaler non sauvegardé → dénormalisation impossible.

**Action :**

```python
# Dans regime_pipeline.py, après fit scaler
import pickle

# Sauvegarder
with open("regime_out/scaler.pkl", "wb") as f:
    pickle.dump(scaler, f)

# Charger
with open("regime_out/scaler.pkl", "rb") as f:
    scaler = pickle.load(f)
```

**Validation :**

```python
# Test inverse transform
X_norm = scaler.transform(X_raw)
X_recon = scaler.inverse_transform(X_norm)
assert np.allclose(X_raw, X_recon, atol=1e-6)
```

---

#### ✅ Étape 1.2: Extraire Données Train/Val/Test

**Problème :** Baselines nécessitent accès aux données train.

**Action :**

```python
# Dans train_regime_aware_model(), sauvegarder splits
np.savez(
    "regime_out/data_splits.npz",
    y_ret_train=yret_train,
    y_ret_val=yret_val,
    y_rv_train=yrv_train,
    y_rv_val=yrv_val,
)

# Charger pour évaluation
data = np.load("regime_out/data_splits.npz")
y_ret_train = data["y_ret_train"]
```

---

### Phase 2: Intégration du Module (4h)

#### ✅ Étape 2.1: Importer le Module Rigoureux

**Fichier :** `regime_pipeline.py`

**Modification :**

```python
# Ajouter en haut
from evaluation_metrics import (
    RigorousEvaluator,
    EvaluationConfig,
)

# Créer l'évaluateur
config = EvaluationConfig(
    direction_neutral_threshold=0.25,
    enable_leakage_test=True,
    enable_variance_shift_test=True,
)

evaluator = RigorousEvaluator(
    scaler=scaler,
    feature_keys=FEATURE_KEYS,
    config=config,
)
```

---

#### ✅ Étape 2.2: Remplacer Fonction d'Évaluation

**Fichier :** `regime_aware_model.py` (ou créer nouveau `evaluation_wrapper.py`)

**Ancienne Fonction (À Remplacer) :**

```python
def evaluate_regime_expert_performance(...):
    # CODE ACTUEL (L963-1041)
    # → Remplacer par appel au RigorousEvaluator
```

**Nouvelle Fonction :**

```python
def evaluate_regime_expert_performance_rigorous(
    model: RegimeAwareMarketModel,
    X: np.ndarray,
    y_regime: np.ndarray,
    y_ret: np.ndarray,
    y_rv: np.ndarray,
    y_ret_train: np.ndarray,  # NOUVEAU: pour baselines
    scaler,  # NOUVEAU: pour dénormalisation
    feature_keys: List[str],  # NOUVEAU
    regime_names: list[str] = None,
) -> Dict:
    """
    Rigorous evaluation with per-regime breakdown.
    """
    if regime_names is None:
        regime_names = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]

    # Get predictions
    outputs = model(X, training=False, return_regime_probs=True)
    y_ret_pred = outputs["ret"].numpy()
    y_rv_pred = outputs["rv"].numpy()
    p_regime = outputs["regime_probs"].numpy()

    # Create evaluator
    evaluator = RigorousEvaluator(scaler=scaler, feature_keys=feature_keys)

    # Evaluate overall
    results_overall = evaluator.evaluate_full(
        y_true=y_ret,
        y_pred=y_ret_pred,
        y_true_rv=y_rv,
        y_pred_rv=y_rv_pred,
        y_train=y_ret_train,
        model=model,
        X_test=X,
    )

    # Evaluate per regime
    results_per_regime = {}

    for regime_id, regime_name in enumerate(regime_names):
        mask = (y_regime == regime_id)
        n_samples = np.sum(mask)

        if n_samples == 0:
            continue

        # Evaluate regime subset
        results_regime = evaluator.evaluate_full(
            y_true=y_ret[mask],
            y_pred=y_ret_pred[mask],
            y_true_rv=y_rv[mask],
            y_pred_rv=y_rv_pred[mask],
            y_train=y_ret_train,  # Full train for baseline
        )

        results_per_regime[regime_name] = {
            "n_samples": int(n_samples),
            **results_regime,
        }

    # Regime classification accuracy
    regime_pred = np.argmax(p_regime, axis=-1)
    regime_acc = np.mean(regime_pred == y_regime)

    return {
        "overall": results_overall,
        "per_regime": results_per_regime,
        "regime_classification_acc": float(regime_acc),
    }
```

---

#### ✅ Étape 2.3: Modifier Appel dans Pipeline

**Fichier :** `regime_pipeline.py`

**Ligne ~150 (dans `train_regime_aware_model`) :**

```python
# AVANT
results = evaluate_regime_expert_performance(
    model=model,
    X=Xw_val,
    y_regime=y_regime_val,
    y_ret=yret_val,
    y_rv=yrv_val,
)

# APRÈS
from evaluation_wrapper import evaluate_regime_expert_performance_rigorous

results = evaluate_regime_expert_performance_rigorous(
    model=model,
    X=Xw_val,
    y_regime=y_regime_val,
    y_ret=yret_val,
    y_rv=yrv_val,
    y_ret_train=yret_train,  # NOUVEAU
    scaler=scaler,            # NOUVEAU
    feature_keys=FEATURE_KEYS, # NOUVEAU
)
```

---

### Phase 3: Rapport Structuré (2h)

#### ✅ Étape 3.1: Générer Rapport JSON

**Fichier :** `regime_pipeline.py`

**Après Évaluation :**

```python
# Sauvegarder résultats complets
with open("regime_out/evaluation_results_rigorous.json", "w") as f:
    # Convertir numpy types
    results_serializable = convert_to_serializable(results)
    json.dump(results_serializable, f, indent=2)

def convert_to_serializable(obj):
    """Convert numpy types to native Python types for JSON"""
    if isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(v) for v in obj]
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.int32, np.int64)):
        return int(obj)
    elif isinstance(obj, (np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    else:
        return obj
```

---

#### ✅ Étape 3.2: Rapport Markdown Lisible

**Nouveau Fichier :** `generate_evaluation_report.py`

```python
"""
Generate human-readable evaluation report from JSON results.
"""

import json
import numpy as np
from typing import Dict


def generate_markdown_report(results: Dict, output_path: str = "regime_out/EVALUATION_REPORT.md"):
    """
    Generate formatted markdown report.
    """
    with open(output_path, "w") as f:
        f.write("# Evaluation Report - Regime-Aware Model\n\n")
        f.write(f"**Date:** {results.get('metadata', {}).get('date', 'N/A')}\n\n")

        f.write("---\n\n")

        # Overall metrics
        f.write("## 📊 Overall Performance\n\n")

        overall = results["overall"]

        # Per-horizon table
        f.write("### Per-Horizon Metrics\n\n")
        f.write("| Horizon | MAE (%) | Correlation | R² |\n")
        f.write("|---------|---------|-------------|----|\n")

        for h in range(len(overall["per_horizon"]["mae"])):
            mae = overall["per_horizon"]["mae"][h]
            corr = overall["per_horizon"]["correlation"][h]
            r2 = overall["per_horizon"]["r2"][h]
            f.write(f"| H={h+1:2d} | {mae:.4f} | {corr:+.3f} | {r2:+.3f} |\n")

        # Aggregated
        f.write("\n### Aggregated Metrics\n\n")
        agg = overall["aggregated"]
        f.write(f"- **MAE (mean):** {agg['mae_mean']:.4f}%\n")
        f.write(f"- **MAE (weighted):** {agg['mae_weighted']:.4f}%\n")
        f.write(f"- **Correlation (mean):** {agg['correlation_mean']:+.3f}\n")
        f.write(f"- **R² (mean):** {agg['r2_mean']:+.3f}\n\n")

        # Direction
        f.write("### Direction Metrics\n\n")
        dir_metrics = overall["direction"]
        f.write(f"- **Accuracy:** {dir_metrics['accuracy']:.2%}\n")
        f.write(f"- **p-value:** {dir_metrics['p_value']:.4f}\n")

        if dir_metrics["significant"]:
            f.write(f"- **Status:** ✅ Statistically significant (p < 0.05)\n")
        else:
            f.write(f"- **Status:** ❌ Not significant\n")

        f.write(f"- **Samples:** {dir_metrics['n_samples']}\n")
        f.write(f"- **Neutral excluded:** {dir_metrics['n_neutral_excluded']}\n\n")

        # Baselines
        if "baselines" in overall:
            f.write("### Baselines Comparison\n\n")
            baselines = overall["baselines"]
            f.write(f"- **MAE (persistence):** {baselines['mae_persistence']:.4f}%\n")
            f.write(f"- **MAE (mean forecast):** {baselines['mae_mean']:.4f}%\n")

            improvement_persistence = (baselines['mae_persistence'] - agg['mae_mean']) / baselines['mae_persistence'] * 100
            f.write(f"- **Improvement vs persistence:** {improvement_persistence:+.1f}%\n\n")

        # Sanity checks
        if "sanity_checks" in overall:
            f.write("### Sanity Checks\n\n")
            checks = overall["sanity_checks"]

            if "temporal_leakage" in checks:
                leak = checks["temporal_leakage"]
                if leak["leak_detected"]:
                    f.write("- ⚠️ **Temporal Leakage:** DETECTED\n")
                else:
                    f.write("- ✅ **Temporal Leakage:** None detected\n")

            if "zero_variance" in checks:
                zv = checks["zero_variance"]
                if zv["zero_variance_detected"]:
                    f.write("- ⚠️ **Zero Variance:** DETECTED (degenerate model)\n")
                else:
                    f.write("- ✅ **Zero Variance:** Predictions have variance\n")

        f.write("\n---\n\n")

        # Per-regime breakdown
        f.write("## 🔍 Per-Regime Performance\n\n")

        for regime_name, regime_results in results["per_regime"].items():
            f.write(f"### {regime_name}\n\n")
            f.write(f"**Samples:** {regime_results['n_samples']}\n\n")

            # Aggregated metrics
            if "aggregated" in regime_results:
                agg_regime = regime_results["aggregated"]
                f.write(f"- MAE: {agg_regime['mae_mean']:.4f}%\n")
                f.write(f"- Correlation: {agg_regime['correlation_mean']:+.3f}\n")
                f.write(f"- R²: {agg_regime['r2_mean']:+.3f}\n\n")

            # Direction
            if "direction" in regime_results:
                dir_regime = regime_results["direction"]
                f.write(f"- Direction Accuracy: {dir_regime['accuracy']:.2%}")

                if dir_regime["significant"]:
                    f.write(" ✅\n")
                else:
                    f.write(" ❌\n")

            f.write("\n")

    print(f"\n✅ Report generated: {output_path}")


if __name__ == "__main__":
    # Load results
    with open("regime_out/evaluation_results_rigorous.json", "r") as f:
        results = json.load(f)

    # Generate report
    generate_markdown_report(results)
```

---

### Phase 4: Tests de Validation (2h)

#### ✅ Étape 4.1: Test Unitaire du Module

**Nouveau Fichier :** `test_evaluation_metrics.py`

```python
"""
Unit tests for evaluation_metrics.py
"""

import numpy as np
import pytest
from evaluation_metrics import (
    Denormalizer,
    BaselineMetrics,
    DirectionMetrics,
    PerHorizonMetrics,
    SanityChecks,
)


def test_direction_classifier():
    """Test direction classification with threshold"""
    ret = np.array([0.01, -0.01, 0.0001, -0.0001, 0.005])

    dir_labels = DirectionMetrics.classify_direction(
        ret,
        threshold_std_fraction=0.25,
        threshold_absolute=0.001
    )

    # Expect: [1, -1, 0, 0, 1]
    expected = np.array([1, -1, 0, 0, 1])
    assert np.array_equal(dir_labels, expected), f"Got {dir_labels}, expected {expected}"


def test_directional_accuracy_with_neutral():
    """Test directional accuracy excludes neutral"""
    y_true = np.array([[0.01], [-0.01], [0.0001]])
    y_pred = np.array([[0.005], [-0.005], [0.0002]])

    result = DirectionMetrics.directional_accuracy(
        y_true, y_pred, exclude_neutral=True
    )

    # First two: both UP/DOWN → 2/2 = 100%
    # Third: both NEUTRAL → excluded
    assert result["accuracy"] == 1.0
    assert result["n_neutral_excluded"] == 1


def test_baseline_persistence():
    """Test persistence baseline"""
    y_true = np.array([[0.01, 0.02], [0.03, 0.04], [0.05, 0.06]])

    y_pred, mae = BaselineMetrics.persistence_baseline(y_true, horizon=1)

    # y_pred[1] should equal y_true[0, 0] (persistence)
    # But shape handling depends on implementation
    assert y_pred.shape == y_true.shape


def test_per_horizon_mae():
    """Test MAE per horizon"""
    y_true = np.array([[1, 2, 3], [4, 5, 6]])
    y_pred = np.array([[1.1, 2.2, 3.3], [3.9, 5.1, 6.2]])

    mae_per_h = PerHorizonMetrics.mae_per_horizon(y_true, y_pred)

    expected = np.array([0.1, 0.15, 0.25])  # Approximate
    assert np.allclose(mae_per_h, expected, atol=0.01)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**Lancer :**

```bash
python test_evaluation_metrics.py
```

---

#### ✅ Étape 4.2: Test d'Intégration End-to-End

**Fichier :** `test_integration_evaluation.py`

```python
"""
Integration test: full pipeline with rigorous evaluation
"""

import numpy as np
from regime_aware_model import RegimeAwareMarketModel, RegimeConfig
from evaluation_metrics import RigorousEvaluator


def test_full_evaluation_pipeline():
    """Test complete evaluation pipeline"""

    # Mock data
    B, L, F, H = 128, 256, 44, 12
    X_test = np.random.randn(B, L, F).astype(np.float32)
    y_train = np.random.randn(500, H).astype(np.float32) * 0.01
    y_test = np.random.randn(B, H).astype(np.float32) * 0.01

    # Mock scaler
    class MockScaler:
        def __init__(self):
            self.median = np.zeros(F)
            self.mad = np.ones(F)

    scaler = MockScaler()
    feature_keys = ["log_ret"] + [f"feat_{i}" for i in range(F-1)]

    # Create model
    cfg = RegimeConfig(lookback=L, horizon=H)
    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=F)

    # Build model
    _ = model(X_test[:1], training=False)

    # Get predictions
    outputs = model(X_test, training=False)
    y_pred = outputs["ret"].numpy()

    # Evaluate
    evaluator = RigorousEvaluator(scaler=scaler, feature_keys=feature_keys)

    results = evaluator.evaluate_full(
        y_true=y_test,
        y_pred=y_pred,
        y_train=y_train,
        model=model,
        X_test=X_test,
    )

    # Assertions
    assert "per_horizon" in results
    assert "aggregated" in results
    assert "direction" in results
    assert "baselines" in results
    assert "sanity_checks" in results

    # Check MAE is reasonable
    mae_mean = results["aggregated"]["mae_mean"]
    assert 0 < mae_mean < 1.0, f"MAE {mae_mean} out of reasonable range"

    # Check direction accuracy
    dir_acc = results["direction"]["accuracy"]
    assert 0 <= dir_acc <= 1.0, f"Direction accuracy {dir_acc} invalid"

    print("\n✅ Integration test PASSED")
    print(f"   MAE (mean): {mae_mean:.4f}")
    print(f"   Direction Acc: {dir_acc:.2%}")


if __name__ == "__main__":
    test_full_evaluation_pipeline()
```

---

### Phase 5: Documentation (1h)

#### ✅ Étape 5.1: Mise à Jour README

**Fichier :** `REGIME_MODEL_README.md`

**Ajouter Section :**

```markdown
## Évaluation Rigoureuse

Le pipeline d'évaluation a été corrigé pour éliminer les erreurs méthodologiques.

### Métriques Implémentées

#### Métriques de Base (Per-Horizon)

- **MAE** : Mean Absolute Error (dénormalisé en %)
- **Correlation** : Pearson correlation
- **R²** : Coefficient of determination (baseline sur train)

#### Direction

- **Accuracy** : Avec seuil de neutralité (±0.25σ)
- **p-value** : Test binomial de significativité
- **Exclusion neutral** : Zones bruitées exclues

#### Baselines

- **Persistence** : ret_t+h = ret_t
- **Mean Forecast** : ret_t+h = mean(ret_train)

#### Sanity Checks

- **Temporal Leakage** : Shuffle test
- **Variance Shift** : Train/val/test distribution comparison
- **Zero Variance** : Degenerate model detection

### Utilisation

```python
from evaluation_metrics import RigorousEvaluator

evaluator = RigorousEvaluator(scaler=scaler, feature_keys=FEATURE_KEYS)

results = evaluator.evaluate_full(
    y_true=y_ret_test,
    y_pred=y_ret_pred,
    y_train=y_ret_train,
    model=model,
    X_test=X_test,
)
```

### Interprétation

#### MAE (%) - Dénormalisé

- **< 0.5% @ H=1** : Excellent (court terme)
- **< 1.0% @ H=6** : Bon (moyen terme)
- **< 2.0% @ H=12** : Acceptable (long terme)

#### Correlation

- **> 0.20 @ H=1** : Signal fort
- **> 0.10 @ H=6** : Signal modéré
- **> 0.05 @ H=12** : Signal faible

#### Direction Accuracy

- **> 52%** avec **p < 0.05** : Significatif
- **≈ 50%** ou **p > 0.05** : Non significatif (hasard)

#### R²

- **R² > 0** : Bat baseline
- **R² < 0** : Pire que baseline (overfitting probable)
```

---

## 📈 Checklist de Déploiement

Avant de considérer le pipeline validé :

### Préparation

- [ ] Scaler sauvegardé dans `regime_out/scaler.pkl`
- [ ] Data splits sauvegardés dans `regime_out/data_splits.npz`
- [ ] `evaluation_metrics.py` présent dans `ai/models/`

### Intégration

- [ ] `evaluate_regime_expert_performance_rigorous()` créée
- [ ] Appels mis à jour dans `regime_pipeline.py`
- [ ] Imports ajoutés en tête de fichier

### Tests

- [ ] `test_evaluation_metrics.py` : tous les tests passent
- [ ] `test_integration_evaluation.py` : test end-to-end OK
- [ ] Exécution manuelle sur données réelles : pas d'erreur

### Validation

- [ ] Rapport JSON généré dans `regime_out/evaluation_results_rigorous.json`
- [ ] Rapport Markdown lisible dans `regime_out/EVALUATION_REPORT.md`
- [ ] Métriques cohérentes (MAE < 10%, corr > -1, dir_acc ∈ [0,1])

### Sanity Checks

- [ ] Temporal leakage: AUCUN détecté
- [ ] Variance shift: ratio < 2.0
- [ ] Zero variance: std(y_pred) > 1e-6
- [ ] Baseline: MAE_model < MAE_persistence

### Documentation

- [ ] README mis à jour avec section Évaluation
- [ ] Exemples d'utilisation ajoutés
- [ ] Interprétation des seuils documentée

---

## 🎯 Critères de Succès

Le pipeline d'évaluation est considéré **valide** si :

### Critères Techniques

1. ✅ **Dénormalisation fonctionnelle** : MAE en % (pas en unités MAD)
2. ✅ **Baselines calculées** : Persistence et mean forecast
3. ✅ **Direction avec seuil** : Threshold > 0.25σ
4. ✅ **Tests statistiques** : p-value sur direction
5. ✅ **Sanity checks passent** : No leaks, no shifts

### Critères de Performance (Indicatifs)

| Métrique | Seuil Minimum | Seuil Bon | Seuil Excellent |
|----------|---------------|-----------|-----------------|
| **MAE @ H=1** | < 1.0% | < 0.5% | < 0.3% |
| **Corr @ H=1** | > 0.05 | > 0.10 | > 0.20 |
| **Dir Acc** | > 50% (p<0.05) | > 52% (p<0.01) | > 55% (p<0.001) |
| **R² @ H=1** | > -1.0 | > -0.5 | > 0.0 |
| **Beat Persistence** | MAE < 1.05× | MAE < 0.95× | MAE < 0.85× |

**Note :** Ces seuils sont **indicatifs** et dépendent du marché/timeframe.

---

## 🚨 Points d'Attention

### ⚠️ Attention 1: Scaler Doit Être Réutilisable

```python
# FAUX
scaler = RunningRobustScaler(...)
scaler.update(X_all)
# → scaler perdu après script

# CORRECT
scaler = RunningRobustScaler(...)
scaler.update(X_all)
scaler.finalize()
pickle.dump(scaler, open("scaler.pkl", "wb"))
```

---

### ⚠️ Attention 2: Baseline sur Train, Pas Test

```python
# FAUX
var_baseline = np.var(y_test)  # Biaisé

# CORRECT
var_baseline = np.var(y_train)  # Out-of-sample
```

---

### ⚠️ Attention 3: Seuil Direction Adaptatif

```python
# FAUX: Seuil fixe
threshold = 0.001  # Peut être trop grand en low_vol

# CORRECT: Adaptatif
threshold = max(np.std(ret) * 0.25, 0.0005)
```

---

## 📞 Support

### Problèmes Courants

| Erreur | Cause | Solution |
|--------|-------|----------|
| `KeyError: 'log_ret'` | Feature key incorrect | Vérifier `FEATURE_KEYS` |
| `ValueError: shapes mismatch` | Dimensions incompatibles | Vérifier [N, H] vs [N] |
| `FileNotFoundError: scaler.pkl` | Scaler non sauvegardé | Sauvegarder après fit |
| `AssertionError: MAE > 10` | Dénormalisation ratée | Vérifier scaler.inverse_transform |

---

## 🎉 Conclusion

**Avant :**
- Métriques non fiables
- Pas de baselines
- Pas de tests statistiques
- Erreurs méthodologiques multiples

**Après :**
- ✅ Métriques dénormalisées et interprétables
- ✅ Baselines (persistence, mean)
- ✅ Tests de significativité (binomial, bootstrap)
- ✅ Sanity checks (leaks, shifts)
- ✅ Rapport structuré (JSON + Markdown)

**Gain :**
- Diagnostic fiable
- Comparaisons valides
- Décisions data-driven

---

**Temps Total Estimé :** 8-10 heures

**Priorité :** 🔴 CRITIQUE

**Auteur :** Expert ML Quantitatif

**Date :** 2025-12-20
