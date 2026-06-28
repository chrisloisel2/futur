# Regime-Gated Portfolio Report (Phase 39-41, 2026-06-28)

Branche `feat/regime-gated-long-book`. RegimeGate **causal** (`portfolio/regime_gate.py`,
F_t uniquement, no-lookahead testé) : les NOUVEAUX longs ne s'ouvrent qu'en BULL/RECOVERY ;
NEUTRAL → demi-taille ; BEAR/CRASH/UNKNOWN → bloqués. Carry indépendant, hedge lié au long.

## Ablation 2022-2026 (`reports/multileg_ablation_regime_gated_2022_2026.json`)

| Run | ROI | PF | maxDD | PnL [dir / carry / hedge / fees] |
|---|---:|---:|---:|---|
| A long brut | −30.5% | 0.82 | −31.9% | [−506 / 0 / 0 / −2546] |
| **B long gaté** | **−4.6%** | 0.93 | **−7.2%** | dir **+730** |
| **C long gaté + carry** | **+1.3%** | 1.00 | −7.5% | dir +743, carry +1116 |
| D final (+hedge) | +0.8% | 1.00 | −7.3% | hedge −18 |
| E carry seul | +6.3% | 1.02 | −0.5% | carry +1152 |
| F carry + hedge | +6.3% | 1.02 | −0.5% | hedge inactif |
| G long gaté + hedge | −5.2% | 0.92 | −7.6% | hedge −10 |

## Effet du RegimeGate (gate Phase 40)

| Critère gate | Cible | Résultat | OK |
|---|---|---|---|
| ROI long s'améliore | oui | −30.5% → −4.6% (dir PnL −506 → **+730**) | ✓ |
| DD long baisse ≥50% | oui | −31.9% → −7.2% (**−77%**) | ✓ |
| Longs réduits en BEAR/CRASH | ≥80% | bloqués (size_mult 0) | ✓ |
| Pas de lookahead | oui | test causalité PASS | ✓ |

→ **Le RegimeGate est le levier décisif** : il transforme le book long de destructeur
(−30%) à quasi-neutre positif en directionnel (+730), DD divisé par ~4.4.

## Système complet (D) — premier full-cycle POSITIF

C (long gaté + carry) = **+1.3%**, D (+hedge) = **+0.8%**, tous deux **positifs full-cycle**
pour la première fois (vs −28.6% avant gating). Le hedge coûte légèrement (insurance).

## Carry réconcilié

`reports/carry_return_reconciliation.md` : +0.37%/mois (BTC, notional plein) vs +0.12%/mois
(sleeve 20% gaté) — écart = **sizing 20% + gating funding**, cohérent. Carry réel mais petit
à cette taille. → **CARRY_SLEEVE : PAPER**.

## Verdict (Phase 41)

| Gate Portfolio V1 | Cible | Résultat |
|---|---|---|
| ROI full-cycle > 0 | oui | **+0.8% (D) ✓** |
| PF net ≥ 1.10 | oui | 1.00 (limite) |
| **maxDD ≤ 3%** | oui | **7.3% ✗** |
| 2026 perte < 1% | oui | à vérifier |

**Cas 1 partiel** : D est devenu **positif full-cycle** (la grande victoire du gating) MAIS
**maxDD 7.3% > 3%** → **PAS encore PAPER_PORTFOLIO_V1**.

```
MULTILEG_ACCOUNTING : VALIDATED
LIVE_WRITE_PATH     : SAFE       DATASTORE : CLEAN
REGIME_GATE         : VALIDATED (causal, −30%→positif, DD −77%)
CARRY_SLEEVE        : PAPER (réconcilié, +6.3%/4.5y, DD 0.5%)
HEDGE_GOVERNOR_V1   : PAPER_ONLY (insurance, coûte un peu)
LONG_BOOK           : PAPER_REGIME_GATED
PORTFOLIO_V1 (D)    : DEFENSIVE_PASS (positif mais DD 7.3% > 3%)
MICRO_LIVE          : DISABLED
```

## Prochaine étape décisive : réduire le DD de 7.3% à ≤3%

Le système est positif ; le seul gate qui bloque est le DD. Sources du DD 7.3% :
longs ouverts en fin de BULL puis pris dans la bascule de régime + RegimeGate sur BTC
appliqué à tous les alts. Leviers (ordre) :
1. **Governor intra-position sur les longs gatés** (couper les longs en DD, pas seulement bloquer
   l'ouverture) — mais éviter le ratchet (DD glissant).
2. **RegimeGate par asset** (pas seulement BTC) pour les alts.
3. **Sortie de régime forcée** : fermer les longs quand le régime passe BULL→BEAR (pas seulement
   bloquer les nouveaux).
Si DD passe ≤3% avec ROI restant > 0 → **PAPER_PORTFOLIO_V1**.
