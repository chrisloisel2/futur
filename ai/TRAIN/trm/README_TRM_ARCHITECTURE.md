# Tiny Recursive Model (TRM) pour Trading Algorithmique

## Philosophie: "Less is More"

Ce document présente l'architecture d'un modèle de trading basé sur le paradigme **Tiny Recursive Model** du papier de recherche "Less is More: Recursive Reasoning with Tiny Networks". L'objectif est de créer un modèle **minuscule, robuste, interprétable** qui généralise en conditions réelles, pas seulement en backtest.

---

## 1. BIAIS INDUCTIFS POUR LES MARCHÉS FINANCIERS

### 1.1 Pourquoi un Tiny Network?

Les marchés financiers ont des caractéristiques uniques:

1. **Non-stationnarité radicale**: Les régimes changent constamment (trending, ranging, volatility spikes)
2. **Signal-to-noise ratio faible**: La plupart des variations sont du bruit
3. **Risque de surapprentissage élevé**: Les patterns historiques ne se répètent jamais exactement
4. **Latence critique**: En production, chaque milliseconde compte

**Biais inductif justifié**: Un réseau volontairement sous-paramétré est **forcé** d'apprendre des représentations générales plutôt que de mémoriser des patterns spécifiques. Il capture l'essence des dynamiques de marché, pas les accidents historiques.

### 1.2 Pourquoi la récursion plutôt que l'attention globale?

**Problème avec Transformers**:
- Attention globale sur toute la séquence → sur-paramétrage massif
- Mémorise des patterns historiques qui ne se reproduisent pas
- Coût computationnel prohibitif en production

**Avantage du raisonnement récursif**:
- **Raffinement itératif**: Chaque itération affine la compréhension du marché
- **Poids partagés**: Force la généralisation (même processus à chaque étape)
- **Interprétabilité**: On peut observer comment la "compréhension" évolue
- **Latence minimale**: Réseau minuscule, inférence ultra-rapide

**Intuition cognitive**: Un trader expérimenté ne regarde pas tout l'historique à chaque fois. Il **itère mentalement** sur quelques observations clés jusqu'à converger vers une décision. Le TRM reproduit ce processus.

---

## 2. ARCHITECTURE TRM POUR LE TRADING

### 2.1 Structure Générale

```
Input (prix, volume, features)
   ↓
[Embedding Layer] (features → latent_dim)
   ↓
[État Initial] h_0 = f_init(embedded_input)
   ↓
┌──────────────────────────────┐
│  RECURSIVE REASONING BLOCK   │
│  (Itérations T fois)         │
│                              │
│  h_{t+1} = RNN(h_t, context) │
│                              │
│  Poids PARTAGÉS              │
└──────────────────────────────┘
   ↓
[Output Head]
   ↓
Signal de trading: {direction, confidence}
```

### 2.2 Dimensions du Modèle

