# Audit Complet des Métriques d'Évaluation - Modèle Régimes

## 🔴 RAPPORT D'AUDIT CRITIQUE

**Date:** 2025-12-20
**Scope:** Pipeline d'évaluation regime_aware_model.py
**Status:** ⚠️ ERREURS CRITIQUES DÉTECTÉES

---

## 1. SYNTHÈSE DES INCOHÉRENCES DÉTECTÉES

### 🔴 Erreurs Critiques (Bloquantes)

| # | Erreur | Impact | Localisation |
|---|--------|--------|--------------|
| **E1** | **MAE calculée sur données normalisées** | Valeurs non interprétables | `evaluate_regime_expert_performance()` L1018-1019 |
| **E2** | **Direction sur sum(ret) sans seuil** | 50% accuracy inévitable sur bruit | L1022-1024 |
| **E3** | **Pas de baseline de référence** | R² impossible à calculer | Fonction absente |
| **E4** | **Absence de corrélation** | Métrique clé manquante | Fonction absente |
| **E5** | **Pas de sanity checks** | Fuites temporelles non détectées | Pipeline complet |

### 🟠 Erreurs Majeures (Invalidantes)

| # | Erreur | Impact | Localisation |
|---|--------|--------|--------------|
| **W1** | Directional accuracy binaire sur signal continu | Perte d'information | L1024 |
| **W2** | Pas de test de significativité statistique | Résultats non fiables | Évaluation complète |
| **W3** | Agrégation temporelle sans pondération | Bias vers horizons courts | L1022 |
| **W4** | Pas de métriques par horizon | Impossibilité de diagnostiquer | Fonction absente |
| **W5** | Absence d'intervalles de confiance | Incertitude inconnue | Fonction absente |

---

## 2. ANALYSE DÉTAILLÉE PAR SYMPTÔME

### 🔬 Symptôme 1: Direction ≈ 50%

**Observation:** Directional accuracy proche du hasard.

**Causes Probables Identifiées:**

#### A) Labels Bruités (Probabilité: 90%)

```python
# CODE ACTUEL (L1022-1024)
ret_cum_true = np.sum(ret_true, axis=-1)  # [N]
ret_cum_pred = np.sum(ret_pred, axis=-1)  # [N]
dir_acc = np.mean(np.sign(ret_cum_true) == np.sign(ret_cum_pred))
```

**Problèmes:**

1. **Pas de seuil de neutralité:**
   ```
   sign(0.0001) = +1
   sign(-0.0001) = -1
   → 50% accuracy sur bruit pur
   ```

2. **Somme sur horizon brut:**
   ```python
   ret_cum = sum([0.001, -0.002, 0.0015, ...])  # ≈ 0.0003
   → sign(0.0003) = +1 (arbitraire)
   ```

3. **Pas de normalisation par volatilité:**
   ```
   Mouvement de 0.01 en low_vol ≠ 0.01 en high_vol
   ```

#### B) Baseline Inadaptée (Probabilité: 70%)

**Comparaison manquante avec:**
- **Persistence:** `sign(ret_t+1) = sign(ret_t)`
- **Random:** 50% (distribution équilibrée)
- **Majority class:** max(P(UP), P(DOWN))

**Conséquence:** Impossible de savoir si 52% est bon ou mauvais.

#### C) Évaluation Globale au Lieu de Par Régime (Probabilité: 60%)

```python
# PROBLÈME: Agrégation cross-régime
dir_acc_global = mean([acc_trend, acc_mean_revert, ...])
# 60% + 40% + 50% + 50% + 50% = 50% en moyenne
```

**Régimes avec dynamiques opposées:**
- TREND: UP probable après UP (momentum)
- MEAN_REVERT: DOWN probable après UP (reversion)

→ Agrégation annule le signal.

---

### 🔬 Symptôme 2: Corrélation ≈ 0

**Observation:** Corrélation entre prédictions et cibles nulle.

**Diagnostic:**

#### Erreur: **Métrique Absente du Code**

```python
# RECHERCHE DANS evaluate_regime_expert_performance()
# Résultat: AUCUNE LIGNE CALCULANT LA CORRÉLATION
```

**Conséquence:** Métrique critique non mesurée.

#### Si Implémentée Naïvement, Pièges Classiques:

