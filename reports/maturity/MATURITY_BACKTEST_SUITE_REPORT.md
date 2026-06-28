# Maturity Backtest Suite Report (V1)

- fenêtre : 2022-11-03 → 2026-06-28 (3.6 ans)
- **SCORE : 90.0/100 → TIER : STRONG_PAPER**
- **DÉCISION : PAPER_CONTINUE / MICRO_LIVE_BLOCKED_UNTIL_60_90D_REAL_TRACKING**
- OFFENSIVE_ALPHA : DATA_NOT_READY

> La suite ne cherche pas à prouver le rendement. Elle prouve où le projet est mûr.

## Scorecard

| Check | Statut | score | poids |
|---|---|---:|---:|
| reproducibility | PASS | 1.00 | 10 |
| data_integrity | PASS | 1.00 | 15 |
| leakage_audit | PASS | 1.00 | 15 |
| baseline | PASS | 1.00 | 10 |
| regime_splits | PASS | 1.00 | 10 |
| cost_stress | PASS | 1.00 | 10 |
| sleeve_ablation | PASS | 1.00 | 10 |
| paper_replay | WARN | 0.50 | 10 |
| event_readiness | INFO | 0.00 | 5 |
| operational_stress | PASS | 1.00 | 5 |
| sizing_sensitivity | PASS | 1.00 | 0 |
| walk_forward | PASS | 1.00 | 0 |
| monte_carlo | PASS | 1.00 | 0 |

## Lecture honnête
- **L1 (reproductible/causal/intègre)** : PASS fort (repro, data_integrity, leakage_audit).
- **L2 (défensif)** : PASS (baseline +18.6%/3.6y, DD 1.9%).
- **L3 (robuste régimes/coûts/sleeves)** : PASS (cost×2 OK, carry=moteur, monte-carlo OK).
- **L4 (paper-live forward)** : NON ATTEINT — paper = re-run déterministe, 0 jour forward réel.
- **L5 (alpha offensif)** : DATA_NOT_READY (0 liquidation accumulée).

## Décision
Système **STRONG_PAPER** (L1-L3 solides). **Micro-live BLOQUÉ** tant que paper-live réel < 30-60j
et qu_aucun moteur offensif n_est prêt. Continuer paper + accumulation liquidations.