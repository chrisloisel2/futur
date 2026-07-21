# Préenregistrement — funding_relative_value_cross_venue_v1

Écrit avant tout accès aux données de spread réalisé, avant tout calcul
d'edge. Succède à `stress_gate_dispersion_v2_reproduction` (clôturé
`NO_INCREMENTAL_EDGE`, `configs/alpha20.yaml`) — mécanisme différent
(moteur de rendement market-neutral, pas un overlay de risque), thèse
nouvelle et matériellement distincte, pas une réutilisation cosmétique.

## Objectif

Identifier des spreads de funding net market-neutral entre venues qui
restent positifs après coûts d'exécution, financement, marge et risque de
venue — un moteur de rendement, pas un filtre de risque.

```yaml
experiment_id: funding_relative_value_cross_venue_v1

objective: >
  identify market-neutral net funding spreads between venues that remain
  positive after execution, borrow, margin and venue-risk costs

mechanism: >
  simultaneous long-perp on the lower-funding venue and short-perp on the
  higher-funding venue, with delta neutrality maintained through both legs

venues:
  - Binance
  - Bybit
  - Hyperliquid only if historical PIT data are reproducible

assets:
  start_with: [BTC, ETH, SOL, BNB]
  no_asset_selection_after_observing_results: true

primary_edge: >
  realized funding received on one leg minus funding paid on the other
  minus all execution and holding costs

mandatory_costs:
  - maker/taker fees on entry and exit
  - bid-ask spread on both legs
  - slippage on both legs
  - margin financing
  - collateral opportunity cost
  - rebalancing costs
  - partial fill risk
  - leg desynchronization
  - liquidation and ADL stress
  - venue failure haircut

primary_rejection_conditions:
  - net spread <= 0 after central costs
  - negative under costs x2
  - dependence on one asset, venue or year
  - excessive collateral transfers
  - unacceptable single-venue exposure
  - drawdown incompatible with dd_kill
  - apparent edge caused by timestamp or settlement mismatch
```

## Leçons portées depuis les pistes précédentes (pas ignorées, appliquées)

- **Jamais un timestamp exact-ms comme preuve de simultanéité** — la
  découverte `settlement_timestamp_alignment_v1` (jitter Binance 0-30ms,
  Bybit sur grille exacte) s'applique ici aussi à toute jointure entre
  venues. Réutiliser `mutual_one_to_one_match` (tolérance à fixer par une
  vérification structurelle identique, pas devinée) plutôt que réinventer.
- **`CARRY_GATE_V2` (df3e1e5, REJECTED_PORTFOLIO)** a déjà montré qu'un
  edge per-période positif peut être détruit par le churn/frais au niveau
  portefeuille — donc : mesurer l'edge événementiel net (étape 6) et
  seulement ensuite le moteur multi-jambes (étape 7), jamais l'inverse.
- **`cross_exchange_stress_gate_h2`** a montré qu'un module source peut
  disparaître du dépôt sans que le résultat cité le signale — donc :
  aucun résultat cité ici sans commit + hash + commande reproductible
  depuis un checkout propre, dès la première étape.
- **Comparabilité des intervalles** (piste basis_dispersion) : si les
  cadences de funding diffèrent entre venues à un instant donné, ne
  jamais comparer des taux couvrant des périodes économiques différentes
  sans normalisation explicite et préenregistrée.

## Gates communs (déjà en vigueur pour tout l'edge factory)

DSR≥95%, PBO≤10%, coûts×2 positifs, robustesse (délai d'exécution, retrait
top-10 événements), indépendance |corr|≤0,35 avec chaque famille déjà
retenue, contribution marginale nette positive au portefeuille combiné
(`research/edge_factory/README.md`).

## Ordre des travaux (aucune étape sautée)

```text
1. [FAIT] clôturer formellement STRESS_GATE
2. [CE DOCUMENT] préenregistrer funding_relative_value_cross_venue_v1
3. inventorier les données historiques réellement accessibles
4. vérifier les calendriers de funding par venue
5. construire un panel PIT des spreads réalisables
6. tester l'edge événementiel net
7. seulement ensuite construire le moteur multi-jambes
8. mesurer la contribution portefeuille
```

Étapes 3-8 non commencées. Aucun code, aucune donnée, aucun résultat dans
ce document.
