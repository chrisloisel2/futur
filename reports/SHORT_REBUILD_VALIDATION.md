# SHORT Rebuild Validation
*Branch: rebuild-short-trm-fleet-stress-alpha*
*Date: 2026-05-10*
*Pipeline: TRMShortFleet v1 — Stress/Breakdown/Crowding*

---

## Executive Decision

> **SHORT_REJECTED**

Walk-forward 5 folds (2022-2026) sur AAVEUSDT 1h :
- Folds OK : 2/5 (minimum requis : 5)
- Folds catastrophiques : 1 (fold 2022, PF=0.61)
- PF médian : 0.95 (objectif : ≥1.30)
- Expectancy médiane : -0.04%/trade

**Ne pas utiliser ce SHORT en live. Ne pas activer le paper trading.**
Le signal existe (AUC 0.60-0.68) mais n'est pas suffisamment stable entre années.

---

## Why old SHORT failed

D'après l'audit `scripts/audit_short_failures.py` → `reports/short_rebuild/short_failure_audit.json`:

| Catégorie d'échec | Description |
|---|---|
| **squeeze_loss** | Short pris sur momentum baissier, retournement brutal (short squeeze) |
| **late_short_after_flush** | Entrée après une baisse déjà consommée — le mouvement est terminé |
| **bull_trend_short** | Short contre une tendance haussière saine → losses structurels |
| **no_breakdown_followthrough** | Cassure technique sans volume confirmateur → faux signal |
| **cost_drag** | Trades trop fréquents, coûts absorbent le PF marginal |
| **bad_regime** | Shorts en régime bull_fresh/bull_mature sans exception crowding |
| **random_noise** | Signal trop bruité, aucune edge identifiable |

**Régimes à bloquer définitivement** (gate NO_SHORT = True):
- Bull trend sain: Close > EMA200 AND EMA50 > EMA200 AND momentum_72h > 0 AND RSI > 50
- Sans exception crowding/failed_breakout/liquidity_stress

**Régimes où le SHORT a une chance**:
- Long crowding extrême (funding z-score > 2.0 + L/S ratio élevé)
- Failed breakout confirmé (upper wick élevé + volume épuisé sur le high)
- Breakdown réel (perte VWAP + perte EMA20/50 + volume vendeur)
- Bear continuation établi (EMA stack bearish + momentum_72h négatif)

---

## New SHORT hypothesis

Le SHORT rentable en crypto ne prédit pas "ça baisse en général".
Il prédit des événements spécifiques de stress de marché:

1. **Crowded longs**: La foule est massivement longée (funding élevé, L/S élevé, OI expansion). Le retournement sera amplifié par les liquidations.

2. **Failed breakout / Bull trap**: Faux breakout sur un high avec épuisement du volume taker acheteur. Le prix rejette et revient violemment sous le niveau de cassure.

3. **Breakdown réel**: Perte de VWAP + EMA20 + local low sur volume — pas un simple pullback mais une structure baissière confirmée.

4. **Liquidity stress**: Spike de liquidations longs + expansion de range + pression vendeuse taker → cascade de ventes.

5. **Hedge de portefeuille**: Le SHORT réduit le drawdown LONG en périodes de stress — valeur de diversification même si PnL isolé modeste.

---

## Labels

*Voir `reports/short_rebuild/short_label_audit.json` pour les chiffres complets.*

| Métrique | Valeur |
|---|---|
| Positive rate (y_short=1) | *À compléter* |
| Negative rate (y_short=0) | *À compléter* |
| Gray rate (y_short=-1) | *À compléter* |
| Median return | *À compléter* |
| Mean MFE | *À compléter* |
| Mean MAE | *À compléter* |
| Squeeze reject rate | *À compléter* |
| Late short reject rate | *À compléter* |
| Threshold used | *calculé sur train uniquement* |
| Breakeven win rate | *À compléter* |

**Règle clé**: Le seuil SHORT est calculé uniquement sur TRAIN. Jamais sur test. Jamais sur tout le dataset.

**Labels asymétriques** (vs symétrique naïf):
- y_short=1 requiert: retour suffisant + MFE > coût + MAE < squeeze_limit + pas d'entrée tardive
- y_short=-1 (gris) exclu du training pour éviter d'apprendre sur les cas ambigus
- Cette asymétrie génère moins de positifs mais plus propres → moins de faux positifs coûteux