**Choix volontairement minimalistes**:
- `latent_dim` = 32 (état latent)
- `hidden_dim` = 64 (couche cachée dans le bloc récursif)
- `num_iterations` = 5 (nombre d'itérations de raisonnement)
- **Total parameters: ~10K-50K** (vs millions pour un Transformer)

**Justification**:
- Marchés financiers = Low-dimensional manifold dans un espace bruité
- On ne prédit pas des images complexes, juste une direction et une magnitude
- Plus petit = plus robuste = meilleure généralisation

### 2.3 État Latent et Récursion

**État latent** `h_t` (dimension 32):
Représente la "compréhension actuelle" du marché à l'itération t:
- Tendance/momentum
- Volatilité locale
- Régime de marché (trending/ranging)
- Conviction (implicite)

**Mécanisme de mise à jour récursive**:
```python
h_{t+1} = GRU(h_t, context)
```

Où:
- `h_t`: État courant
- `context`: Embedding fixe des features d'entrée (prix, volume, etc.)
- GRU: Cellule récurrente avec poids **partagés** à travers toutes les itérations

**Rôle des itérations**:
1. **Itération 1-2**: Extraction de features primaires (momentum, volatilité)
2. **Itération 3-4**: Intégration multi-échelle (micro vs macro trends)
3. **Itération 5**: Convergence vers signal de trading robuste

**Pourquoi 5 itérations?**
- Trop peu (1-2): Sous-utilise la capacité récursive
- Trop (>10): Risque de sur-raisonnement, coût computationnel
- 5 itérations: Sweet spot empirique (Ablation study recommandée)

---

## 3. DONNÉES ET FEATURES

### 3.1 Données Sources

**Sources primaires** (S3 + Scrapers existants):
1. **OHLCV haute fréquence** (1 minute): S3 bucket `qbia/bourse/mintrad`
2. **Données alternatives** (Scrapers):
   - Sentiment social (Twitter, Reddit)
   - On-chain metrics (exchange flows, whale activity)
   - Macro-économique (Fed rate, VIX, BTC dominance)
   - Événements géopolitiques

### 3.2 Feature Engineering Minimal

**Principe**: Fournir des features brutes/simples, laisser le modèle apprendre

**Features obligatoires** (dimension: ~10-15):
1. **Retours logarithmiques** (multi-horizon):
   - `log_return_1m`, `log_return_5m`, `log_return_15m`, `log_return_1h`
2. **Volatilité locale** (rolling std):
   - `volatility_15m`, `volatility_1h`
3. **Volume normalisé**:
   - `volume_zscore` (rolling z-score 24h)
4. **Spread/Liquidity** (si disponible):
   - `bid_ask_spread`
5. **Signal embedding** (optionnel):
   - `sentiment_score` (si données alternatives disponibles)
   - `exchange_net_flow_normalized`

**Normalisation ROBUSTE**:
```python
# Rolling z-score (online, pas de fuite de données)
def rolling_zscore(series, window=1440):  # 24h pour 1-minute bars
    mean = series.rolling(window, min_periods=1).mean()
    std = series.rolling(window, min_periods=1).std()
    return (series - mean) / (std + 1e-8)
```

**Fenêtre temporelle**:
- `lookback_window` = 60 (1 heure de données minute-par-minute)
- Justification: Capture momentum court-terme sans sur-historiser

### 3.3 Gestion de la Non-Stationnarité

**Problème**: Les distributions changent constamment

**Solutions**:
1. **Normalisation online** (rolling statistics)
2. **Retours logarithmiques** (stationnaires par construction)
3. **Validation temporelle stricte** (pas de shuffle)
4. **Réentraînement périodique** (walk-forward)

---

## 4. OBJECTIF D'APPRENTISSAGE: LOSS ALIGNÉE TRADING

### 4.1 Pourquoi les losses classiques échouent

**MSE sur retours**: Ignore le coût du trading, la directionnalité
**Cross-entropy sur direction**: Pas de notion de magnitude
**Sharpe ratio direct**: Non-différentiable, bruyant

### 4.2 Trading-Aware Loss Function

**Composantes**:

```python
L_total = α * L_direction + β * L_magnitude + γ * L_trading_cost + δ * L_drawdown
```

**1. Directional Loss** (α = 1.0):
```python
# Pénalise les erreurs de direction
pred_direction = torch.sign(pred_return)
true_direction = torch.sign(true_return)
L_direction = -torch.mean(pred_direction * true_direction)
```

**2. Magnitude-Weighted Loss** (β = 0.5):
```python
# Pondère les erreurs par magnitude des vrais mouvements
# Les gros mouvements doivent être mieux prédits
weight = torch.abs(true_return)
L_magnitude = torch.mean(weight * (pred_return - true_return)**2)
```

**3. Trading Cost Penalty** (γ = 0.2):
```python
# Pénalise le sur-trading (changements fréquents de position)
position_changes = torch.abs(torch.diff(pred_direction))
L_trading_cost = torch.mean(position_changes) * trading_fee  # e.g., 0.001 (0.1%)
```

**4. Drawdown-Aware Regularization** (δ = 0.3):
```python
# Pénalise les séquences qui mènent à des drawdowns
cumulative_return = torch.cumsum(pred_return * true_direction, dim=0)
running_max = torch.cummax(cumulative_return, dim=0)[0]
drawdown = running_max - cumulative_return
L_drawdown = torch.mean(torch.relu(drawdown - max_acceptable_drawdown))
```

**Justification**:
- Cette loss optimise directement pour la performance de trading réelle
- Intègre les frictions du marché (coûts, drawdown)
- Différentiable et stable

---

## 5. RÉGIMES DE MARCHÉ IMPLICITES

### 5.1 Pas de Clustering Explicite

**Approche rejetée**:
- Clustering k-means sur features → Labels "Bull/Bear/Sideways"
- Problème: Rigide, non-adaptatif, nécessite re-clustering

**Approche TRM**:
Le modèle apprend **implicitement** les régimes via:
1. **État latent dynamique**: `h_t` capture le régime courant
2. **Adaptation récursive**: Les itérations permettent de basculer de régime
3. **Pas de labels hard**: Régimes sont des continuums

### 5.2 Comment le TRM Capture les Régimes

**Trending** (momentum fort):
- État latent évolue lentement, maintient la direction
- Confiance élevée

**Range-bound** (oscillations):
- État latent oscille, détecte les reversals
- Confiance modulée

**Chocs** (volatility spikes):
- État latent réagit rapidement
- Réduit la confiance (risk-off)

**Preuve empirique**: Visualiser `h_t` en t-SNE sur périodes historiques doit montrer des clusters naturels

---

## 6. ENTRAÎNEMENT ET VALIDATION

### 6.1 Splits Temporels Stricts

```
|-- Train (70%) --|-- Validation (15%) --|-- Test (15%) --|
2020-2023          2023-Q1/Q2              2023-Q3/Q4-2024
```

**Règles**:
- **AUCUN SHUFFLE**: Ordre chronologique strict
- Validation = Early stopping + hyperparameter tuning
- Test = Hold-out final, **jamais touché pendant le développement**

### 6.2 Walk-Forward Analysis

**Procédure**:
1. Entraîner sur fenêtre glissante de N mois
2. Valider sur mois suivant
3. Avancer d'un mois, réentraîner
4. Comparer performance constante vs dégradation

**Objectif**: Détecter si le modèle nécessite réentraînement fréquent (signe de non-robustesse)

### 6.3 Optimisation

**Optimizer**: AdamW avec weight decay (régularisation L2 implicite)
**Learning rate**: 1e-4 avec cosine annealing
**Batch size**: 128
**Gradient clipping**: Max norm = 1.0 (stabilité)
**Early stopping**: Patience = 20 epochs sur validation Sharpe

---

## 7. ÉVALUATION: AU-DELÀ DES MÉTRIQUES ML

### 7.1 Métriques de Trading Réelles

**1. PnL Cumulé** (avec frais):
```python
position = torch.sign(predictions)
returns = true_returns * position
pnl = torch.cumsum(returns - trading_fee * torch.abs(torch.diff(position)), dim=0)
```

**2. Sharpe Ratio**:
```python
sharpe = torch.mean(returns) / (torch.std(returns) + 1e-8) * sqrt(252 * 24 * 60)  # Annualisé
```

**3. Maximum Drawdown**:
```python
cumulative = torch.cumsum(returns, dim=0)
running_max = torch.cummax(cumulative, dim=0)[0]
drawdown = (running_max - cumulative) / (running_max + 1e-8)
max_drawdown = torch.max(drawdown)
```

**4. Turnover** (fréquence de trading):
```python
turnover = torch.sum(torch.abs(torch.diff(position))) / len(position)
```

**5. Win Rate & Profit Factor**:
```python
win_rate = torch.sum(returns > 0) / len(returns)
profit_factor = torch.sum(returns[returns > 0]) / torch.abs(torch.sum(returns[returns < 0]))
```

### 7.2 Pourquoi MSE/Accuracy sont insuffisants

- **MSE faible** ≠ PnL élevé (peut prédire magnitude mais mauvaise direction)
- **Accuracy 55%** peut être excellent si:
  - Gains moyens > Pertes moyennes (profit factor)
  - Faible turnover (coûts minimaux)
  - Pas de gros drawdowns

**Principe**: Optimiser pour Sharpe, surveiller drawdown, minimiser turnover

---

## 8. TESTS DE ROBUSTESSE

### 8.1 Test 1: Changement de Timeframe

**Procédure**:
1. Entraîner sur 1-minute bars
2. Tester sur 5-minute bars (agrégation)
3. Tester sur 15-minute bars

**Hypothèse de robustesse**: Performance doit dégrader gracieusement, pas s'effondrer

### 8.2 Test 2: Ajout de Bruit

**Procédure**:
```python
# Ajouter du bruit gaussien aux prix
noisy_prices = prices + np.random.normal(0, 0.001 * prices.std(), size=prices.shape)
```

**Hypothèse**: Modèle robuste est stable sous perturbations mineures

### 8.3 Test 3: Réduction Drastique des Données

**Procédure**:
- Entraîner sur 10% des données (sampling aléatoire temporel)
- Comparer performance vs 100% des données

**Hypothèse**: TRM minuscule doit maintenir performance (pas de data-hungry)

### 8.4 Test 4: Différents Actifs

**Procédure**:
- Entraîner sur BTC
- Tester sur ETH, BNB, SOL (zero-shot transfer)

**Hypothèse**: Patterns généraux doivent transférer (preuve de généralisation)

### 8.5 Test 5: Périodes de Crise

**Procédure**:
- Isoler périodes haute volatilité (Mars 2020, Mai 2021, Nov 2022)
- Mesurer max drawdown et recovery

**Hypothèse**: Modèle doit survivre (pas exploser), quitte à sous-performer temporairement

---

## 9. IMPLÉMENTATION: PRINCIPES DE CODE

### 9.1 Structure

```
trm/
├── data/
│   ├── features.py          # Feature engineering
│   ├── loader.py            # DataLoader avec split temporel
│   └── normalization.py     # Rolling z-score, online stats
├── model/
│   ├── trm.py               # TinyRecursiveModel
│   └── loss.py              # Trading-aware loss
├── training/
│   ├── trainer.py           # Training loop
│   └── scheduler.py         # Learning rate scheduling
├── evaluation/
│   ├── metrics.py           # PnL, Sharpe, Drawdown
│   └── backtest.py          # Walk-forward validation
├── robustness/
│   └── tests.py             # 5 tests de robustesse
└── config.yaml              # Hyperparamètres
```

### 9.2 Dépendances Minimales

- PyTorch (modèle + training)
- Pandas (data manipulation)
- Boto3 (S3 access)
- NumPy (calculs)

**Pas de**:
- TensorFlow
- Keras
- Frameworks lourds (Ray, Kubeflow)

### 9.3 Code Lisible

**Règles**:
- Fonctions < 50 lignes
- Docstrings clairs
- Type hints systématiques
- Pas de "magie" (pas de métaprogrammation obscure)

---

## 10. JUSTIFICATIONS SCIENTIFIQUES CLÉS

### 10.1 Pourquoi Récursif > Feedforward

**Paper**: "Neural Turing Machines" (Graves et al., 2014)
- Raisonnement multi-step améliore généralisation
- Poids partagés = meilleure induction

### 10.2 Pourquoi Tiny > Large

**Paper**: "Deep Double Descent" (Nakkiran et al., 2019)
- Régime sous-paramétré évite le surapprentissage
- Marchés = low signal-to-noise → petit modèle optimal

**Paper**: "Lottery Ticket Hypothesis" (Frankle & Carbin, 2018)
- Un sous-réseau minuscule suffit souvent
- Initialisation + architecture > taille

### 10.3 Pourquoi Retours Log > Prix

**Finance 101**:
- Prix sont non-stationnaires (random walk)
- Log-returns sont ~stationnaires, additifs, symétriques

### 10.4 Pourquoi Validation Temporelle

**Paper**: "A Few Useful Things to Know About Machine Learning" (Domingos, 2012)
- IID assumption invalide pour time series
- Test set doit être **futur** du train set

---

## 11. ROADMAP DE DÉVELOPPEMENT

**Phase 1: Data** (Semaine 1)
- [ ] Implémenter feature engineering robuste
- [ ] Créer DataLoader avec splits temporels
- [ ] Valider absence de data leakage

**Phase 2: Model** (Semaine 1-2)
- [ ] Implémenter TRM en PyTorch
- [ ] Vérifier convergence sur données synthétiques
- [ ] Ablation: nombre d'itérations

**Phase 3: Training** (Semaine 2)
- [ ] Implémenter trading-aware loss
- [ ] Training loop avec early stopping
- [ ] Tuning hyperparamètres sur validation

**Phase 4: Evaluation** (Semaine 3)
- [ ] Métriques de trading
- [ ] Walk-forward backtesting
- [ ] Comparaison vs baselines (buy-and-hold, moving average)

**Phase 5: Robustness** (Semaine 3-4)
- [ ] 5 tests de robustesse
- [ ] Rapport d'analyse
- [ ] Décision: production-ready ou itération?

---

## 12. CRITÈRES DE SUCCÈS

**Minimum Viable Performance**:
- Sharpe ratio > 1.0 sur test set (hors échantillon)
- Max drawdown < 20%
- Turnover raisonnable (< 50 trades/jour pour 1-minute bars)
- Robustesse: performance stable sur 3/5 tests

**Red Flags** (signes de sur-optimisation):
- Performance parfaite sur train, effondrement sur test
- Dépendance critique à un hyperparamètre
- Échec sur tous les tests de robustesse
- Turnover explosif (modèle "bruite")

**Production-Ready**:
- Latence < 10ms par inférence
- Stable sur 6+ mois de forward testing
- Survit à périodes de crise
- Code maintenable

---

## CONCLUSION

Ce TRM incarne le principe "Less is More":
- **Architecture minuscule** → Pas de surapprentissage
- **Raisonnement récursif** → Adaptabilité aux régimes
- **Loss alignée trading** → Optimise ce qui compte
- **Validation rigoureuse** → Pas de magie de backtest

L'objectif n'est pas d'impressionner avec de la complexité, mais de **généraliser en conditions réelles**. Un modèle simple qui fonctionne vaut mieux qu'un monstre qui échoue.

**Next step**: Implémenter le code.
