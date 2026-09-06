# Préenregistrement — calendar_basis_v1

Écrit avant tout accès aux données de courbe futures/basis calendaire, avant
tout calcul d'edge. Distinct de `basis_dispersion` (piste 8,
`NO_EDGE (directionnel)`, `a589e54`) : cette piste-là pariait sur le
**niveau** du funding comme signal directionnel (bucket z>2 → la
continuation domine, +0,50 %) et a été rejetée sur ce mécanisme précis. Ici,
aucun pari directionnel : on exploite l'écart **relatif** entre deux
instruments de maturité différente sur le même sous-jacent (spot/perp/future
daté), en position delta-neutre — un moteur de portage relatif, thèse
matériellement distincte, pas une réutilisation cosmétique.

## Objectif

Identifier des écarts de basis calendaire (spot vs perp vs futures datés à
échéance proche/lointaine) qui restent positifs après financement, frais,
marge et risque de convergence.

```yaml
experiment_id: calendar_basis_v1

objective: >
  identify calendar/basis relative-value spreads across spot, perp and
  dated futures that remain positive after funding, financing, fees and
  convergence risk

mechanism: >
  simultaneous long/short across two instruments of different maturity on
  the same underlying (long near-dated future vs short far-dated future,
  or long spot vs short dated future, or long perp vs short dated future),
  delta neutrality maintained, convergence captured at or before expiry

venues:
  - Binance (dated quarterly futures + perp)
  - Bybit dated quarterly futures + perp, only if PIT history reproducible
  - Deribit calendar futures, only if usable historical data exist

assets:
  start_with: [BTC, ETH]
  no_asset_selection_after_observing_results: true

primary_edge: >
  annualized basis captured between the two legs, minus expected funding on
  any perp leg held, minus financing cost on any spot leg, minus fees,
  minus slippage, minus borrow — measured event-level (per convergence
  cycle) before any multi-leg engine is built

mandatory_costs:
  - maker/taker fees on entry and exit, both legs
  - bid-ask spread on both legs
  - slippage on both legs
  - funding paid/received on any perp leg held during the trade
  - collateral/margin financing
  - borrow cost if a leg requires borrowing
  - basis risk from imperfect convergence at/before expiry
  - roll risk when rebuilding calendar spreads across expiries
  - venue failure haircut

primary_rejection_conditions:
  - net spread <= 0 after central costs
  - negative under costs x2
  - dependence on one asset, one venue, or one expiry cycle
  - apparent edge caused by illiquid quarterly contracts (wide quoted
    spread with no executable depth behind it)
  - drawdown incompatible with dd_kill
  - apparent edge caused by timestamp or settlement mismatch between
    spot/perp/future clocks
```

## Leçons portées depuis les pistes précédentes (pas ignorées, appliquées)

- **`basis_dispersion` (piste 8, NO_EDGE directionnel, `a589e54`)** — la
  distinction de mécanisme ci-dessus n'est pas cosmétique : le funding
  extrême en niveau est mort comme signal directionnel, mais rien dans ce
  résultat ne dit quoi que ce soit sur l'écart relatif entre deux
  instruments de maturité différente. Les deux thèses doivent rester
  falsifiées séparément.
- **Jamais un timestamp exact-ms comme preuve de simultanéité** (même
  leçon que `funding_relative_value_cross_venue_v1`, découverte
  `settlement_timestamp_alignment_v1`) — s'applique à toute jointure
  spot/perp/future à travers des horloges différentes. Réutiliser
  `mutual_one_to_one_match`.
- **`CARRY_GATE_V2` (df3e1e5, REJECTED_PORTFOLIO)** — mesurer l'edge
  événementiel net par cycle de convergence (étape 6) avant de construire
  le moteur multi-jambes (étape 7), jamais l'inverse.
- **`cross_exchange_stress_gate_h2`** — aucun résultat cité ici sans commit
  + hash + commande reproductible depuis un checkout propre, dès l'étape 3.
- **Nouvelle leçon propre à cette piste** : les futures datés trimestriels
  ont une profondeur bien plus fine que les perps. Le spread coté ne suffit
  pas — vérifier la profondeur réellement exécutable au moment précis où
  une jambe devrait entrer/sortir, avant de compter une opportunité.

## Gates communs (déjà en vigueur pour tout l'edge factory)

DSR≥95%, PBO≤10%, coûts×2 positifs, robustesse (délai d'exécution, retrait
top-10 événements), indépendance |corr|≤0,35 avec chaque famille déjà
retenue (en particulier `funding_relative_value_cross_venue_v1` — même
famille de risque de base, vérifier qu'il ne s'agit pas du même pari
recyclé), contribution marginale nette positive au portefeuille combiné
(`research/edge_factory/README.md`).

## Ordre des travaux (aucune étape sautée)

```text
1. [FAIT] distinguer la thèse de basis_dispersion (ce document)
2. [CE DOCUMENT] préenregistrer calendar_basis_v1
3. inventorier les données de futures datés réellement accessibles
   (Binance/Bybit quarterly, Deribit) — aucun collecteur n'existe
   aujourd'hui (absent de data_pipeline/sources.py)
4. construire la surface actif × venue × maturité × basis annualisée
5. vérifier la liquidité exécutable des contrats datés (profondeur, pas
   seulement le spread coté)
6. tester l'edge événementiel net par cycle de convergence
7. seulement ensuite construire le moteur multi-jambes (calendar spread
   engine)
8. mesurer la contribution portefeuille (indépendance vs
   funding_relative_value_cross_venue_v1 en particulier)
```

Étapes 3-8 non commencées. Aucun code, aucune donnée, aucun résultat dans
ce document.
