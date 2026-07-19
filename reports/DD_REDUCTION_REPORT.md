# DD Reduction Report — Phase 43-46 (2026-06-28)

Branche `feat/dd-reduction-regime-exit`. Objectif : maxDD 7.3% → ≤3% en gardant ROI>0,
via **forced exit on regime flip** (alpha) + **intra-position DD governor** (survie).

## Bugs corrigés en cours de route (importants)

1. **Whipsaw flip-exit** : sortir sur NEUTRAL provoquait un whipsaw BULL↔NEUTRAL horaire
   (B1 −22.6% vs B0 −4.6%). Fix : **hystérésis** — entrer en BULL/RECOVERY, sortir seulement
   sur BEAR/CRASH/UNKNOWN, tenir NEUTRAL.
2. **Ratchet intra-governor** : `portfolio_dd` calculé sur le peak ALL-TIME → une fois >2.5% sous
   le peak, CLOSE_ALL à chaque barre → whipsaw (B2 −36%, fees ×2.7). Fix : **DD GLISSANT** (fenêtre 30j).
3. **Seuil intra 1.0%** dans le bruit horaire crypto → whipsaw. Fix : 2.0%.

## Ablation 2022-2026 (`reports/dd_reduction_regime_exit_intra_governor_2022_2026.json`)

| Run | ROI | PF | maxDD | dir PnL |
|---|---:|---:|---:|---:|
| B0 long gaté (baseline) | −4.6% | 0.93 | **−7.2%** | +730 |
| B1 + flip exit | −6.0% | 0.90 | −8.4% | +599 |
| B2 + intra gov | −5.2% | 0.92 | −7.6% | +718 |
| B3 long final (1+3) | −8.0% | 0.87 | **−10.1%** | +565 |
| C3 long + carry | −2.4% | 0.99 | −10.3% | — |
| D3 final | −3.6% | 0.99 | −10.4% | — |
| E carry seul | **+6.3%** | 1.02 | **−0.5%** | — |

## Gates (Phase 45)

| Gate | Cible | Résultat | OK |
|---|---|---|---|
| Regime-flip-exit : DD baisse ≥30% | oui | DD 7.2% → 8.4% (**augmente**) | ✗ |
| Intra-gov : DD baisse | oui | 7.2% → 7.6% (**n'aide pas**) | ✗ |
| bull_capture_ratio ≥50% | oui | **77%** (dir 730→565) | ✓ |
| ROI reste > 0 | oui | B3 −8% (non) | ✗ |

## Verdict — RÉSULTAT NÉGATIF HONNÊTE

**Les leviers 1+3 ne réduisent PAS le drawdown** ; après correction des 3 bugs (whipsaw,
ratchet, seuil), ils sont au mieux neutres et au pire augmentent le DD (B3 10.1% > B0 7.2%) en
gardant 77% du bull capture. Le maxDD ~7% est **structurel** : il vient de **grinds multi-semaines
en NEUTRAL / transitions de régime** que des sorties horaires ne peuvent pas couper sans whipsaw.

→ On **n'adopte PAS** les forced-exits comme réducteurs de DD (modules gardés, désactivés par défaut).
Conformément à la règle "ne pas sur-optimiser le DD en tuant le rendement", on ne sculpte pas.

## Décision

```
FORCED_EXIT + INTRA_GOV : REJETÉS comme réducteurs de DD (ne marchent pas ; bugs documentés)
CARRY_SLEEVE            : PAPER   (E : +6.3%/4.5y, maxDD 0.5%, SEUL positif avec DD≤3%)
LONG_BOOK              : PAPER_REGIME_GATED mais DD ~7% STRUCTUREL (gating entrée OK, exits non)
PORTFOLIO_V1 (long+carry, sans leviers) : DEFENSIVE_PASS (+1.3%/7.5% — cf. v0.12 C)
MICRO_LIVE             : DISABLED
```

## Prochaine étape (Phase 47, maintenant justifiée par la mesure)

Les exits horaires échouent → la réduction de DD doit venir d'**en amont** :
1. **RegimeGate PAR-ASSET** : ne pas ouvrir un alt long si son propre régime est faible (le DD vient
   d'alts longs tenus pendant des rotations où BTC est "BULL" mais l'alt non).
2. Sinon, **accepter carry-only comme premier sleeve PAPER** (+6.3%/DD 0.5%) et chercher un alpha
   convexe (event/liquidation, vrai feed OI) pour le rendement — le long régime-gaté reste défensif
   (réduit la perte de −30% à −5%) mais n'est pas, seul, un générateur de rendement à DD≤3%.

Le carry est le seul bloc qui passe ROI>0 ET DD≤3% aujourd'hui.
