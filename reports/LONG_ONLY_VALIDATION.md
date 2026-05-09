# LONG-only Validation
## Date : 2026-05-09
## Branche : long-only-scientific-validation

---

## Executive Decision

**NOT_DEPLOYABLE**

Le système LONG-only ne remplit pas les critères minimaux de déploiement.
Le LONG est rejeté sur l'ensemble de la période testée (2017-2026).

**LIVE = INTERDIT**
**PAPER = INTERDIT** (conditions non remplies)
**SHORT = INTERDIT** (rejeté définitivement)
**COMBINED = INTERDIT** (rejeté définitivement)

---

## Current Status

```
promising_but_insufficient_sample → REMPLACÉ PAR :

backtest_failed_validation
```

**Ce que montrent les vraies données :**

Le backtest sur la période complète (2017-2026) retourne :
- **n_trades = 2355**
- **PF = 0.840** (< 1.0 → perd de l'argent)
- **Expectancy = -0.32 USD/trade**
- **Total return = -7.56%** sur capital initial

Le backtest original reporté (26 trades, PF=5.3) était basé sur une méthodologie "1-bar hold" sur la seule période 2024-2026.
Ce chiffre est trompeur : il ne tient pas compte de la durée de position, des stops, ni du slippage réel.

**La validation scientifique réaliste avec stop/TP + frais + slippage + market impact retourne un résultat NÉGATIF.**

---

## Trade Count

| Méthode | Période | n_trades | Statut |
|---|---|---|---|
| 1-bar hold (train_pipeline) | 2024-2026 | 26 | Insuffisant + méthodologie simpliste |
| Stop/TP réaliste (ce rapport) | 2024-2026 | 246 | PF=0.988, rejeté |
| Stop/TP réaliste (ce rapport) | 2017-2026 | 2355 | PF=0.840, rejeté |

Minimum requis pour déploiement paper : **50 trades avec PF ≥ 1.20 et expectancy > 0**.

---

## Gate Rejection Report

Période : 2024-01-01 → 2026-05-08 | Paramètres : ft=0.51, dt=0.58, uw=0.30

| Gate | Count | % | Description |
|---|---|---|---|
| Barres totales | 20 601 | 100% | |
| Rejeté filtre tradeable | 18 442 | 89.5% | p_filter < 0.51 (Level 0) |
| Rejeté seuil direction | 1 547 | 7.5% | p_long < 0.58 (Level 2) |
| Rejeté uncertainty gate | 0 | 0.0% | width > 0.30 |
| Rejeté risk gate | 109 | 0.5% | cooldown / pertes consécutives / limite journalière |
| Rejeté régime | 0 | 0.0% | Level 1 régime |
| Setups acceptés | 612 | 3.0% | |
| Trades exécutés | 246 | 1.2% | |

**Observations :**
- Le filtre Level 0 rejette 89.5% des barres → très sélectif
- Parmi les setups acceptés (612), 246 sont exécutés → les 366 restants étaient en position ouverte
- L'uncertainty gate (RV-based) ne bloque aucune barre sur cette période (vol trop basse)
- Le risk gate bloque 109 setups (cooldown principalement)

---

## Threshold Sweep

Sweep sur la période 2024-2026 (246 barres de test pour la combinaison baseline).

| FT | DT | UW | Trades | PF | E/trade | DD% | Deploy |
|---|---|---|---|---|---|---|---|
| 0.51 | 0.52 | 0.30 | 338 | 1.038 | +0.073 | — | ✗ NON |
| 0.54 | 0.52 | 0.30 | 305 | 1.029 | +0.056 | — | ✗ NON |
| 0.51 | 0.55 | 0.30 | 292 | 0.996 | -0.007 | — | ✗ NON |
| 0.51 | 0.58 | 0.30 | 246 | 0.988 | -0.023 | — | ✗ NON |
| 0.48 | 0.52 | 0.30 | 376 | 0.945 | -0.111 | — | ✗ NON |

**Verdict du sweep :**
- Aucune combinaison n'atteint PF ≥ 1.20 avec expectancy > 0 sur la période 2024-2026
- La meilleure expectancy est +0.073 USD/trade (ft=0.51, dt=0.52) — statistiquement proche de zéro
- Abaisser dt augmente les trades mais réduit la qualité du signal
- **Aucune combinaison n'est déployable**

---

## Walk-forward Results

| Année | n_trades | PF | Expectancy | DD% | B&H | Fold OK? |
|---|---|---|---|---|---|---|
| 2020 | 242 | 0.860 | -0.29 | 1.0% | +303% | ✗ |
| 2021 | 566 | 0.831 | -0.35 | 2.2% | +59% | ✗ |
| 2022 | 258 | 0.815 | -0.39 | 1.1% | -65% | ✗ |
| 2023 | 32 | 1.726 | +1.08 | 0.1% | +156% | ✓ |
| 2024 | 125 | 1.061 | +0.12 | 0.3% | +120% | ✗ |
| 2025 | 83 | 0.938 | -0.13 | 0.4% | -7% | ✗ |
| 2026 | 38 | 0.875 | -0.26 | 0.3% | -9% | ✗ |

**Walk-forward pass : ✗ NON**

- 6/7 folds échouent (PF < 1.20 ou expectancy ≤ 0)
- 1 fold OK : 2023 (n=32 seulement, insuffisant statistiquement)
- Aucun fold catastrophique (DD < 12%) → le système perd peu mais de façon continue
- La stratégie perd systématiquement contre le buy-and-hold en 2020, 2021, 2023, 2024

---

## Baseline Comparison

| Stratégie | Trades | Retour% | DD% | Sharpe | Sortino | Ret/Exp |
|---|---|---|---|---|---|---|
| **Modèle LONG-only** | **2355** | **-7.56%** | **-8.68%** | **-10.09** | **-11.09** | **-0.39** |
| Buy & Hold BTC | 1 | +1754% | -83.9% | 0.61 | 0.81 | +17.5 |
| EMA 20/50 | 681 | +8.29% | -1.7% | -5.29 | -9.77 | +0.16 |
| RSI Oversold | 589 | -2.48% | -3.5% | -8.09 | -3.39 | -0.06 |
| Always Cash | 0 | 0% | 0% | — | — | — |
| Random same-freq | ~1542 | -5.67% | -6.1% | -12.59 | -31.99 | -0.16 |

**Verdicts :**
- ✗ Le modèle ne bat PAS le random à même fréquence
- ✗ Le modèle ne bat PAS always cash (perd 7.56% net)
- ✓ Meilleur drawdown que buy-and-hold (-8.7% vs -83.9%)
- ✗ EMA 20/50 (simple, sans ML) fait +8.29% contre -7.56% pour le modèle

**Conclusion : aucun edge démontré. Le modèle est statistiquement équivalent à du bruit.**

---

## Multi-Asset Results

| Actif | Trades | PF | E/trade | DD% | Deploy |
|---|---|---|---|---|---|
| BTCUSDT | 2355 | 0.840 | -0.321 | -8.68% | ✗ NON |
| ETHUSDT | 682 | 0.763 | -0.506 | -3.83% | ✗ NON |
| SOLUSDT | N/A | — | — | — | Données absentes |

**Interprétation :**
- Aucun actif ne remplit les critères
- ETH est encore plus mauvais que BTC (PF=0.763)
- Pas de généralisation possible
- L'edge observé sur la fenêtre courte 2023 (BTC) est probablement du hasard statistique

---

## Cost Assumptions

| Coût | Valeur | Description |
|---|---|---|
| Maker fee | 0.05% | Limit orders Binance Futures |
| Taker fee | 0.10% | Market orders / stops |
| Slippage | 0.02% | Demi-spread conservateur |
| Funding 8h | 0.01% | Taux moyen haussier |
| Market impact | k=0.15 × √(participation) × vol_bps | Square-root model |

Les frais expliquent une partie des pertes mais pas tout. Sur 246 trades (période test), les frais totaux sont modestes (~1-2% du capital). Le problème est le signal, pas les coûts.

---

## Uncertainty Gate Effect

Sur la période 2024-2026 (ft=0.51, dt=0.58, uw=0.30) :

| Résultat gate | Count | % des setups |
|---|---|---|
| Bloqués (uncertainty high) | 0 | 0.0% |
| Taille réduite (medium) | ~0 | ~0% |
| Autorisés (low) | 612 | 100% |

**L'uncertainty gate basé sur rv_24 est trop permissif sur cette période.**
La volatilité réalisée BTC en 2024-2026 est faible (< 0.05 / 24h), ce qui donne des width < 0.30 systématiquement.

Pour être utile, l'uncertainty gate devrait :
- Utiliser les vrais p10/p90 du modèle calibré (conformal prediction)
- Ou utiliser un seuil rv_24 > 0.03 qui filtre les périodes à basse volatilité

---

## Deployment Decision

| Mode | Statut | Raison |
|---|---|---|
| **LIVE** | ✗ INTERDIT | Signal LONG non validé |
| **PAPER** | ✗ INTERDIT | PF < 1.0 sur la majorité des années |
| **SHORT** | ✗ INTERDIT DÉFINITIVEMENT | PF < 0.5, expectancy négative |
| **COMBINED** | ✗ INTERDIT DÉFINITIVEMENT | SHORT entraîne le combined |

---

## Remaining Risks

1. **Signal LONG non validé** — Le modèle perd sur 6/7 années testées. L'edge observé en 2023 est un artefact statistique sur 32 trades.

2. **Overfitting du pipeline d'entraînement** — Le pipeline rapporte 26 trades PF=5.3 en "1-bar hold", mais ce résultat n'est pas reproductible en conditions réelles (stop/TP, frais, slippage).

3. **Features manquantes en production** — 9 des 53 features du modèle edge_long sont absentes du CSV de base (fear_greed, funding_rate, liq_imbalance, etc.). Ces features sont remplies à 0 lors des inférences sur ETH/SOL, ce qui biaise les prédictions.

4. **Régime-dépendance** — Le seul bon fold est 2023 (marché haussier fort). Le modèle semble capturer un biais haussier mais pas un edge directionnel robuste.

5. **Coût d'opportunité** — EMA 20/50 simple fait +8.29% sur la même période. Un modèle ML plus complexe devrait au moins égaler ce résultat.

6. **Données MongoDB** — Le script `backtest_long_only.py` nécessite `historical_ohlcv` dans MongoDB pour le flux complet. Les backtests ci-dessus utilisent le CSV alpha.

---

## Next Actions

**Priorité 1 — Comprendre l'edge 2023**
- Analyser les 32 trades de 2023 : profil de marché, features dominantes, sortie des modèles
- Vérifier si 2023 est reproductible sur 2026 (marché similaire?)

**Priorité 2 — Réévaluer la labellisation**
- Le label `future_ret_h` (retour 1h) est peut-être trop bruité
- Tester horizon 4h ou 8h pour un meilleur rapport signal/bruit

**Priorité 3 — Backtest 1-bar hold honnête**
- Implémenter le backtest "1-bar hold" avec frais et slippage réels
- Comparer avec le stop/TP pour comprendre d'où vient le PF=5.3 original

**Priorité 4 — Features enrichies**
- S'assurer que les 9 features manquantes (fear_greed, OI, funding, liq) sont disponibles dans le pipeline d'entraînement ET de backtest

**Priorité 5 — Réentraîner avec régime plus strict**
- Tester un filtre régime plus agressif : N'entraîner que sur les périodes NEUTRAL/LONGABLE
- Ne pas mélanger les labels bear market et bull market

**Ne pas faire :**
- Ne pas réduire les gates pour « trouver » de la performance
- Ne pas réactiver SHORT
- Ne pas déployer en paper trading avec les métriques actuelles
- Ne pas confondre le PF=5.3 (1-bar hold, 2024-2026) avec une vraie performance

---

## Résumé exécutif pour le dashboard

```json
{
  "deployment_status": "NOT_DEPLOYABLE",
  "live_enabled": false,
  "paper_enabled": false,
  "short_enabled": false,
  "combined_enabled": false,
  "long_only": {
    "status": "backtest_failed_validation",
    "deployable": false,
    "n_trades_realistic": 246,
    "profit_factor_realistic": 0.988,
    "expectancy_realistic": -0.023,
    "walk_forward_pass": false,
    "beats_random": false,
    "reason": "PF < 1.0 sur 6/7 années de walk-forward. Modèle statistiquement équivalent au bruit."
  }
}
```