---

## Features

*Voir `ai/level_0/short_features.py` et `FEATURES_SHORT_GAMECHANGER` dans `ai/level_0/features.py`.*

**Features gamechanger par contexte**:

| Contexte | Features clés |
|---|---|
| Crowded longs | `long_crowding_score`, `funding_extreme_positive`, `funding_accel_24`, `long_short_extreme` |
| Breakdown | `breakdown_score`, `below_vwap_4h`, `local_low_break_24`, `ema_stack_bearish` |
| Failed breakout | `failed_breakout_score`, `upper_wick_z_24`, `taker_buy_exhaustion`, `volume_exhaustion_high` |
| Liquidity stress | `taker_sell_pressure`, `sell_volume_shock`, `range_expansion_6`, `liq_long_spike_12` |
| Squeeze risk | `squeeze_risk_score`, `positive_momentum_accel`, `funding_negative_squeeze` |

**Disponibilité live**: toutes les features sont calculables en temps réel sans lookahead.

---

## TRMShortFleet architecture

```
TRMShortFleet v1
 ├─ crowded_longs     → funding extrême + L/S ratio + OI expansion
 ├─ breakdown         → perte VWAP/EMA + local low break + volume vendeur
 ├─ failed_breakout   → upper wick + exhaustion taker + rejection high
 ├─ liquidity_stress  → spike liq + range expansion + sell shock
 ├─ bear_continuation → EMA stack bearish + momentum négatif + weak bounce
 ├─ macro_riskoff     → fear + funding négatif + OI en baisse
 └─ general_short     → fallback (poids réduit)
```

**Routing**:
- p_final = 0.70 × p_specialist + 0.30 × p_general
- Contexte incertain (2 actifs): p = 0.50 × p_top1 + 0.25 × p_top2 + 0.25 × p_general

**Hard example training** (2 rounds):
- Round 1: entraînement normal par contexte
- Round 2: sample_weight ×3 sur hard negatives (y=0, p>0.55) et missed positives (y=1, p<0.45)
- Round 3 optionnel: uniquement si val AUC améliore de +0.005

---

## Walk-forward results

*Voir `reports/short_rebuild/walk_forward_short_results.json` pour les détails complets.*

| Fold | n_trades | PF | Expectancy | WR | Max DD | Squeeze Rate | Status |
|---|---|---|---|---|---|---|---|
| 2020 | — | — | — | — | — | — | SKIP (données <2018) |
| 2021 | — | — | — | — | — | — | SKIP (données <2019) |
| 2022 | 172 | 0.61 | -0.415% | 42% | 0.1% | 2% | **CATASTROPHIC** |
| 2023 | 341 | 1.38 | +0.189% | 54% | 0.0% | 1% | **OK** |
| 2024 | 51 | 1.79 | +0.376% | 51% | 0.0% | 2% | **OK** |
| 2025 | 170 | 0.90 | -0.098% | 49% | 0.0% | 1% | WEAK |
| 2026 | 478 | 0.95 | -0.041% | 48% | 0.1% | 1% | WEAK |

**Critères fold OK**: n≥10, PF≥1.30, E>0, MaxDD≤8%, squeeze_rate≤35%
**Critères fold catastrophique**: PF<0.75 OU MaxDD>8% OU squeeze_rate>50%

---

## Baseline comparison

*Voir `reports/short_rebuild/short_baseline_comparison.json`.*

| Strategy | PF | Expectancy | n_trades | Vs TRM |
|---|---|---|---|---|
| **TRMShortFleet** | ∞ (fictif) | +0.00002% | 1212 | référence |
| random_short_same_frequency | — | 0% | 0 | FAIL ⚠ |
| short_below_ema20_50 | 0.80 | -1.12% | 24 454 | INFO |
| short_breakdown_local_low_24 | 0.71 | -1.98% | 6 173 | FAIL ⚠ |
| short_funding_extreme_positive | 0.72 | -1.36% | 1 679 | FAIL ⚠ |
| short_failed_breakout_simple | 0.62 | -2.07% | 3 153 | INFO |
| always_cash | — | 0% | 0 | FAIL ⚠ |

**Verdict**: NOT_DEPLOYABLE — TRMShortFleet ne bat pas les baselines obligatoires par PF.
*Note: Le PF TRM affiché est ∞ car le benchmark utilise des données synthétiques (pas les vrais folds). Les résultats walk-forward réels sont dans la table ci-dessus.*

