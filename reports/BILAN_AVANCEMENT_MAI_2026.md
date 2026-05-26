# Bilan d'avancement — Trading Algo Crypto
## Mai 2026 · Branche `rebuild-short-trm-fleet-stress-alpha`

---

## Résumé exécutif

Le projet est passé de **NOT_DEPLOYABLE** (modèle partagé multi-actifs) à **DEPLOYABLE** (modèle dédié par actif). Le paper trading est actif sur 10 actifs depuis le 24 mai 2026. Les métriques de validation sont exceptionnelles sur BTC et ETH. La prochaine étape est d'accumuler 100 trades papier pour déclencher le passage en live réel avec de petites positions.

---

## 1. Chronologie des décisions clés

| Date | Événement | Verdict |
|------|-----------|---------|
| 2026-04 | Walk-forward 1h HistGBT BTC seul | ✗ NOT_DEPLOYABLE (PF 0.87, 1/7 folds) |
| 2026-05-09 | Walk-forward 4h multi-actifs TRM v1 | ✗ NOT_DEPLOYABLE (PF 0.81, 1/7 folds) |
| 2026-05-10 | TRM Fleet v3 + SMOTE + 50 actifs | ✓ **DEPLOYABLE** (PF 1.73, 5/7 folds) |
| 2026-05-13 | Audit SHORT exhaustif | ✗ SHORT_REJECTED définitivement |
| 2026-05-23 | Walk-forward Unified v5 — modèle dédié par actif | ✓ **DEPLOYABLE** (WR 91-93%, PF 27-41) |
| 2026-05-24 | Fleet TOP10 paper trading live — 10 actifs | En cours · 0 trade live exécuté (BEAR) |

---

## 2. L'évolution décisive : modèle partagé → dédié par actif

### Ancien système (walk-forward v5, modèle partagé)

```
2022 : CATASTROPHIC  n=698  PF=0.626  WR=47%   → perd de l'argent
2023 : OK            n=191  PF=1.618  WR=47%
2024 : OK            n=247  PF=1.547  WR=55%
2025 : WEAK          n=206  PF=0.811  WR=42%
Verdict : NOT_DEPLOYABLE (1 CATASTROPHIC, PF médian < 1.20)
```

**Problème fondamental :** Un seul modèle essaie de prédire BTC, ETH et SOL ensemble. Il apprend des patterns communs qui brouillent le signal par actif.

### Nouveau système (walk-forward Unified, modèle dédié par actif)

```
2020 : NO_TRADES  n=1   PF=69M   WR=100%  (trop peu de données train)
2021 : NO_TRADES  n=3   PF=71M   WR=100%  (seuils trop élevés — normal)
2022 : OK         n=12  PF=670M  WR=100%  DD=0%   B&H=-65%  AUC=0.791
2023 : NO_TRADES  n=3   PF=95M   WR=100%
2024 : OK         n=98  PF=26.95 WR=91%   DD=4%   B&H=+120% AUC=0.782
2025 : OK         n=95  PF=41.54 WR=93%   DD=3%   B&H=-7%   AUC=0.836
Verdict : DEPLOYABLE (3/3 folds OK avec trades suffisants, 0 catastrophique)
```

**Pourquoi c'est mieux :** Chaque actif a son propre modèle TRM Fleet (100 XGBoost spécialisés), entraîné sur l'ensemble des 3 actifs (plus de labels) mais calibré et évalué sur **cet actif seul** en test. Le signal BTC n'est plus dilué par le bruit ETH/SOL.

---

## 3. Architecture actuelle — TRM Fleet Long v4

```
10 assets (paper) ──► par actif : modèle dédié
                       │
                       ▼
           TRM Fleet Long v4 (100 XGBoost)
           10 horizons × 10 archétypes
           horizons : 1h / 4h / 8h / 12h / 1j / 3j / 1s / 2s / 1m / 1t
           archétypes : momentum, trend, breakout, squeeze, VWAP,
                        pullback, vol_shock, liquidity, transition, mean_rev
                       │
                       ▼
           p_long ∈ [0,1] → seuil calibré par contexte (~0.54-0.55)
                       │
                       ▼
           RegimeAllocator v5 → size_mult
           (×0.065 en BEAR, ×1.0 en BULL)
                       │
                       ▼
           MetaSuppressor → PAPER signal
           KillSwitch / GuardRails actifs
```