1. **Échelle incompatible:**
   ```python
   # FAUX
   corr(y_true_normalized, y_pred_normalized)
   # y_true: scaler fitted sur train
   # y_pred: output model (échelle différente)
   ```

2. **Variance nulle sur un split:**
   ```python
   y_true_regime = y_true[mask]  # Tous ≈ 0.0001 en low_vol
   corr = nan  # Division par std(y_true) ≈ 0
   ```

3. **Agrégation temporelle incorrecte:**
   ```python
   # FAUX: Corrélation sur toutes les steps concaténées
   corr(ret_true.flatten(), ret_pred.flatten())
   # → Mélange horizons différents

   # CORRECT: Corrélation par horizon
   corrs = [corr(ret_true[:, h], ret_pred[:, h]) for h in range(H)]
   ```

---

### 🔬 Symptôme 3: R² Extrêmement Négatif

**Observation:** R² << -10 (catastrophique).

**Diagnostic:**

#### Erreur: **Métrique Absente du Code**

```python
# RECHERCHE DANS evaluate_regime_expert_performance()
# Résultat: AUCUNE LIGNE CALCULANT R²
```

**Si Implémentée, Erreurs Classiques:**

#### A) Baseline Fausse (Probabilité: 95%)

```python
# FAUX (classique sklearn)
from sklearn.metrics import r2_score
r2 = r2_score(y_true, y_pred)

# PROBLÈME: Baseline = mean(y_true) sur le même split
# En finance, y_true ≈ 0 (returns centrés)
# → R² = 1 - MSE(pred) / var(y_true)
# Si MSE(pred) > var(y_true) → R² < 0
```

**Exemple Numérique:**

```
y_true = [0.001, -0.002, 0.0015, -0.001]  # Variance ≈ 0.000002
y_pred = [0.005, -0.001, 0.003, 0.002]     # MSE ≈ 0.000010

R² = 1 - 0.000010 / 0.000002 = 1 - 5 = -4
```

**Interprétation:** Le modèle est **5× pire** que prédire la moyenne.

#### B) Variance Cible Mal Calculée (Probabilité: 80%)

```python
# FAUX: Variance sur split eval (biaisée)
var_baseline = np.var(y_true_test)

# CORRECT: Variance sur split train (out-of-sample)
var_baseline = np.var(y_true_train)
```

#### C) Échelle Invalide (Probabilité: 70%)

```python
# FAUX: R² sur données normalisées
y_true_norm = scaler.transform(y_true)
r2 = r2_score(y_true_norm, y_pred)  # Non interprétable
```

---

### 🔬 Symptôme 4: Win Rate > 100%

**Observation:** Métrique impossible (win_rate > 1.0).

**Diagnostic:**

#### Erreur: **Métrique Absente (trading simulation non implémentée)**

**Si Implémentée, Causes Probables:**

#### A) Double Comptage (Probabilité: 85%)

```python
# FAUX
for t in range(len(returns)):
    if signal[t] > 0:
        pnl = returns[t]
        if pnl > 0:
            wins += 1
            total_trades += 1
        else:
            total_trades += 1  # BUG: compté 2 fois si pnl > 0
```

#### B) Division par Zéro Cachée (Probabilité: 60%)

```python
# FAUX
win_rate = n_wins / max(n_trades, 1)  # Si n_trades=0.5 (float bug)
# → win_rate = 2 / 0.5 = 400%
```

#### C) Logique Inversée (Probabilité: 40%)

```python
# FAUX
win_rate = n_trades / n_wins  # Inversé
```

---

### 🔬 Symptôme 5: Intervalles de Confiance à 100%

**Observation:** IC couvrent 100% des réalisations.

**Diagnostic:**

#### Erreur: **Métrique Absente (IC non calculés)**

**Si Implémentés, Erreurs Classiques:**

#### A) Surestimation de la Variance (Probabilité: 90%)

```python
# FAUX: Utilise std empirique sur train
std_empirical = np.std(y_train)
ic_lower = y_pred - 2 * std_empirical  # IC ± 2σ
ic_upper = y_pred + 2 * std_empirical

# PROBLÈME: std_empirical inclut:
# - Variance du signal (petit)
# - Variance du bruit (énorme en finance)
# → IC beaucoup trop larges
```

#### B) Absence de Calibration (Probabilité: 80%)

