# CARRY_GATE_V2 — Portfolio Impact (2026-06-28)

Câblage de CARRY_GATE_V2 (consensus funding Binance×Bybit) dans le carry engine, mesure
de l'impact portefeuille sur la fenêtre overlap Bybit (2022-11-03 → 2026-06-28, 3.6 ans).

## Résultat — le signal NE devient PAS de l'alpha portefeuille

| Config (carry 50%) | ROI | annualisé | PF | maxDD | fees | carry PnL |
|---|---:|---:|---:|---:|---:|---:|
| **V1.1 — old sticky gate (FUNDING_POSITIVE_STABLE mono-exchange)** | **+18.6%** | **+4.8%** | 1.03 | **−1.9%** | −1526 | 2782 |
| V1.2 — gate-v2 cross-exchange (entry+exit) | −19.9% | −5.9% | 0.98 | −22.8% | −5280 | 2833 |
| V1.2 — gate-v2 size-only (old gate + dispersion réduit la taille) | +16.8% | +4.3% | 1.03 | −1.9% | −1365 | 2441 |
| V1.2 — carry 75% gate-v2 | −51.5% | −18.0% | 0.94 | −51.5% | −7629 | 2157 |

## Pourquoi (diagnostic)

Le test H3 (per-période) reste vrai : les périodes *gated* ont un net carry 2.65× et moins de
flips. **Mais ce n'est pas exploitable comme règle de portefeuille** :
1. **Entry+exit cross-exchange = CHURN** : exiger les 2 exchanges positifs et sortir dès qu'UN
   devient négatif → bien plus d'open/close que le gate mono-exchange *sticky* → **fees ×3.5**
   qui détruisent l'edge (et DD explose à −22.8%).
2. **Size-only** (vieux gate stable + dispersion réduit la taille) : pas de churn mais **rogne juste
   le rendement** (+16.8% < +18.6%) sans bénéfice DD (le DD est déjà 1.9%, rien à récupérer).

C'est la 3e occurrence de la même leçon (exit engine, forced-exit, carry-gate) :
**un edge conditionnel per-période ne devient pas automatiquement de l'alpha portefeuille — les
coûts de transaction et la dynamique de position dominent.**

## Décision (selon tes gates)

CARRY_GATE_V2 passe seulement si « net carry portfolio ↑, PF ↑, maxDD≤3% ». → **ÉCHEC**.

```
CROSS_EXCHANGE_DIRECTIONAL_ALPHA : REJECTED
CROSS_EXCHANGE_STRESS_GATE       : VALIDATED_SIGNAL (recherche/risque, pas câblé)
CARRY_GATE_V2                    : VALIDATED_SIGNAL (per-période) MAIS NON_VALIDATED_PORTFOLIO
PORTFOLIO_V1.1 (old gate, carry 50%) : CONFIRMÉ — +4.8%/an, DD 1.9% sur 3.6 ans (le socle)
PORTFOLIO_V1.2                   : ABANDONNÉ (n'améliore pas V1.1)
MICRO_LIVE                       : DISABLED
```

Modules gardés (désactivés par défaut : `carry_gate_v2`, `carry_gate_v2_size_only`) — réutilisables
si un jour les fees/exécution changent, mais **non recommandés** en l'état.

## Le vrai gain de cette phase

V1.1 carry 50% (old gate) est **confirmé comme socle solide** : **+4.8%/an, DD 1.9%** sur 3.6 ans
(2022-11→2026-06) — le meilleur résultat validé du projet, palier 1-2. Le cross-exchange reste un
**signal de recherche** (qualité carry per-période, stress) sans valeur portefeuille nette à ce stade.
Pour dépasser ~5%/an il faut toujours un moteur OFFENSIF sur vraie donnée (liquidations en cours de collecte).