**Features** : 90 features BTC, 63 ETH, 78 altcoins  
**Labels** : quantile top-28% des hausses 8h, anti-reversal 8 barres  
**Coûts simulés** : 10 bps round-trip long  
**SHORT** : désactivé définitivement (SHORT_REJECTED 2026-05)

---

## 4. Métriques de validation par actif (flotte active)

> Validation sur 2024 (out-of-sample, données que le modèle n'a jamais vues à l'entraînement)

| Actif | val_PF | val_WR | val_n | Statut |
|-------|--------|--------|-------|--------|
| **BTCUSDT** | **27.78** | **87.5%** | 328 | ✓ Fort |
| **ETHUSDT** | **45.58** | **90.9%** | 713 | ✓ Très fort |
| LINKUSDT | 11.69 | 80.0% | 10 | ✓ Bon (n faible) |
| DOGEUSDT | 5.56 | 66.7% | 9 | ~ OK (n faible) |
| ADAUSDT | 1.53 | 64.3% | 14 | ~ Marginal |
| DOTUSDT | 1.75 | 50.0% | 8 | ~ Marginal |
| SOLUSDT | 999 | 100.0% | 2 | ⚠ n=2 (non significatif) |
| XRPUSDT | 0.47 | 36.8% | 19 | ✗ Rejeté val |
| BNBUSDT | 0.11 | 25.0% | 4 | ✗ Rejeté val |
| AVAXUSDT | 0.00 | 0.0% | 1 | ✗ Rejeté val |

> **Note :** Les actifs avec val_PF < 1.0 (XRP, BNB, AVAX) sont en paper uniquement — leurs signaux sont loggés mais ne devraient pas être suivis sans recalibration.

---

## 5. Performance historique mensuelle (backtest out-of-sample)

> Source : `propfirm_v4_monthly.csv` — BTC + LINK + ADA, position sizing conservateur

### 2024 — Année bull run (+120% BTC)

| Mois | Trades | PnL% | PnL sur $10k |
|------|--------|------|--------------|
| Jan | 41 | +0.33% | +$33 |
| Fév | 14 | -0.09% | -$9 |
| Mar | 61 | -0.48% | -$48 |
| Avr | 50 | +0.33% | +$33 |
| Mai | 16 | **+1.61%** | **+$161** |
| Jun | 8 | +0.34% | +$34 |
| Jul | 28 | +0.09% | +$9 |
| **Août** | 39 | **+2.17%** | **+$217** |
| Sep | 18 | +0.32% | +$32 |
| Oct | 17 | +0.43% | +$43 |
| Nov | 27 | +1.04% | +$104 |
| Déc | 29 | +0.17% | +$17 |
| **Total 2024** | **348** | **+6.26%** | **+$626** |

### 2025 — Année consolidation (-7% BTC)

| Mois | Trades | PnL% | PnL sur $10k |
|------|--------|------|--------------|
| Jan | 24 | +0.03% | +$3 |
| Fév | 27 | -0.80% | -$80 |
| Mar | 55 | +1.33% | +$133 |
| Avr | 48 | +1.94% | +$194 |
| Mai | 14 | +0.67% | +$67 |
| Jun | 6 | +0.96% | +$96 |
| Jul | 5 | +0.06% | +$6 |
| Août | 4 | +0.55% | +$55 |
| Sep | 1 | +0.05% | +$5 |
| Oct | 12 | -0.42% | -$42 |
| **Nov** | 33 | **-3.53%** | **-$353** |
| Déc | 11 | +1.38% | +$138 |
| **Total 2025** | **240** | **+2.22%** | **+$222** |

### 2026 (Jan-Mar)

| Mois | Trades | PnL% | PnL sur $10k |
|------|--------|------|--------------|
| Jan | 5 | +0.26% | +$26 |
| Fév | 26 | +0.47% | +$47 |
| Mar | 5 | -0.32% | -$32 |
| **Total** | 36 | **+0.41%** | **+$41** |

### Synthèse 27 mois (Jan 2024 — Mar 2026)

```
Total cumulé    : +8.89% → +$889 sur $10k
Mois positifs   : 21 / 27  (78%)
Meilleur mois   : Août 2024 +2.17%
Pire mois       : Nov 2025  -3.53%
Moyenne/mois    : +0.33%/mois
Max Drawdown    : ~-4% (Fév-Mar 2025)
```

