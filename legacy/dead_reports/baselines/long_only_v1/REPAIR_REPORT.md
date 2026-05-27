# Repair Report
## Branche : repair-long-only-production-cleanup
## Date : 2026-05-09

---

## Summary

Le projet a subi un audit complet et 8 phases de nettoyage.
Objectif : supprimer l'illusion de performance, désactiver les branches non validées,
nettoyer les endpoints mockés, et rendre le système honnête et déployable.

**État pré-réparation :**
- SHORT : PF ≈ 0.40, expectancy négative, walk-forward cassé (`No module named 'models'`)
- LONG : PF = 5.3 sur 26 trades — Sharpe = 66.9 (irréaliste avec n < 50)
- COMBINED : rejeté (SHORT entraîne le combined dans le négatif)
- Endpoints : Level 3, Level 4, `/ai/model-metrics`, `/ai/decision-explanation`, `/pipeline/predictions/future` retournaient du `random.*` avec `status: OK`
- RiskController : implémenté et testé unitairement, mais persistance non branchée au niveau projet
- Signals (twitter, news) : `from models import` → `No module named 'models'` depuis la racine

**État post-réparation :**
- SHORT désactivé via flag central (`SHORT_ENABLED = False`)
- Endpoints mockés → `status: disabled, deployable: false`
- Imports signals → imports relatifs corrigés
- RiskController → état persisté dans `state/risk_state.json`
- Uncertainty gate → `risk/uncertainty_gate.py` créé
- Backtest LONG-only → `scripts/backtest_long_only.py` avec frais réels

---

## Disabled

- **SHORT branch (production/live/runtime.py, ai/_pipeline.py)**
  reason: PF < 1 across tested years, negative expectancy, statistically unstable
  flag: `config/strategy_flags.py::SHORT_ENABLED = False`

- **COMBINED branch**
  reason: SHORT component fails validation, contaminates combined pipeline
  flag: `config/strategy_flags.py::COMBINED_ENABLED = False`

- **Level 3 endpoint (ml_endpoints.py::generate_level3_data)**
  reason: EventClassifier / PairwiseComparator not trained — was returning random.choice
  new behavior: `{"status": "disabled", "deployable": false, "reason": "model_not_connected"}`

- **Level 4 endpoint (ml_endpoints.py::generate_level4_data)**
  reason: Meta-Decider PPO not trained — was returning random action probabilities, random trade history, random Sharpe
  new behavior: `{"status": "disabled", "deployable": false, "reason": "model_not_connected"}`

- **`/ml/flow/throughput` (ml_endpoints.py)**
  reason: was returning random CPU/GPU/memory/latency
  new behavior: returns null values with note "not yet instrumented"

- **`/pipeline/predictions/future/{symbol}` (api_server.py)**
  reason: was applying random drift `(random.random() - 0.5) * 0.004` to last known price
  new behavior: `{"status": "disabled", "reason": "model_not_connected"}`

- **`/ai/model-metrics` (api_server.py)**
  reason: was returning random accuracy/precision/recall/Sharpe with `success: True`
  new behavior: reads real backtest results from pipeline_summary.json, or returns disabled

- **`/ai/decision-explanation/{symbol}` (api_server.py)**
  reason: was generating random price_change to fake action/features/reasoning
  new behavior: returns live prediction data if available, or disabled

- **`/market/trades` (api_server.py)**
  reason: was generating fake trades with `random.random()` price within candle range
  new behavior: uses real OHLCV VWAP and volume, no random generation

---

## Archived

- old: `legacy/signals/` (twitter, news signal engines)
  new: `_archive_disabled/short_legacy/legacy_signals/`
  reason: duplicate of `signals/`, uses broken `from models import`, not imported anywhere active

---

## Fixed

