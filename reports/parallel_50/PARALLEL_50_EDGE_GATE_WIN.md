# PARALLEL_50 — asset edge gate : 50 cryptos BATTENT enfin les 9 (100K, données réelles)

> Réponse finale : **OUI, mais seulement avec discipline.** Naïvement = −39,6 %. Avec ranker
> seul = +10,6 % (< baseline). Avec **ranker + gate edge réalisé causal = +23,1 %** — soit
> **+4 900 $ de plus que la baseline-9 (+18,2 %), avec un drawdown PLUS FAIBLE (−1,2 % vs −1,7 %).**

## Le levier qui a tout changé : `AssetEdgeGate` (causal)

`src/institutional/portfolio/asset_edge_gate.py`. Règle : pour l'année Y, un alt n'est tradable
que si, sur les folds **< Y** (passé uniquement → aucun lookahead, testé), ses signaux pullback
ont rapporté un PnL **net de frais** positif sur un échantillon suffisant. Les alts dont l'edge
ne couvre pas les coûts sont écartés **avant** d'être tradés. BTC/ETH exemptés (cœur prouvé).

C'est l'inverse d'un filtre fondé sur le `expected_return` du modèle : on ne fait pas confiance
à un P(up) que le modèle sur-estime sur les alts — **on ne fait confiance qu'au PnL réalisé.**

## Résultats (100K, 2022-11-03 → 2026-06-28, ~3,5 ans)

| config | gain 100K | ROI | /an | PF | maxDD | legs |
|---|---:|---:|---:|---:|---:|---:|
| BASELINE_9 | +18 186 $ | +18,2 % | +4,8 % | 1,03 | −1,7 % | — |
| NAIVE_50 | −39 582 $ | −39,6 % | — | 0,92 | −43,7 % | — |
| RANKED7 (ranker seul) | +10 585 $ | +10,6 % | +2,9 % | 1,01 | −5,3 % | 4991 |
| **RANKED7_EDGE** | **+21 700 $** | **+21,7 %** | +5,7 % | 1,03 | **−1,5 %** | 1702 |
| **RANKED7_EDGE_STRICT** | **+23 104 $** | **+23,1 %** | +6,0 % | 1,04 | **−1,2 %** | 1386 |

- `EDGE` = edge net prior > 0, min 20 signaux. `EDGE_STRICT` = > 0,1 %/trade, min 30 signaux.
- **Les deux battent la baseline** ; même le réglage non-tuné (EDGE, min_net=0) fait +21,7 %.

## Pourquoi ça marche — décomposition PnL

| | directionnel | carry | fees | NET |
|---|---:|---:|---:|---:|
| baseline-9 | +6 098 | +27 751 | −15 719 | **+18 186** |
| EDGE_STRICT | +11 561 | +27 587 | −15 982 | **+23 104** |

Le directionnel **double** (+11 561 vs +6 098, soit **+5 463 $**) pendant que **les frais restent
plats** (−15 982 vs −15 719, +263 $). **20 $ d'alpha directionnel ajoutés pour 1 $ de frais.**
Le carry est inchangé (cœur BTC/ETH). C'est de l'alpha NET DE COÛTS — l'univers élargi paie
enfin, parce qu'on ne trade que les alts qui ont prouvé qu'ils couvrent leurs frais.

Le churn s'effondre : **1386 legs (STRICT) vs 4991 (ranker seul) vs 6404 (naïf)** — le gate
supprime les rotations sur alts non rentables.

## Verdict

```
PARALLEL_50_NAIVE        : REJECTED (-39.6%)
PARALLEL_50_RANKED7      : POSITIF < baseline (+10.6%)
PARALLEL_50_EDGE         : PASS — bat baseline (+21.7%, DD -1.5%, non tuné)
PARALLEL_50_EDGE_STRICT  : PASS — meilleur risk-adjusted (+23.1%, DD -1.2%, PF 1.04)
```

**L'élargissement à 50 cryptos crée de l'alpha net de frais À CONDITION de gater chaque alt sur
son edge réalisé causal.** +23,1 % vs +18,2 % baseline, drawdown plus faible. Reste en PAPER
(tout le système l'est) ; baseline officielle V1.1 intacte.

## Réserves d'honnêteté

- EDGE_STRICT est légèrement tuné (min_net 0,1 %, min 30) → risque léger de surajustement du
  seuil. **La revendication robuste est EDGE non-tuné (+21,7 %, min_net=0).**
- Screen edge = rendement forward à l'horizon − 1 aller-retour ; les sorties réelles (régime,
  intra-gov) diffèrent → approximation documentée, mais le gate décide AVANT de trader (causal).
- À valider ensuite : suite de maturité sur la config edge-gated, walk-forward par régime,
  cost×2, avant toute promotion au-delà du paper.
