# Piste 4 — Flux on-chain (branche séparée)

Horizon : 1–6 h. Des travaux out-of-sample trouvent une information
prédictive différenciée dans les flux ETH, BTC et USDT
(https://arxiv.org/abs/2411.06327).

## Sous-signaux

- **ETH → exchanges** : pression vendeuse ; filtre d'entrée pour les longs.
- **USDT → exchanges** : pouvoir d'achat disponible ; signal risk-on.
- **Whales/vaults** : accumulation, distribution, rotation.
- **Divergence prix vs flux** : prix monte pendant que les flux se dégradent.

## Données existantes à auditer d'abord

Le repo collecte déjà via `scrapers/` : Whale Alert (> $500K, MongoDB
`whale_data.transactions`), Arkham, Etherscan, Mempool.space
(S3 `qbia/bourse/raw/`). Avant tout nouveau collecteur :

1. auditer couverture/trous des collections Mongo existantes ;
2. normaliser vers le lake parquet (`data_pipeline.storage`) ;
3. étiqueter les adresses d'exchanges (inflow/outflow net par heure).