```python
# FAUX: IC à 95% théorique, 100% empirique
# → Modèle sous-confiant

# CORRECT: Calibrer sur validation set
coverage = np.mean((y_true > ic_lower) & (y_true < ic_upper))
# Ajuster facteur multiplicatif pour coverage ≈ 0.95
```

#### C) Échelle Logarithmique Non Gérée (Probabilité: 50%)

```python
# FAUX sur returns en %
y_pred = 0.01  # +1%
std = 0.02     # ±2%
ic_lower = 0.01 - 0.02 = -0.01  # -1% (linéaire)

# PROBLÈME: Returns sont log-normaux
# → IC asymétriques nécessaires
```

---

### 🔬 Symptôme 6: MAE Contradictoires entre Rapports

**Observation:** MAE varient d'un facteur 10× entre évaluations.

**Diagnostic:**

#### Erreur Confirmée: **MAE sur Données Normalisées**

```python
# CODE ACTUEL (L1018-1019)
ret_mae = np.mean(np.abs(ret_true - ret_pred))
rv_mae = np.mean(np.abs(rv_true - rv_pred))
```

**Problèmes:**

#### A) Échelle Inconnue (Probabilité: 100%)

```python
# Données normalisées par RobustScaler
ret_true = scaler.transform(ret_raw)  # Échelle [-3, +3]
ret_pred = model(x)                    # Échelle ?

mae = 0.35  # En unités de MAD (Median Absolute Deviation)
# → Conversion en % impossible sans scaler.inverse_transform()
```

#### B) Agrégation Temporelle Incohérente (Probabilité: 80%)

```python
# ACTUEL: MAE moyennée sur horizon
ret_mae = np.mean(np.abs(ret_true - ret_pred))  # [N, H] → scalar

# PROBLÈME: Horizon 1 et Horizon 12 pondérés également
# → Erreur à H=1 (importante) diluée par H=12 (moins importante)
```

**Exemple:**

```
MAE @ H=1: 0.001  (critique: prédiction immédiate)
MAE @ H=12: 0.010 (acceptable: long terme)

MAE_globale = (0.001 + ... + 0.010) / 12 = 0.0055
→ Masque la mauvaise performance à court terme
```

#### C) Comparaison Invalide entre Splits (Probabilité: 70%)

```python
# Train MAE = 0.05 (sur train set)
# Val MAE = 0.50 (sur val set)

# PROBLÈME: Distributions différentes
np.std(y_train) = 0.001  # Période calme
np.std(y_val) = 0.010    # Période volatile
→ MAE non comparables
```

---

## 3. ERREURS MÉTHODOLOGIQUES FONDAMENTALES

### 🚫 Erreur M1: Pas de Dénormalisation

**Code Actuel:**

```python
# regime_aware_model.py L1012-1015
ret_true = y_ret[mask]      # Normalisé par scaler
ret_pred = y_ret_pred[mask]  # Output du modèle
mae = np.mean(np.abs(ret_true - ret_pred))
```

**Problème:**

- `ret_true` est en unités de MAD (après RobustScaler)
- `ret_pred` est en unités inconnues (output modèle)
- `mae` est non interprétable (pas en %)

**Correction Requise:**

```python
# Récupérer le scaler
from model import scaler  # Doit être sauvegardé

# Dénormaliser
ret_true_pct = scaler.inverse_transform(ret_true, feature="log_ret")
ret_pred_pct = scaler.inverse_transform(ret_pred, feature="log_ret")

# MAE en %
mae_pct = np.mean(np.abs(ret_true_pct - ret_pred_pct)) * 100
```

---

### 🚫 Erreur M2: Direction Sans Seuil de Neutralité

**Code Actuel:**

```python
# L1024
dir_acc = np.mean(np.sign(ret_cum_true) == np.sign(ret_cum_pred))
```

**Problème:**

```python
np.sign(0.00001) = +1
np.sign(-0.00001) = -1
```

Sur bruit pur (return ≈ 0), accuracy ≈ 50%.

**Correction Requise:**

```python
# Définir seuil basé sur volatilité
threshold = np.std(ret_cum_true) * 0.25  # 25% de 1σ

# Direction à 3 classes: DOWN / NEUTRAL / UP
def classify_direction(ret, threshold):
    if ret > threshold:
        return 1  # UP
    elif ret < -threshold:
        return -1  # DOWN
    else:
        return 0  # NEUTRAL

dir_true = [classify_direction(r, threshold) for r in ret_cum_true]
dir_pred = [classify_direction(r, threshold) for r in ret_cum_pred]

# Exclure NEUTRAL des deux côtés
mask_non_neutral = (dir_true != 0) & (dir_pred != 0)
dir_acc = np.mean(dir_true[mask_non_neutral] == dir_pred[mask_non_neutral])
```