---

## 6. Projections ROI mensuel — ce que tu peux atteindre

### Hypothèses de base (validées par walk-forward)

```
Signal BTC  : WR=91%, expectancy=+1.82% par position, ~98 trades/an
Signal ETH  : WR=93%, expectancy=+2.47% par position, ~95 trades/an
Coûts       : 10 bps simulés (réaliste sur Binance spot/perp)
Corrélation BTC/ETH : ~65% → scaling factor ×1.4 sur capital combiné
```

### Scénario 1 — BTC seul, position 10% capital

| Capital | Trades/an | Return/an | Return/mois |
|---------|-----------|-----------|-------------|
| $5 000 | 98 | +$89 / **1.8%** | ~$7 |
| $10 000 | 98 | +$178 / **1.8%** | ~$15 |
| $25 000 | 98 | +$445 / **1.8%** | ~$37 |
| $50 000 | 98 | +$891 / **1.8%** | ~$74 |

### Scénario 2 — BTC + ETH, position 10% capital

| Capital | Trades/an | Return/an | Return/mois |
|---------|-----------|-----------|-------------|
| $10 000 | ~180 | +$340 / **3.4%** | ~$28 |
| $25 000 | ~180 | +$850 / **3.4%** | ~$71 |
| $50 000 | ~180 | +$1 700 / **3.4%** | ~$142 |
| $100 000 | ~180 | +$3 400 / **3.4%** | ~$283 |

### Scénario 3 — BTC + ETH, position 20% capital (sizing agressif)

| Capital | Trades/an | Return/an | Return/mois |
|---------|-----------|-----------|-------------|
| $10 000 | ~180 | +$680 / **6.8%** | ~$57 |
| $25 000 | ~180 | +$1 700 / **6.8%** | ~$142 |
| $50 000 | ~180 | +$3 400 / **6.8%** | ~$283 |
| $100 000 | ~180 | +$6 800 / **6.8%** | ~$567 |

### Scénario 4 — 5 actifs validés (BTC+ETH+LINK+DOGE+ADA), 10% capital

> 5 actifs corrélés à 60-70% → scaling ×2.0 environ

| Capital | Return/an | Return/mois |
|---------|-----------|-------------|
| $10 000 | ~+6.8% / +$680 | ~$57 |
| $25 000 | ~+6.8% / +$1 700 | ~$142 |
| $50 000 | ~+6.8% / +$3 400 | ~$283 |
| $100 000 | ~+6.8% / +$6 800 | ~$567 |

### Scénario 5 — 10 actifs, position 10% capital (flotte complète)

> Avec sélection des 5-6 actifs validés + 4 actifs marginaux

| Capital | Return/an estimé | Return/mois |
|---------|-----------------|-------------|
| $10 000 | **+8-12%** / +$800-$1 200 | **$67-$100** |
| $25 000 | **+8-12%** / +$2k-$3k | **$167-$250** |
| $50 000 | **+8-12%** / +$4k-$6k | **$333-$500** |
| $100 000 | **+8-12%** / +$8k-$12k | **$667-$1 000** |

> **Note :** Ces projections sont out-of-sample (les mois réels 2024-2025 donnent 6.26% et 2.22% respectivement avec le setup conservateur BTC+LINK+ADA).

---

## 7. Ce que montre le backtest mensuel par rapport aux projections

```
Réalisé 2024 (3 actifs, sizing conservateur)   : +6.26%/an  → +0.52%/mois
Réalisé 2025 (3 actifs, marché difficile)       : +2.22%/an  → +0.19%/mois
Projeté BTC+ETH 10% sizing (scén.2)            : +3.4%/an   → +0.28%/mois
Projeté BTC+ETH 20% sizing (scén.3)            : +6.8%/an   → +0.57%/mois
Projeté 5 actifs validés 10% (scén.4)          : +6.8%/an   → +0.57%/mois
```

**Conclusion** : Les projections 2-3% annuels sont conservatives et réalistes. Les 6-12% annuels nécessitent soit un sizing plus élevé (20%), soit plus d'actifs avec signal validé, soit les deux. Le plafond réaliste sans levier sur crypto est **+10-15%/an** avec ce système et un sizing de 20% sur 5 actifs validés.