- **signals/twitter/*.py** (14 files)
  change: `from models import X` → `from .models import X` (relative imports)
  files: enrichment.py, aggregation.py, pipeline.py, signals.py, filters.py, collector.py, sentiment.py
  + twitter_signal_engine/ (7 files)

- **signals/news/*.py** (14 files)
  change: `from models import X` → `from .models import X` (relative imports)
  files: aggregation.py, collector.py, signals.py, classification.py, filters.py, enrichment.py, pipeline.py
  + news_signal_engine/ (7 files)
  note: inline `from models import GeographicScope` (line 104) also fixed

- **ml_endpoints.py** — Level 0/1/2 fallbacks
  change: fallback de `random.uniform(0.3, 0.9)` → `0.0` si pas de prédiction réelle
  reason: Level 0 fallbackait sur random si le PredictionEngine n'avait pas encore tourné

---

## Created

- `config/strategy_flags.py` — flags centraux SHORT/COMBINED/LONG_ONLY
- `state/risk_state.json` — état initial du RiskController (paper trading LONG)
- `risk/__init__.py` — module risk
- `risk/uncertainty_gate.py` — filtre conformal simple (p10/p90 ou fallback RV)
- `scripts/backtest_long_only.py` — backtest LONG-only réaliste avec frais/slippage/market impact

---

## Validation Results

### Backtest LONG (dernier run : train_btcusdt_20260509_083513_845513)
```json
{
  "n_trades": 26,
  "profit_factor": 5.3153,
  "win_rate": 0.7692,
  "expectancy_per_trade": 5.50,
  "sharpe_annualized": 66.9,
  "max_drawdown": 0.12,
  "status": "promising_but_insufficient_sample",
  "deployable": false,
  "reason": "only 26 trades, minimum required 50"
}
```

**⚠ AVERTISSEMENT :** Le Sharpe de 66.9 est irréaliste avec 26 trades.
Ces métriques sont mathématiquement instables. Ne pas utiliser pour justifier un déploiement.
Minimum requis : 50 trades couvrant plusieurs régimes de marché.

### Compilation Python
```
python3 -m py_compile $(find . -name "*.py" -not -path "./_archive_disabled/*" ...)
→ 0 erreur de syntaxe
```

### Uncertainty gate
```
conformal_width(0.2, 0.7) = 0.5  → allow_trade: False (too wide)
width=0.15 → low_uncertainty, size_multiplier=1.0
width=0.25 → medium_uncertainty, size_multiplier=0.25
width=0.35 → too wide, size_multiplier=0.0
```

---

## Remaining Risks

1. **LONG insuffisant statistiquement** (26 trades)
   Action : laisser tourner sur une période plus longue ou baisser le seuil tradeable_quantile
   pour obtenir ≥ 50 trades.

2. **Sharpe irréaliste (66.9)**
   Le backtest interne `train_pipeline.py` ne comptabilise pas le slippage ni le market impact.
   `scripts/backtest_long_only.py` corrige cela, mais nécessite les colonnes `long_signal` dans les données.

3. **RiskController non branché dans le flux live**
   `ai/level_7/state.py::load_or_create_risk_controller` existe et fonctionne.
   Il doit être appelé explicitement dans `production/live/runtime.py` avant chaque décision.
   État persisté dans `state/risk_state.json`.

4. **Level 3 et Level 4 non entraînés**
   Ces niveaux retournent `disabled`. Le système reste fonctionnel sans eux
   (les niveaux 0-2 sont opérationnels).

5. **SHORT complètement désactivé**
   Le code SHORT reste dans le dépôt (non supprimé).
   Il peut être réactivé via `SHORT_ENABLED = True` dans `config/strategy_flags.py`
   uniquement si un nouveau backtest sur une période plus longue valide l'edge.

6. **Données MongoDB manquantes**
   `scripts/backtest_long_only.py` nécessite `historical_ohlcv` dans MongoDB.
   Alternative : passer `--run-dir` vers un dossier de run avec parquet exporté.

7. **`/ai/feature-importance` retourne des valeurs hardcodées**
   Ce n'est pas du random, mais ce n'est pas du SHAP réel non plus.
   Marqué comme acceptable (valeurs génériques, non prétendument dynamiques).

---

## Critère de succès global

| Critère | Status |
|---|---|
| SHORT désactivé | ✓ |
| COMBINED désactivé | ✓ |
| Aucun endpoint random prétendant être réel | ✓ |
| RiskController implémenté | ✓ |
| RiskState persisté | ✓ (state/risk_state.json) |
| Backtest LONG réaliste créé | ✓ |
| Filtre incertitude créé | ✓ |
| Projet compilable | ✓ |
| Dashboard honnête | ✓ (disabled vs deployable clair) |
| LONG déployable paper trading | ✗ (26 trades < 50 requis) |