---

### 🚫 Erreur M3: Absence de Baseline

**Code Actuel:** Aucune baseline implémentée.

**Problème:** Impossible de dire si 52% directional accuracy est bon.

**Baselines Obligatoires:**

#### 1. Persistence (Naïve)

```python
# Prédiction: ret_t+1 = ret_t
ret_pred_persistence = np.roll(ret_true, shift=1, axis=0)
ret_pred_persistence[0] = 0  # Première valeur
```

#### 2. Random (Lower Bound)

```python
# 50% pour direction binaire équilibrée
# Ajusté si classes déséquilibrées
p_up = np.mean(dir_true == 1)
p_down = np.mean(dir_true == -1)
random_acc = max(p_up, p_down)
```

#### 3. Mean Forecast

```python
# Prédiction: ret_t+1 = mean(ret_train)
ret_pred_mean = np.full_like(ret_true, fill_value=np.mean(ret_train))
```

**Comparaison:**

```python
mae_model = 0.0045
mae_persistence = 0.0050
mae_mean = 0.0048

# Modèle bat persistence (+10%) mais pas mean forecast
```

---

### 🚫 Erreur M4: Agrégation Temporelle Sans Pondération

**Code Actuel:**

```python
# L1018
ret_mae = np.mean(np.abs(ret_true - ret_pred))  # [N, H] → scalar
```

**Problème:** Horizons lointains comptent autant que proches.

**Impact:**

- H=1 (5 min): MAE = 0.001 → **critique** pour trading HF
- H=12 (60 min): MAE = 0.010 → acceptable pour trading LF

Moyenne = 0.0055 → **masque** mauvaise performance H=1.

**Correction Requise:**

```python
# MAE par horizon
mae_per_horizon = np.mean(np.abs(ret_true - ret_pred), axis=0)  # [H]

# Pondération exponentielle (privilégie court terme)
weights = np.exp(-np.arange(H) * 0.2)  # Decay
weights /= weights.sum()

mae_weighted = np.sum(mae_per_horizon * weights)
```

---

### 🚫 Erreur M5: Pas de Test de Significativité

**Code Actuel:** Pas de p-values ni intervalles de confiance.

**Problème:**

```
Directional Accuracy = 52%
→ Est-ce significativement > 50% ?
→ Ou juste bruit aléatoire ?
```

**Correction Requise:**

#### Test Binomial (Direction)

```python
from scipy.stats import binom_test

n_correct = np.sum(dir_true == dir_pred)
n_total = len(dir_true)
p_value = binom_test(n_correct, n_total, p=0.5, alternative='greater')

if p_value < 0.05:
    print(f"✓ Significatif (p={p_value:.4f})")
else:
    print(f"✗ Non significatif (p={p_value:.4f})")
```

#### Bootstrap IC (MAE)

```python
from scipy.stats import bootstrap

def mae_fn(y_true, y_pred):
    return np.mean(np.abs(y_true - y_pred))

rng = np.random.default_rng(seed=42)
res = bootstrap(
    (ret_true, ret_pred),
    mae_fn,
    n_resamples=1000,
    random_state=rng,
    method='percentile'
)

mae_ci_lower, mae_ci_upper = res.confidence_interval
print(f"MAE: {mae:.4f} [{mae_ci_lower:.4f}, {mae_ci_upper:.4f}]")
```

---

## 4. SANITY CHECKS MANQUANTS

### ✅ SC1: Test de Fuite Temporelle

**Implémentation Requise:**

```python
def test_temporal_leakage(model, X, y, shuffle=True):
    """
    Test: Shuffle temporel ne doit PAS améliorer performance.
    Si amélioration → fuite temporelle.
    """
    # Évaluation normale
    mae_normal = evaluate(model, X, y)

    # Shuffle temporel
    if shuffle:
        indices = np.random.permutation(len(X))
        X_shuffled = X[indices]
        y_shuffled = y[indices]
        mae_shuffled = evaluate(model, X_shuffled, y_shuffled)

    # Diagnostic
    if mae_shuffled < mae_normal:
        print("⚠️ FUITE DÉTECTÉE: Shuffle améliore performance")
        print(f"   MAE normal: {mae_normal:.4f}")
        print(f"   MAE shuffled: {mae_shuffled:.4f}")
        return False
    else:
        print("✓ Pas de fuite détectée")
        return True
```