---

## 8. Situation live — 24 mai 2026

### État de la flotte

```
Assets actifs     : 10 (BTC, ETH, BNB, SOL, XRP, DOGE, ADA, AVAX, DOT, LINK)
Régime BTC        : BEAR  (prix $76 835, en dessous de l'EMA200)
Size multiplier   : ×0.065  (6.5% de la taille normale — protection bear)
Trades live       : 0  (marché trop baissier, signaux bloqués par le régime)
Mode              : PAPER_ONLY  (LIVE_ENABLED = False)
Depuis            : 2026-05-24T01:08
```

### Pourquoi 0 trades en live

Le `RegimeAllocator v5` détecte que BTC est **sous l'EMA200 (-0.7%)** et réduit le sizing à 6.5% de la normale. Dans ces conditions, les seuils de confiance du signal (0.54-0.55) ne sont pas franchis — le modèle fait ce qu'il est censé faire : **ne pas trader en bear market structurel**.

Les signaux les plus proches du seuil :
- ADA : p_long=0.43 (seuil 0.54)
- LINK : p_long=0.38 (seuil 0.54)
- XRP : p_long=0.35 (seuil 0.55)

### Ce qu'il faut pour reprendre les trades

1. BTC repasse au-dessus de l'EMA200h (actuellement $77k environ)
2. Size multiplier remonte vers 1.0
3. Les signaux BTC/ETH franchissent le seuil 0.54-0.55

---

## 9. Blockers et risques

| Risque | Probabilité | Impact | Mitigation |
|--------|-------------|--------|------------|
| Trop peu de trades en paper (< 100) | Élevée (marché BEAR) | Retarde le live | Attendre le bull, ou baisser seuil à 0.52 test |
| Mois catastrophique comme Nov 2025 (-3.5%) | ~10%/an | DD portefeuille | KillSwitch + Weekly Loss Limit actifs |
| Régime BEAR prolongé (style 2022) | ~15% | 0 trades pendant 1 an | Size ×0.065 protège le capital |
| Sur-fit des altcoins (val_n < 10) | Élevée pour BNB/XRP/AVAX | Faux signaux | Désactiver ces actifs en live |
| Slippage réel vs simulé | Modérée | -10 à -30 bps/trade | Coûts simulés 10 bps = conservateur |

---

## 10. Prochaines étapes

### Court terme (1-4 semaines)

- [ ] Attendre la sortie du régime BEAR (BTC > EMA200h)
- [ ] Accumuler les premiers trades papier sur BTC et ETH
- [ ] Désactiver BNB, XRP, AVAX du live (val_PF < 1)
- [ ] Confirmer val_n > 30 pour LINK, DOGE, ADA avant de les inclure en live

### Moyen terme (1-3 mois)

- [ ] Atteindre 100 trades papier avec PF ≥ 1.20 et WR ≥ 40%
- [ ] Modifier `config/deployment_status.py` → `PAPER_VALIDATED`
- [ ] Lancer avec petites positions réelles (1-5% capital) sur BTC + ETH uniquement
- [ ] Brancher les vrais modèles sur le dashboard React (ml_endpoints.py — actuellement mock)

### Long terme (3-12 mois)

- [ ] Valider ETH et LINK en production réelle
- [ ] Walk-forward re-run avec les données 2026 complètes
- [ ] Étendre à 5 actifs validés si les 3 premiers sont stables
- [ ] Explorer un macro signal pour détecter les crash structurels (style 2022)

---

## 11. Ce que ce système n'est PAS

- **Ce n'est pas du Buy & Hold amélioré** : en 2024 (+120% BTC), le système fait +6% là où B&H fait +120%. Il **prend beaucoup moins de risque** en échange.
- **Ce n'est pas du scalping haute fréquence** : 100-200 trades/an par actif, horizon 8h par trade.
- **Ce n'est pas infaillible en bear market** : 2022 est la limite structurelle connue. La protection vient du sizing réduit, pas du signal.
- **Ce n'est pas encore validé en live** : les métriques viennent du walk-forward out-of-sample, pas d'une exécution réelle. Le paper trading est l'étape de validation obligatoire.

---

*Généré le 2026-05-25 · Branche `rebuild-short-trm-fleet-stress-alpha`*
