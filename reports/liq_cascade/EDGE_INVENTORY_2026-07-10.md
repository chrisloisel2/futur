# INVENTAIRE DES EDGES — 4 moteurs mesurés (2026-07-10)

Session : construction de 2 nouveaux moteurs événementiels (CROWDING_REVERSAL,
PREMIUM_DISLOCATION) + backfill premium 5-min 49 actifs + analyse de STACK
inter-moteurs sur tapes de trades OOS. Harnais et gates identiques pour tous
(pré-déclarés : purged val+embargo, top20% val-quantile, fold valide ⟺ train
≥2000, CANDIDATE ⟺ 3 folds PF≥1,35 sans destructeur, coûts 14 bps, stress ×2).

## Les 4 edges

| # | Edge | Mécanisme | Données | Verdict |
|---|---|---|---|---|
| 1 | **CARRY (V1.1/V1.2)** | portage funding delta-neutral | funding 8h | **VALIDÉ** (socle paper, +4,8→8,6 %/an) |
| 2 | **LIQ_CASCADE v2** | capitulation OI 30-min (event) | metrics 5-min Vision | **CANDIDATE** (2,35/1,41/1,12/1,42 — 3/4) |
| 3 | **CROWDING_REVERSAL** | washout de positionnement (état 24h) | toptrader+OI 5-min | **NO_EDGE au gate** (2026 destructeur) — raw PF 1,44, 2024-25 exceptionnels (PF 2,7/18,3), n faible (2 239) |
| 4 | **PREMIUM_DISLOCATION** | perp survendu vs index (basis) | premiumIndexKlines 5-min | **NO_EDGE au gate** (1/4, 2023 destructeur n=53) — 2024 PF 1,52, edge réel mais instable |

## Stack — la vraie diversification existe

Corrélations des PnL mensuels (trades OOS) : cascade↔crowding **0,12**,
crowding↔premium 0,17, cascade↔premium 0,57 (21 % de trades partagés — même
famille de stress). **Anticorrélation de régimes précieuse : 2025 est la
meilleure année du crowding (PF 18) et la plus faible de la cascade ; inverse
en 2026.**

## Stack — mais la CONVERSION reste LE problème (5e confirmation)

4 designs d'allocation mesurés sur les tapes (aucun re-fit) :

| Sim | Design | Résultat |
|---|---|---|
| A | union FIFO, cap 6 positions | **−50 %** (anti-sélection massive) |
| B | union, cap gross 60 %, 2 %/trade | −4,7 % |
| C | priorité score par batch, gross 60 % | −5,1 % (le gross ne sature pas intra-batch) |
| D | books séparés 20 %/moteur | cascade −9,5 % · **crowding +17,8 %** · premium −4,3 % · somme +4,0 % |

**Le mécanisme est identifié et constant : l'edge vit dans les RAFALES
(vagues de capitulation multi-symboles) ; toute exécution à capacité bornée
premier-arrivé prend le bleed régulier en entier et tronque les rafales.**
Preuve par contraste : les sommes événementielles non contraintes sont
fortement positives (cascade +152 %, crowding +115 %, premium +140 %, sizé
10 % linéaire) pendant que tous les books contraints saignent.

## Conséquence de design (prochain chantier, avec sa propre validation)

L'unité de trading ne doit PAS être l'event mais la **VAGUE** : agréger les
events simultanés en un trade de vague (panier top-k par score, ou 1 trade
par symbole-vague), avec capacité DYNAMIQUE (le gross doit s'étendre pendant
les vagues, pas se rationner). À implémenter dans le multileg backtester
(niveau 2), puis re-mesurer. Ne PAS itérer davantage sur les tapes actuelles
(risque de fishing — 4 designs déjà mesurés).

## Extension 2026-07-10 soir — 2 jambes candidates supplémentaires : REJETÉES

Analyse de couverture (trous identifiés : expansion/continuation + propagation
lead-lag) → 2 détecteurs construits et mesurés aux mêmes gates :

- **FLOW_IGNITION** (OI expansion z≥+3 + taker z≥+1 + thrust, continuation 8h) :
  **NO_EDGE** — raw PF 0,88 ; ML 2023 PF 1,35 mais 2024 PF 0,93 et 2026 PF 0,57
  DESTRUCTEURS. La continuation sur ignition ne survit pas aux coûts ; le régime
  2026 fade agressivement les ignitions.
- **BTC_SPILLOVER** (thrust BTC 1h ≥1,5 %, alt retardataire ≤40 % du mouvement,
  rattrapage 4h) : **NO_EDGE** — 2023 spectaculaire (PF 3,22, +78 bps) puis MORT
  (2024 PF 0,73, 2025 PF 0,76 destructeurs). Le lead-lag s'est arbitré : l'edge
  de propagation existait en 2023 et a disparu.

**Leçon** : le cimetière grandit (2 de plus), c'est le système qui marche. Les
mécanismes de RÉVERSION sur stress (cascade, crowding, premium) portent l'edge
événementiel ; les mécanismes de CONTINUATION (ignition, spillover) sont
arbitrés/fadés dans les régimes récents. Ne plus chercher de continuation
intraday sur ces données.

**3e candidate documentée (données CONFIRMÉES disponibles, build plus lourd)** :
**BASIS_TERM** — klines de TOUS les contrats trimestriels USDT-M depuis 2021 sur
Vision (`klines/BTCUSDT_YYMMDD/`) → basis annualisé spot/quarterly → second
carry INDÉPENDANT du funding (structure par terme, convergence à échéance).
Nécessite la comptabilité des rolls dans le multileg. Prochaine session dédiée.

## Réserves

- 3 variantes de harnais ont été exécutées au fil des sessions (v1, v2-iso,
  v2b) + 4 designs de stack : le risque de multiplicité est réel. Confirmation
  exigée : SHADOW forward ≥30j sur feed live + validation multileg.
- Crowding : 2 239 events seulement, verdict dominé par 2026 (n=43). Premium :
  fold 2023 destructeur sur n=53. Les deux se re-testent mécaniquement chaque
  mois avec l'accumulation (Vision J-2 + collecteur live).
- 2022 reste l'année interdite de tous les moteurs longs (aucun n'a de données
  d'entraînement pré-2022 suffisantes ET le régime est hostile) → le
  RegimeGate existant doit couvrir le bear profond.