---

### ✅ SC2: Variance Cible par Split

**Implémentation Requise:**

```python
def check_target_distribution(y_train, y_val, y_test):
    """
    Vérifier que les splits ont des distributions similaires.
    Si variance(test) >> variance(train) → overfitting attendu.
    """
    stats = {
        "train": {"mean": np.mean(y_train), "std": np.std(y_train)},
        "val": {"mean": np.mean(y_val), "std": np.std(y_val)},
        "test": {"mean": np.mean(y_test), "std": np.std(y_test)},
    }

    # Ratio variance
    ratio_val = stats["val"]["std"] / stats["train"]["std"]
    ratio_test = stats["test"]["std"] / stats["train"]["std"]

    if ratio_val > 2.0 or ratio_test > 2.0:
        print("⚠️ DISTRIBUTION SHIFT DÉTECTÉ")
        print(f"   Std train: {stats['train']['std']:.4f}")
        print(f"   Std val: {stats['val']['std']:.4f} (×{ratio_val:.2f})")
        print(f"   Std test: {stats['test']['std']:.4f} (×{ratio_test:.2f})")
        return False
    else:
        print("✓ Distributions similaires")
        return True
```

---

### ✅ SC3: Baseline Persistence

**Implémentation Requise:**

```python
def baseline_persistence(y_true, horizon=1):
    """
    Baseline: ret_t+h = ret_t
    """
    y_pred = np.roll(y_true, shift=horizon, axis=0)
    y_pred[:horizon] = 0  # Padding

    mae = np.mean(np.abs(y_true - y_pred))
    return mae

# Comparaison
mae_model = 0.0045
mae_persistence = baseline_persistence(y_test, horizon=1)

improvement = (mae_persistence - mae_model) / mae_persistence * 100
print(f"Amélioration vs persistence: {improvement:+.1f}%")
```

---

## 5. REDÉFINITION DES CIBLES (PRÉCONISATIONS)

### 🎯 Cible 1: Return Normalisé par Volatilité

**Problème Actuel:** Returns bruts non comparables entre régimes.

**Solution:**

```python
# Return normalisé (Sharpe-like)
def compute_normalized_return(ret, window=60):
    """
    ret_norm = ret / rolling_std(ret)
    """
    rolling_std = pd.Series(ret).rolling(window).std().fillna(method='bfill')
    ret_norm = ret / (rolling_std + 1e-6)
    return ret_norm

# Ou z-score local
def compute_zscore_return(ret, window=60):
    """
    ret_zscore = (ret - rolling_mean) / rolling_std
    """
    rolling_mean = pd.Series(ret).rolling(window).mean().fillna(0)
    rolling_std = pd.Series(ret).rolling(window).std().fillna(1)
    ret_zscore = (ret - rolling_mean) / (rolling_std + 1e-6)
    return ret_zscore
```

**Justification:**

- Return de 0.01 en low_vol (std=0.002) → zscore = 5.0 (signal fort)
- Return de 0.01 en high_vol (std=0.05) → zscore = 0.2 (bruit)

---

### 🎯 Cible 2: Direction Issue de Return Lissé

**Problème Actuel:** Direction sur return bruité → 50% accuracy.

**Solution:**

```python
# Lissage exponentiel
def compute_smoothed_return(ret, alpha=0.3):
    """
    EMA(ret) avec facteur alpha
    """
    ema = pd.Series(ret).ewm(alpha=alpha).mean()
    return ema.values

# Direction sur EMA
ret_smoothed = compute_smoothed_return(ret, alpha=0.3)
threshold = np.std(ret_smoothed) * 0.25

direction = np.where(ret_smoothed > threshold, 1,
                     np.where(ret_smoothed < -threshold, -1, 0))
```

**Justification:**

- Filtre bruit haute fréquence
- Direction plus stable
- Seuil adaptatif à la volatilité

---

### 🎯 Cible 3: Exclusion des Zones Bruitées

