# Préenregistrement — cross_sectional_momentum_v1

Écrit avant tout accès aux données de retours cross-sectionnels au-delà de
ce qui est déjà archivé, avant tout calcul d'edge. Distinct de `ctrend`
(piste 1, `CTREND_REJECTED`, `859ebad`) : CTREND pariait sur une
**exposition nette longue** (top-K equal-weight, cash conditionnel au
régime BTC) et a été rejeté une fois le biais de survivance retiré sur
l'univers PIT (CAGR −2,1 %, médiane mensuelle −3,4 %). Ici, aucune
exposition nette : portefeuille **long-short** par décile, bêta marché
neutralisé explicitement à la construction — la thèse testée (prime
relative gagnants-perdants, market-neutral) n'a pas été testée par
CTREND_REJECTED, qui portait sur un pari directionnel cadencé par un
filtre de régime, pas sur un spread long-short.

## Objectif

Identifier une prime de momentum cross-sectionnelle long-short,
market-neutral (bêta BTC ≈ 0), qui reste positive après neutralisation du
bêta, de la volatilité et de la concentration sectorielle, nette des coûts
de financement du short et d'exécution.

```yaml
experiment_id: cross_sectional_momentum_v1

objective: >
  identify a cross-sectional long-short momentum premium, beta-neutral to
  BTC, that remains positive after short-borrow costs, execution costs and
  sector/vol neutralization

mechanism: >
  rank a point-in-time universe of 30-80 liquid perps by multi-horizon
  momentum residualized against BTC beta; long top decile, short bottom
  decile, equal risk-weighted, rebalanced on a fixed grid, beta and sector
  exposure neutralized at construction

universe:
  size: 30 to 80 liquid perps
  source: >
    point-in-time snapshot already archived by bin/archive-derivs
    (binance_futures_universe, since 2026-07-17) — reuse, do not rebuild
    from scratch unless statistical depth requires history before that date
  constraints:
    - no survivorship bias: delisted names included, same fix already
      validated (and required) by CTREND v1
    - shortability verified per asset per period, never assumed

assets:
  no_asset_selection_after_observing_results: true

primary_edge: >
  cross-sectional spread between the long-decile and short-decile
  portfolios, residualized against BTC beta, minus short-borrow cost,
  minus fees, minus slippage, minus rebalancing/turnover cost — measured
  event-level (per rebalance period) before any portfolio optimizer is
  built

mandatory_costs:
  - maker/taker fees on entry/exit for every name, both legs
  - bid-ask spread and slippage per name
  - short-borrow cost (or funding cost of a short perp leg) per name
  - rebalancing/turnover cost at each grid step
  - concentration cost if any single name dominates PnL

primary_rejection_conditions:
  - net spread <= 0 after central costs
  - negative under costs x2
  - single name contributes > 15% of total PnL
  - residual beta to BTC materially different from 0 after neutralization
  - dependence on one year or one regime (leave-one-year fails)
  - apparent edge caused by survivorship bias (same failure mode already
    found in CTREND v0 — today's universe applied to the past)
  - drawdown incompatible with dd_kill
```

## Leçons portées depuis les pistes précédentes (pas ignorées, appliquées)

- **`CTREND_REJECTED` (`859ebad`)** — le biais de survivance a inversé le
  signe du résultat (v0 positif → v1 négatif, univers PIT). Ici, univers
  PIT dès l'étape 3, jamais un univers "aujourd'hui appliqué au passé"
  comme le v0 assumé de CTREND.
- Réutiliser l'univers point-in-time déjà archivé par `bin/archive-derivs`
  (`binance_futures_universe`, depuis 2026-07-17) plutôt que d'en
  reconstruire un depuis les volumes Vision — sauf si l'historique avant
  cette date est nécessaire pour la profondeur statistique, auquel cas le
  reconstruire avec la même méthode que CTREND v1 (top-50 volume médian
  30 j décalé).
- **`CARRY_GATE_V2` (df3e1e5, REJECTED_PORTFOLIO)** — mesurer l'edge
  événementiel net par période de rebalancement (étape 5) avant de
  construire l'optimiseur de portefeuille (étape 7), jamais l'inverse.
- **`cross_exchange_stress_gate_h2`** — aucun résultat cité ici sans commit
  + hash + commande reproductible depuis un checkout propre, dès l'étape 3.
- **`top_traders` (piste 3, NO_EDGE, `7ef8d83`)** — un effet
  cross-sectionnel peut être fort avant 2024 et mort depuis (t = 14,8 →
  t = −0,75). Tester explicitement la stabilité inter-année
  (leave-one-year), pas seulement un split in-sample/out-of-sample global,
  avant tout verdict positif.

## Gates communs (déjà en vigueur pour tout l'edge factory)

DSR≥95%, PBO≤10%, coûts×2 positifs, robustesse (délai d'exécution, retrait
top-10 événements), indépendance |corr|≤0,35 avec chaque famille déjà
retenue, contribution marginale nette positive au portefeuille combiné
(`research/edge_factory/README.md`).

## Ordre des travaux (aucune étape sautée)

```text
1. [FAIT] distinguer la thèse de CTREND_REJECTED (ce document)
2. [CE DOCUMENT] préenregistrer cross_sectional_momentum_v1
3. vérifier la disponibilité et la qualité de l'univers PIT déjà archivé
   (bin/archive-derivs, depuis 2026-07-17) et l'historique de
   shortabilité par actif
4. construire le classement momentum multi-horizon résiduel du bêta BTC
   (réutiliser la logique z-score de CTREND, adaptée long-short)
5. tester l'edge événementiel net par période de rebalancement (spread
   long-short, avant tout optimiseur de portefeuille)
6. vérifier la stabilité inter-année (leave-one-year) et la concentration
   par nom (cap 15 % du PnL) avant tout verdict positif — leçon top_traders
7. seulement ensuite construire l'optimiseur de portefeuille
   (neutralisation bêta/secteur/vol à la construction)
8. mesurer la contribution portefeuille (indépendance vs les autres
   moteurs vivants, même famille de données que ctrend fermé)
```

Étapes 3-8 non commencées. Aucun code, aucune donnée, aucun résultat dans
ce document.
