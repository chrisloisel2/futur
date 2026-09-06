# Piste 4 — Flux on-chain (branche séparée)

> ## ⚠️ VERDICT : NOT_TESTABLE — données absentes (2026-07-17, commit distant `53d6f70`)
>
> Audit machine distante : `whale_data.whale_transactions` et
> `trader.whale_transactions` **vides** (0 docs), aucun parquet on-chain.
> Les scrapers n'ont jamais tourné en production. Pas de backfill gratuit
> (flux exchange = payant chez tous les fournisseurs). **Décision à
> prendre : collecteur live (rééval à 60-90 j) ou drop de la piste.**

Horizon : 1–6 h. Des travaux out-of-sample trouvent une information
prédictive différenciée dans les flux ETH, BTC et USDT
(https://arxiv.org/abs/2411.06327).

## Résultats détaillés du papier de référence (2411.06327)

Sur des intervalles intraday 1–6 h :

- **USDT → exchanges** : prédit **positivement** les retours BTC et ETH à
  plusieurs horizons ; prédit négativement la vol ETH (et la vol BTC à 6 h).
  C'est le sous-signal le plus robuste du papier.
- **ETH → exchanges** : prédit **négativement** les retours ET la
  volatilité ETH sur *tous* les horizons intraday.
- **BTC → exchanges** : quasi pas de pouvoir prédictif sur les retours
  (sauf 4 h), mais associé négativement à la vol — le flux BTC est un
  signal de vol, pas de direction. Ne pas le forcer en directionnel.

Complément : les transactions whales (Whale Alert + CryptoQuant) aident à
prévoir les **spikes de volatilité** BTC (transformer,
https://arxiv.org/abs/2211.08281) — cohérent avec un usage
« régime/vol » des cohortes whales plutôt que directionnel naïf.

## Sous-signaux

- **USDT → exchanges** : pouvoir d'achat disponible ; signal risk-on
  (priorité 1, le mieux documenté).
- **ETH → exchanges** : pression vendeuse ; filtre d'entrée pour les longs
  (priorité 2).
- **BTC → exchanges** : prédicteur de volatilité, pas de direction —
  alimenter le sizing, pas le sens.
- **Whales/vaults** : accumulation, distribution, rotation ; cohortes par
  taille (le signal whale est surtout un signal de vol).
- **Divergence prix vs flux** : prix monte pendant que les flux se dégradent.

## Données existantes à auditer d'abord

Le repo collecte déjà via `scrapers/` : Whale Alert (> $500K, MongoDB
`whale_data.transactions`), Arkham, Etherscan, Mempool.space
(S3 `qbia/bourse/raw/`). Avant tout nouveau collecteur :

1. auditer couverture/trous des collections Mongo existantes ;
2. normaliser vers le lake parquet (`data_pipeline.storage`) ;
3. étiqueter les adresses d'exchanges (inflow/outflow net par heure).