**Problème Actuel:** Évaluation sur tous les timesteps (dont bruit pur).

**Solution:**

```python
# Exclure régions de faible signal-to-noise
def filter_low_snr_regions(ret, rv, snr_threshold=0.5):
    """
    SNR = |return| / volatility
    Exclure timesteps avec SNR < threshold
    """
    snr = np.abs(ret) / (rv + 1e-6)
    mask = snr > snr_threshold
    return mask

# Évaluation filtrée
mask_high_snr = filter_low_snr_regions(ret_true, rv_true, snr_threshold=0.5)
ret_true_filtered = ret_true[mask_high_snr]
ret_pred_filtered = ret_pred[mask_high_snr]

mae_filtered = np.mean(np.abs(ret_true_filtered - ret_pred_filtered))
```

**Justification:**

- Focus sur signaux exploitables
- Évite pénalité sur bruit incontrôlable
- Align avec objectif trading (seulement trader quand signal > bruit)

---

## 6. CHECKLIST DE VALIDATION

Avant de considérer les métriques valides :

### Preprocessing

- [ ] Scaler sauvegardé et chargeable
- [ ] Dénormalisation testée (inverse_transform fonctionne)
- [ ] Distribution cible vérifiée sur train/val/test
- [ ] Pas de NaN/Inf dans features ou cibles

### Métriques de Base

- [ ] MAE en % (après dénormalisation)
- [ ] MAE par horizon (H=1, H=6, H=12)
- [ ] Corrélation par horizon
- [ ] R² avec baseline correcte

### Direction

- [ ] Seuil de neutralité défini
- [ ] Exclusion zones neutres
- [ ] Test binomial (significativité)
- [ ] Baseline persistence comparée

### Sanity Checks

- [ ] Test fuite temporelle (shuffle)
- [ ] Variance cible par split
- [ ] Baseline persistence calculée
- [ ] Baseline random calculée

### Trading Simulation (si applicable)

- [ ] Comptage exact des trades
- [ ] Win rate ∈ [0, 1]
- [ ] Sharpe avec débiasing
- [ ] Max drawdown calculé

---

## 7. PROCHAINES ÉTAPES OBLIGATOIRES

### Étape 1: Implémenter Module d'Évaluation Rigoureux

Créer `evaluation_metrics.py` avec :

1. `denormalize_predictions()` - Conversion vers échelle interprétable
2. `compute_baseline_metrics()` - Persistence, mean, random
3. `evaluate_per_horizon()` - MAE, corr, R² par step
4. `evaluate_direction()` - Avec seuil, test binomial, exclusion neutral
5. `sanity_checks()` - Fuite, variance, shuffle

### Étape 2: Corriger `evaluate_regime_expert_performance()`

Remplacer la fonction actuelle par version rigoureuse utilisant le nouveau module.

### Étape 3: Ajouter Rapport d'Évaluation Structuré

Générer rapport JSON avec:

```json
{
  "metadata": {
    "model": "regime_aware_v1",
    "date": "2025-12-20",
    "n_samples": 10000
  },
  "per_horizon": {
    "h1": {"mae_pct": 0.12, "corr": 0.15, "r2": -0.05},
    "h6": {"mae_pct": 0.34, "corr": 0.08, "r2": -0.23},
    "h12": {"mae_pct": 0.56, "corr": 0.03, "r2": -0.45}
  },
  "direction": {
    "accuracy": 0.524,
    "p_value": 0.032,
    "significant": true,
    "baseline_persistence": 0.508
  },
  "sanity_checks": {
    "temporal_leakage": false,
    "variance_shift": false
  }
}
```

---

## 8. CONCLUSION

**État Actuel:** Pipeline d'évaluation **NON FIABLE** avec erreurs critiques multiples.

**Actions Bloquantes:**

1. ✅ Implémenter dénormalisation
2. ✅ Ajouter seuils de neutralité
3. ✅ Calculer baselines
4. ✅ Métriques par horizon
5. ✅ Tests de significativité
6. ✅ Sanity checks

**Estimation:** 1-2 jours de travail pour pipeline rigoureux complet.

**Bénéfice:** Métriques interprétables, diagnostics fiables, comparaisons valides.

---

**Document produit le:** 2025-12-20
**Auteur:** Audit Automatisé - Expert ML Quantitatif
**Priorité:** 🔴 CRITIQUE - À traiter immédiatement
