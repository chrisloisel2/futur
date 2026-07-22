# Piste 8 — Basis spot–perp & dispersion inter-exchange

> ## ⚠️ STATUT COURANT (2026-07-21) — supplante le statut STRESS_GATE ci-dessous
>
> ```yaml
> CROSS_EXCHANGE_STRESS_GATE:
>   previous_status: VALIDATED_SIGNAL
>   current_status: UNVERIFIED_PROVENANCE
>   blocking_reason:
>     - implementation source unavailable in canonical repository
>       (src/institutional/data/derivatives/features/cross_exchange_features.py
>       introuvable dans tout l'historique Git local ET distant GitHub —
>       git log --all --full-history, git rev-list --objects --all,
>       git reflog --all, branche non fusionnée feat/free-derivatives-backfill
>       — tous vides. Idem pour l'ancien module cross_exchange.py)
>     - original data manifest unavailable
>     - lookahead and PIT properties not auditable
>     - original result not currently reproducible
>   allowed_use: [forensic investigation, preregistered reproduction]
>   forbidden_use: [runner qualification, portfolio wiring,
>                   ACTIVE or OBSERVE_ONLY deployment, evidence for risk allocation]
> ```
>
> Le signal n'est PAS classé NO_EDGE : il n'a pas été falsifié, sa
> provenance est simplement invérifiable en l'état. Voir
> `research/forensics/stress_gate_c78874b/` pour la tentative de
> récupération. Le rejet portefeuille de CARRY_GATE_V2 (H3, `df3e1e5`,
> reproductible et vérifié 9/9) ne falsifie PAS ce statut (H2) : H3 est
> un mécanisme de carry conditionné, H2 un indicateur de stress — le
> rejet de H3 démontre la vulnérabilité au churn de cette famille, pas
> l'invalidité de H2.
>
> ## ❌ VERDICT historique : NO_EDGE directionnel (2026-07-17, commit distant `a589e54`)
>
> Funding extrême en niveau, panel 9 actifs × 5,4 ans (52 573 obs 8 h) :
> t = −0,17, signe instable, et le bucket z > 2 a des retours forward
> *supérieurs* (+0,50 %) — la continuation domine, le contrarian de la
> littérature carry ne survit pas. Verdicts antérieurs de la famille
> (machine distante) : directionnel cross-exchange REJECTED, carry
> portefeuille NON_VALIDATED (churn/fees ×3,5), **STRESS_GATE =
> VALIDATED_SIGNAL** (parqué, non câblé) — **statut supplanté ci-dessus**.
> Exploratoire : premium très bas → poursuite de la baisse (−1,2 à
> −1,5 % ETH/SOL à 24 h).

Deux usages distincts, à évaluer séparément :

1. **Carry delta-neutre** : long spot / short perp quand le funding est
   élevé (ou l'inverse), PnL = funding encaissé − coûts − risque de basis.
2. **Filtre de stress** : l'élargissement simultané des primes
   inter-exchange et du basis est un marqueur de stress de liquidité →
   overlay risk-off pour les autres moteurs.

Horizon : minutes – jours.

## Ce que dit la recherche

- Makarov & Schoar, *Trading and Arbitrage in Cryptocurrency Markets*
  (JFE, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3171204) :
  déviations de prix inter-exchanges **larges, récurrentes, persistantes**
  (heures/jours), qui co-varient et s'ouvrent pendant les phases
  d'appréciation ; les coûts de transaction seuls ne les expliquent pas.
  Le volume signé se décompose en composante commune (~80 % des retours
  BTC) + idiosyncratique (explique les spreads inter-exchanges).
- *The Crypto Carry Trade* (Christin et al., CMU,
  https://www.andrew.cmu.edu/user/azj/files/CarryTrade.v1.0.pdf) : le carry
  crypto est historiquement élevé mais la volatilité du funding est forte ;
  **funding élevé coïncide avec les sommets locaux et précède les
  corrections** (positionnement long leveragé instable) — le funding est
  donc aussi un signal directionnel contrarian, pas seulement un revenu.
- *Exploring Risk and Return Profiles of Funding Rate Arbitrage on CEX and
  DEX* (https://www.sciencedirect.com/science/article/pii/S2096720925000818) :
  l'arb de funding est **décorrélé du HODL** (bon candidat diversification),
  mais le rendement net dépend du venue et des coûts réels.
- *Perpetual Futures and Basis Risk* (AEA 2026,
  https://www.aeaweb.org/conference/2026/program/paper/ByyFEfr4) : en
  crise, les liquidations génèrent des **spikes de basis négatifs à
  récupération lente** — le carry « sans risque » perd précisément quand
  tout le reste perd. Dimensionner en conséquence.
- Fondements mécaniques du perp : https://arxiv.org/abs/2212.06888.

## Sous-signaux à tester

1. **Carry conditionnel** : delta-neutre uniquement si funding annualisé >
   seuil ET basis stable ET profondeur suffisante des deux jambes.
2. **Funding extrême comme fade** : funding > p95 + OI en hausse = filtre
   défavorable pour les longs des autres pistes (croise la piste 2).
3. **Prime Binance–Bybit–Hyperliquid** : z-score de la prime perp par
   venue ; élargissement = stress, lead-lag possible du venue directeur.
4. **Basis spot-perp Binance** : z-score intrajournalier ; spikes négatifs
   = capitulation (croise liquidation_exhaustion).

## Données

- ✅ `binance_futures_funding` (8 h) et `bybit_funding` (8 h) archivés.
- ✅ Klines spot + perp Binance Vision → basis spot-perp reconstructible
  en 1 m sur tout l'historique.
- À ajouter : funding + mark price Hyperliquid (API info publique, même
  collecteur que la piste 5) ; `premiumIndex` Binance en 5 m (archivable
  via `bin/archive-derivs`).

## Protocole de rejet

- Carry : PnL net positif avec coûts ×2 **et** slippage des deux jambes,
  après stress-test sur les fenêtres de spikes de basis (mars 2020,
  mai 2021, nov. 2022, oct. 2025).
- Filtre de stress : doit améliorer le Sharpe du portefeuille combiné
  (contribution marginale), pas seulement avoir une belle corrélation
  avec les drawdowns passés.