---

## Cost stress

*Voir `reports/short_rebuild/short_cost_stress.json`.*

| Scénario | PF slip×1 | PF slip×2 | PF slip×3 | Verdict |
|---|---|---|---|---|
| 10 bps normal | 0.36 | 0.32 | 0.28 | broken |
| 15 bps stress | 0.26 | 0.23 | 0.21 | broken |
| 20 bps extreme | 0.20 | 0.17 | 0.15 | broken |
| Worst 10% liquidity | 0.51 | 0.45 | 0.39 | broken |
| Funding adverse | 0.36 | 0.32 | 0.28 | broken |
| Pire cas | 0.15 | — | — | broken |

**Résultat**: 0/36 scénarios viables. Le SHORT ne survit à aucun test de coût.
**Seuil requis** pour SHORT_PAPER_CANDIDATE: survie à 15 bps + slippage ×2. Non atteint.

---

## Squeeze risk

| Fold | Squeeze loss rate | Avg MAE short | Squeeze rejects |
|---|---|---|---|
| Toutes périodes | *TBD* | *TBD* | *TBD* |

**Gate squeeze_risk_score > 0.70**: bloque le trade automatiquement.
**Squeeze reject label**: exclu du training (y_short = -1 ou label gris).

---

## Portfolio hedge value

*Voir `scripts/test_short_hedge_allocator.py` pour la simulation.*

| Métrique | Long only | Long + SHORT hedge |
|---|---|---|
| Max drawdown | *TBD* | *TBD* |
| Sharpe ratio | *TBD* | *TBD* |
| Hedge effectiveness | — | *TBD* |
| Correlation (L vs S) | — | *TBD* |

**Règle d'allocation hedge**:
- Max short exposure: min(10%, 0.5 × long_exposure_total)
- Prioriser BTC/ETH shorts
- Altcoin shorts uniquement si own breakdown + BTC weak + liquidity ok

---

## Deployment decision

| Décision | Valeur |
|---|---|
| SHORT live | **false** |
| SHORT paper | **false** — SHORT_REJECTED interdit le paper trading |
| COMBINED (long+short) | **false** |
| Verdict final | **SHORT_REJECTED** |

**Règle absolue**: Aucun SHORT ne retourne `SHORT_DEPLOYABLE` directement depuis cette validation.
Le chemin obligatoire: validation walk-forward → paper trading séparé → review → live.

---

## Remaining risks

| Risque | Probabilité | Impact | Mitigation |
|---|---|---|---|
| Squeeze adverse | Élevé | Élevé | Gate squeeze_risk_score, MAE limit dans les labels |
| Funding adverse | Moyen | Moyen | SHORT_COST_PCT inclut funding, test stress funding |
| Slippage extrême | Moyen | Moyen | Test slippage ×2 et ×3 dans stress tests |
| Liquidation cascade | Faible | Élevé | No short > 10% exposure, monitoring OI |
| Regime shift | Moyen | Élevé | Walk-forward strict par année, gate NO_SHORT |
| Liquidity collapse | Faible | Élevé | Filtre volume minimum, filtre worst 10% liquidity |
| Overfit sur bull trap 2021 | Moyen | Moyen | Out-of-sample strict, pas de feature sur 2021 en test |

---

## Next actions

1. **Fait**: Walk-forward exécuté → verdict **SHORT_REJECTED**
2. **Action requise**: Archiver ce branch dans `_archive_disabled/short_rebuild_v1/` si on abandonne
3. **Alternative**: Relancer avec les 50 actifs multi-asset (pas seulement AAVEUSDT) pour avoir plus de folds statistiquement représentatifs
4. **Alternative**: Ajouter les features macro réelles (funding rate, L/S ratio, OI) via le bundle de données complet
5. **Ne pas faire**: Activer le SHORT, modifier les résultats pour les rendre positifs, lancer le paper trading
6. **Prochaine itération**: rebuild-short-v2 avec 50 actifs + features macro + horizons 8h

---

*Rapport généré par: rebuild-short-trm-fleet-stress-alpha*
*Scripts: audit_short_failures.py | walk_forward_short_4h.py | benchmark_short_baselines.py | stress_test_short_costs.py | test_short_hedge_allocator.py*
