# INSTITUTIONAL_ENGINE — Architecture

## Vue d'ensemble

L'INSTITUTIONAL_ENGINE est un moteur algorithmique institutionnel construit **en parallèle** du TRM_EVENT_ENGINE existant. Les deux moteurs ne se modifient jamais mutuellement. Toute interaction passe par le contrat `SignalFrame`.

```
DATA LAYER
    ↓  (validation qualité obligatoire)
FEATURE STORE  (causal, versionné)
    ↓  (séparation stricte features/labels)
LABEL STORE    (triple barrier, forward returns)
    ↓  (split temporel, scaler fit sur train uniquement)
MODEL FACTORY  (baselines → tree → state-space)
    ↓  (walk-forward strict, embargo)
SIGNAL ENGINES (→ SignalFrame standardisé)
    ↓
PORTFOLIO ALLOCATOR (vol targeting, Kelly fractionnel)
    ↓
RISK ENGINE    (limites exposition, kill switch, state persistant)
    ↓
EXECUTION SIM  (frais, slippage, latence, participation rate)
    ↓
PAPER TRADING  (gates : 90j | 100 trades | PF>1.15 | DD<3%)
    ↓
LIVE READINESS
```

Le META_PORTFOLIO_ENGINE combine les deux :

```
TRM_EVENT_ENGINE (SignalFrame)  ──┐
                                   ├── META_PORTFOLIO_ENGINE
INSTITUTIONAL_ENGINE (SignalFrame) ┘   (sans double exposition)
```

## Contrats

Tous les contrats sont définis dans `src/institutional/contracts.py` :

| Contrat | Description |
|---------|-------------|
| `SignalFrame` | Interface engine → portfolio. Colonnes obligatoires. |
| `PortfolioState` | État courant du portefeuille (positions, exposition) |
| `RiskState` | État persistant du risk engine (drawdown, PnL, cooldown) |
| `DataQualityReport` | Rapport qualité par asset/source |
| `ExperimentRecord` | Tracking complet d'une expérience ML |
| `RobustnessScore` | Score de robustesse avec verdict REJECT→LIVE_READY |

## Garanties anti-overfit

1. **Causalité** : aucune feature n'utilise de données futures
2. **Walk-forward strict** : scaler/calibration fit sur train uniquement
3. **Embargo** : 7 jours entre train et test
4. **Threshold** : calibré sur val, jamais sur test
5. **Cost stress** : PF cost×2 et cost×3 obligatoires
6. **Shuffle test** : performance doit chuter significativement
7. **Year ablation** : aucune année ne doit dominer le résultat

## Structure des fichiers

```
src/institutional/
  contracts.py           ← Tous les contrats (interface)
  data/                  ← Loaders, validators, as-of join
  features/              ← Feature store causal et versionné
  labels/                ← Triple barrier, forward returns, label store
  models/                ← Baselines + LightGBM + state-space
  signals/               ← Signal engines (→ SignalFrame)
  portfolio/             ← Allocator, vol targeting, HRP
  risk/                  ← Risk engine + state persistant
  execution/             ← Simulateur exécution (frais, slippage)
  backtest/              ← Event backtester + walk-forward + métriques
  monitoring/            ← Paper trading + drift detection
  experiments/           ← Experiment tracking

scripts/
  institutional_build_features.py
  institutional_build_labels.py
  institutional_train_models.py
  institutional_walk_forward.py
  institutional_run_backtest.py
  institutional_portfolio_report.py
  institutional_paper_trade.py

configs/institutional/
  features.yaml  labels.yaml  models.yaml
  walk_forward.yaml  risk.yaml  portfolio.yaml  execution.yaml
```

## Signal Engines implémentés

| Engine | Actifs | Horizon | Méthode |
|--------|--------|---------|---------|
| Trend Following | BTC/ETH | 4h-3d | LightGBM DART + Kalman |
| Carry/Funding | BTC/ETH/alts | 8h-24h | Rules + LightGBM |
| Cross-Sectional Momentum | Univers 10 actifs | 1j-7j | Rank + LightGBM |
| Relative Value | Pairs corrélées | 4h-48h | Kalman hedge ratio |
| Volatility Breakout | BTC/ETH | 4h-24h | LightGBM + meta-label |

## Non-régression TRM

La TRM actuelle n'est PAS modifiée. Règle stricte :
- `TRM_EVENT_ENGINE` → engine_name = "TRM_EVENT_ENGINE"
- `INSTITUTIONAL_ENGINE` → engine_name = "INSTITUTIONAL_ENGINE"
- Aucun module `src/institutional/` n'importe depuis `ai/`
- Aucun module `ai/` n'importe depuis `src/institutional/`
- Interaction uniquement via `SignalFrame` dans `META_PORTFOLIO_ENGINE`
