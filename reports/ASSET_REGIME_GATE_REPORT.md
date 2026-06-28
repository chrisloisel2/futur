# Asset Regime Gate Report — Phase 47 (2026-06-28)

Branche `feat/asset-regime-gate`. Politique **réparer-d'abord** appliquée : au lieu de désactiver
le LONG_BOOK (DD trop haut), on a réparé le filtre manquant **par-actif**.

`asset_regime_gate.py` : long autorisé(asset) ⟺ btc_regime ∈ {BULL,RECOVERY} **ET**
asset_regime ∈ {BULL,RECOVERY} (causal, réutilise RegimeGate par actif). Flip exit ASSET-LEVEL
(sortie si BTC **ou** l'actif devient hostile) = réparation du FORCED_EXIT.

## Ablation 2022-2026 (`reports/asset_regime_gate_ablation_2022_2026.json`)

| Run | ROI | PF | maxDD | PnL [dir/carry/hedge/fees] |
|---|---:|---:|---:|---|
| M macro gate (BTC only) | −4.6% | 0.93 | −7.2% | [730/0/0/−1192] |
| **A asset gate** | **+1.6%** | 1.07 | **−4.6%** | [624/0/0/**−465**] |
| A asset + flip | +1.3% | 1.06 | −4.6% | [598/0/0/−468] |
| A asset + flip + intra | +1.3% | 1.06 | −4.6% | — |
| A asset + carry | +7.6% | 1.02 | **−2.0%** | [609/1161/0/−1004] |
| **A asset FULL (+hedge)** | **+7.6%** | 1.02 | **−2.0%** | [609/1161/−0/−1013] |
| E carry seul | +6.3% | 1.02 | −0.5% | — |

## Effet du gate par-actif (la réparation)

- **Long book : −4.6% → +1.6%** (POSITIF). Le gate par-actif ne trade que quand BTC **et** l'actif
  sont favorables → **fees −1192 → −465 (−61%)** et **maxDD 7.2% → 4.6%**.
- → Le DD structurel venait bien d'**alts longs mal filtrés** (tenus quand BTC=BULL mais l'actif non).
- PnL LONG par actif (tous positifs après gate) : BTC +75, SOL +31, ETH +24 → **aucun actif destructeur**
  restant. Pas besoin de réduire l'univers.

## Système complet A_asset_full — par année (robustesse)

| Année | Régime | ROI | maxDD |
|---|---|---:|---:|
| 2022 | bear | −1.17% | 1.3% |
| 2023 | recovery | +4.50% | 1.6% |
| 2024 | bull | +2.26% | 1.4% |
| 2025 | mixed | +1.67% | 1.9% |
| 2026 | hostile | **+0.14%** | 0.2% |
| **Full-cycle** | | **+7.6%** | **−2.0%** |

**4/5 années positives**, bear 2022 limité à −1.2%, **2026 hostile flat-positif**, DD annuel ≤2%.
Ce n'est PAS un artefact 2024.

## Gate PAPER_PORTFOLIO_V1

| Critère | Cible | Résultat | OK |
|---|---|---|---|
| ROI full-cycle > 0 | oui | **+7.6%** | ✓ |
| maxDD ≤ 3% | oui | **2.0%** | ✓ |
| 2026 ≥ flat ou perte < 1% | oui | **+0.14%** | ✓ |
| carry positif | oui | +1161 | ✓ |
| aucun short nu | oui | invariants | ✓ |
| PF net ≥ 1.10 | oui | 1.02 | ✗ (marginal) |
| aucun moteur > 60% PnL | oui | carry ~65% | ✗ (concentré carry) |

## Décision : **PAPER_PORTFOLIO_V1_CANDIDATE**

ROI>0 ET DD≤3% ET robuste multi-régime → premier config crédible. **Réserves honnêtes** :
PF 1.02 (mince), **~65% du PnL = carry** (les longs gatés sont quasi break-even après coûts :
le gate les a rendus NON-destructeurs, pas fortement positifs). C'est donc "carry + longs
régime-gatés défensifs", pas encore un générateur de rendement diversifié.

```
ASSET_REGIME_GATE     : VALIDATED (réparation réussie : long book −4.6%→+1.6%, DD −36%)
LONG_BOOK             : REPAIRED (asset-gated, positif, DD-contrôlé, aucun actif destructeur)
CARRY_SLEEVE          : PAPER (~65% du PnL)
HEDGE_GOVERNOR_V1     : PAPER_ONLY (neutre ici)
FORCED_EXIT / INTRA   : neutres avec asset-gate (gardés activés, non nuisibles)
PORTFOLIO_V1 (A_full) : PAPER_PORTFOLIO_V1_CANDIDATE (+7.6%/DD 2.0%, PF 1.02)
MICRO_LIVE            : DISABLED (paper observation requise d'abord)
```

## Prochaines étapes (avant micro-live)
1. **Paper-live 30-60 j** du système multi-leg asset-gated, réconcilier backtest vs paper (mêmes legs).
2. Épaissir le PF (1.02→≥1.10) : améliorer l'edge long (les longs sont break-even) ou augmenter
   prudemment le sizing carry (DD carry 0.5% le permet) — sans casser DD≤3%.
3. Réduire la concentration carry (<60%) via un 2e edge non-directionnel (liquidation event-first, vrai feed OI).
