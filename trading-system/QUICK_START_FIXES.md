# 🚀 Quick Start - Tester les Corrections

## 1. Valider le Système

```bash
cd /Users/christopher/Desktop/futur/trading-system

# Vérifier que main.py fonctionne
python -m src.app.main validate --config configs/base.yaml

# Devrait afficher:
# ✓ Config valid
# ✓ Artifacts directory created
# ✓ MongoDB URI configured (ou warning si non configuré)
```

## 2. Tester un Mini-Backtest

```bash
# Lancer un backtest simple
python -m src.app.main backtest \
  --config configs/base.yaml \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --symbols BTCUSDT \
  --verbose

# Vérifie:
# - Exit prices ≠ entry prices ✓
# - Sharpe ratio calculé ✓
# - Costs réalistes (>10 bps) ✓
```

## 3. Vérifier les Métriques

```bash
# Afficher le rapport de backtest
cat artifacts/backtests/backtest_*/metrics.json

# Rechercher:
# {
#   "sharpe_ratio": 0.8,        # > 0 maintenant
#   "net_pnl": -50.0,           # Peut être négatif (normal avec vrais costs)
#   "total_costs": 150.0,       # ~15 bps par trade
#   "win_rate": 0.52,           # > 50%
#   "profit_factor": 1.1        # > 1.0
# }
```

## 4. Tester le Killswitch

```python
# Test unitaire
cd trading-system

python3 << 'EOF'
from pipeline.risk.controller import RiskController

config = {
    "controller": {},
    "killswitch": {
        "max_drawdown_pct": 10.0,
        "daily_loss_limit_pct": 2.0,
    }
}

rc = RiskController(config)

# Cas 1: Drawdown 5% → OK
portfolio = {"capital": 10_000, "drawdown": 0.05, "daily_loss": 0, "hourly_loss": 0, "consecutive_losses": 0}
assert rc._killswitch(portfolio) == False, "Should NOT trigger at 5% DD"

# Cas 2: Drawdown 15% → KILLSWITCH
portfolio = {"capital": 10_000, "drawdown": 0.15, "daily_loss": 0, "hourly_loss": 0, "consecutive_losses": 0}
assert rc._killswitch(portfolio) == True, "Should trigger at 15% DD"

# Cas 3: Daily loss 3% → KILLSWITCH
portfolio = {"capital": 10_000, "drawdown": 0.02, "daily_loss": 300, "hourly_loss": 0, "consecutive_losses": 0}
assert rc._killswitch(portfolio) == True, "Should trigger at 3% daily loss"

print("✅ Killswitch tests passed!")
EOF
```

## 5. Tester CVaR Corrigé

```python
python3 << 'EOF'
from pipeline.risk.var_cvar import VaREngine
import pandas as pd
import numpy as np

# Generate sample returns
returns = pd.Series(np.random.normal(-0.001, 0.02, 1000))

engine = VaREngine(method="parametric")
var, cvar = engine.compute(returns, alpha=0.95)

print(f"VaR (95%): {var:.4f}")
print(f"CVaR (95%): {cvar:.4f}")

# CVaR should be > VaR for normal distribution
assert cvar > var, f"CVaR ({cvar}) should be > VaR ({var})"
assert cvar > 0, "CVaR should be positive"

print("✅ CVaR calculation correct!")
EOF
```

## 6. Tester Score Composite

```python
python3 << 'EOF'
from pipeline.decision.logic import DecisionLogic
from domain.signal.signal import Signal, DecisionStatus, SignalDirection, TradeMode
import pandas as pd

logic = DecisionLogic()

# Signal avec bon composite score
signal = Signal(
    event_time=pd.Timestamp.now(),
    symbol="BTCUSDT",
    tradeable=True,
    mode=TradeMode.TAKER,
    direction=SignalDirection.LONG,
    decision_status=DecisionStatus.DELAY,
    coarse_direction=SignalDirection.LONG,
    regime_probs={"calm": 0.8, "chop": 0.2},
    regime_entropy=0.5,           # Low entropy
    quantiles={"q05": -0.01, "q50": 0.02, "q95": 0.05},
    p_hit=0.65,
    expected_shortfall=-0.005,
    rv_fwd={"mean": 0.015},
    confidence_raw=0.7,
    confidence_calibrated=0.7,    # High confidence
    novelty_score=1.0,            # Low novelty
    disagreement_score=0.3,       # Low disagreement
    quality_flags=0,
    reasons=[],
    run_id="test"
)

result = logic.apply(signal)
print(f"Decision: {result.decision_status}")
print(f"Reasons: {result.reasons}")

# Should CONFIRM
assert result.decision_status == DecisionStatus.CONFIRM, "Should confirm good signal"

print("✅ Composite score working!")
EOF
```

## 7. Vérifier les Fichiers Modifiés

```bash
# Fichiers critiques modifiés
git diff src/pipeline/risk/var_cvar.py | head -50
git diff src/pipeline/research/cost_model.py | head -50
git diff src/pipeline/execution/engine.py | head -50

# Nouvelles configs
cat configs/risk_updated.yaml
```

## 8. Prochaines Étapes

### Si tous les tests passent ✅:

1. **Entraîner les modèles** (optionnel, sklearn fallback existe):
   ```bash
   # TODO: Préparer données avec labels
   python scripts/train_models.py \
     --config configs/base.yaml \
     --state-path data/BTCUSDT_labeled.parquet \
     --run-id test_20241228
   ```

2. **Backtest complet** (1-3 mois de données):
   ```bash
   python scripts/run_backtest.py \
     --start 2024-01-01 \
     --end 2024-03-31 \
     --symbols BTCUSDT ETHUSDT
   ```

3. **Analyser Sharpe**:
   - Si Sharpe < 1.0 → Tuner seuils de décision
   - Si Sharpe > 1.5 → Passer au paper trading

4. **Paper trading** (capital virtuel):
   ```bash
   # TODO: Implémenter mode paper dans main.py
   python -m src.app.main live --mode paper --capital 10000
   ```

### Si des tests échouent ❌:

1. Vérifier les dépendances:
   ```bash
   pip install scipy scikit-learn pandas numpy pyarrow
   ```

2. Vérifier les imports:
   ```bash
   python -c "from pipeline.risk.var_cvar import VaREngine; print('OK')"
   ```

3. Logs détaillés:
   ```bash
   export PYTHONPATH=/Users/christopher/Desktop/futur/trading-system/src:$PYTHONPATH
   python -m src.app.main validate --verbose
   ```

---

## 📊 Résultats Attendus

### Avant Fixes:
- ❌ Main.py vide
- ❌ CVaR = VaR - sigma (faux)
- ❌ Backtest exit_px = entry_px (PnL = 0)
- ❌ Fees 0.5 bps (irréaliste)
- ❌ Killswitch max DD 100%
- ❌ Sharpe = NaN ou négatif

### Après Fixes:
- ✅ Main.py CLI complet
- ✅ CVaR correct (formule scipy)
- ✅ Backtest simulation réaliste
- ✅ Fees 10 bps taker (Binance VIP 0)
- ✅ Killswitch max DD 10%
- ✅ Sharpe > 0 (espéré 0.8-1.2)

---

## 🔗 Documentation

- Audit complet: Voir le rapport initial
- Fixes appliqués: [FIXES_APPLIED.md](FIXES_APPLIED.md)
- Config risk: [configs/risk_updated.yaml](configs/risk_updated.yaml)

**Questions?** Check les logs dans `artifacts/` ou relancer avec `--verbose`
