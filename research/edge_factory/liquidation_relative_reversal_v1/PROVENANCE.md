# Provenance — liquidation_relative_reversal_v1

Source exacte, méthode de collecte, limites connues et commande de
reproduction pour chaque type de donnée inventorié dans `DATA_INVENTORY.yaml`.
Toutes les vérifications ci-dessous ont été faites en lecture seule sur
`qbee@100.127.59.114:~/futur` le 2026-07-22.

## Perp 5m OHLCV

- **Source publique** : `data.binance.vision/data/futures/um/daily/klines/{SYM}/1m/`
  (archives ZIP quotidiennes 1 minute, resamplables à 5m).
- **Collecteur existant, jamais exécuté pour cet univers** :
  `hedge_fund/hf_crypto_dataset/collectors/binance.py::futures_klines`.
- **Ce qui existe déjà à la place** : `data/enriched/*_1h_enriched.parquet`
  (1h, 50 symboles, colonnes `taker_buy_*` = placeholder `qv/2` confirmé —
  ne pas utiliser comme volume agressif réel) et
  `data/listings_backfill/binance/klines_5m/` (5m réel mais seulement ±1,5j
  autour de la date de listing — inutilisable en dehors de l'étude de
  listing pour laquelle il a été construit).
- **Limite connue** : aucune, hors coût de calcul/stockage (312 symboles ×
  plusieurs années de fichiers 1m).

## aggTrades

- **Source publique** : `data.binance.vision/data/futures/um/daily/aggTrades/{SYM}/`.
- **Collecteur existant, jamais exécuté** :
  `BinanceCollector.binance_vision_daily_agg_trades` +
  `read_agg_zip`/`aggregate_trades_1m` (même fichier que ci-dessus).
- **Ce qui existe déjà** : rien sur disque, recherche exhaustive faite.
- **Limite connue** : aucune (gratuit, sans clé), coût réel = beaucoup de
  petits fichiers quotidiens × 312 symboles, agrégation 1m coûteuse en CPU
  avant tout roulement à 5m.

## Spot / index 5m

- **Source publique** : archive spot Binance Vision (méthode `spot_klines()`
  du même collecteur, jamais exécutée).
- **Ce qui existe déjà** : rien. Confirme le constat déjà fait pour
  `calendar_basis_v1` (le backtest `basis_term_v0` substitue déjà le close
  perp comme proxy spot, faute de vraie série).

## Mark price 5m

- **Ce qui existe** : `data/derivatives_backfill/binance_vision_premium/`
  — série 5m réelle mais de PRIME (ratio), pas de prix mark absolu ; source
  `data.binance.vision/.../premiumIndexKlines/` vraisemblablement (à
  confirmer si ce flux est un jour utilisé directement — non vérifié ici,
  seul le fichier local a été lu).
- **Mark price absolu réel** : uniquement dans
  `data/derivatives_backfill/binance/funding/*.parquet` (colonne
  `mark_price`), cadence 8h, peuplé seulement depuis le 2023-10-31.
- **Limite connue** : aucune source publique gratuite connue de mark price
  absolu à 5m sur un historique long n'a été identifiée dans cet inventaire
  — à revérifier si cette piste avance, ne pas supposer qu'elle n'existe
  pas ailleurs sans avoir cherché spécifiquement l'endpoint
  `premiumIndexKlines` en klines classiques (pas seulement l'archive
  utilisée ici).

## Open interest 5m

- **Source publique, la bonne** : `data.binance.vision/data/futures/um/daily/metrics/{SYM}/`
  — confirmé par curl direct servir TOUS les symboles usdm historiquement
  (testé sur ACTUSDT/AEVOUSDT/XLMUSDT, non encore ingérés localement,
  HTTP 200 dans les trois cas).
- **Collecteur existant, à ré-exécuter sur le reste de l'univers** :
  `scripts/backfill_binance_metrics_vision.py`.
- **Source REST alternative, plafonnée par l'API elle-même** :
  `fapi/v1/openInterestHist` — confirmé (code + comportement observé) que
  la rétention est ~30 jours quel que soit `period`/`limit` demandé ; ce
  n'est pas un manque de collecte, c'est une limite documentée de
  l'endpoint. Ne pas essayer de la contourner en redemandant plus souvent.
- **Bonus** : poll live `data/derivatives_raw/.../stream=open_interest/`
  (8 symboles, depuis le 2026-06-28) regroupe mark/index/funding en même
  temps que l'OI — utile pour le signal de divergence perp/index sur ces
  8 noms précis, pas un substitut à `binance_vision_metrics` pour le reste.

## Funding

- **Source** : `fapi/v1/fundingRate` (déjà utilisée par
  `scripts/backfill_binance_derivatives_free.py`, réutilisé cette session
  pour funding RV et momentum).
- **Limite connue** : réglé au settlement (00:00/08:00/16:00 UTC), jamais
  continu — voir la note méthodologique dans `DATA_INVENTORY.yaml`.

## Liquidations déclarées

- **Binance Vision** (`data.binance.vision/.../liquidationSnapshot/`) :
  cm uniquement, jamais usdm ; publication arrêtée le 2024-10-14
  (re-vérifié par curl direct sur trois dates récentes, 404 dans les trois
  cas — la coupure n'a pas été levée).
- **Bybit** : websocket public v5 `allLiquidation.{symbol}` (le flux
  poussé toutes les 500 ms depuis 2025-02, qui a remplacé l'ancien flux
  d'1 liquidation/seconde déprécié — confirmé contre la documentation
  officielle Bybit). Collecte live tournant depuis le 2026-07-04 sur cette
  machine, pas d'archive REST historique publique connue au-delà de cette
  fenêtre (vérifié par recherche : aucun endpoint REST bybit ne sert
  d'historique de liquidations, seul OHLCV/trades classique existe en
  historique).
- **OKX** : polling REST des ordres de liquidation, même fenêtre de
  collecte (depuis le 2026-07-04), même limite d'historique.
- **Binance live** (`!forceOrder@arr`) : codé mais non fonctionnel depuis
  cette machine — diagnostic du 2026-07-03 déjà présent dans le
  collecteur : la connexion websocket est acceptée par
  `fstream.binance.com` mais aucune donnée n'est jamais poussée (blocage
  géographique silencieux, pas un problème de données).
- **Historique payant identifié, non acquis** : Tardis.dev (depuis
  2020-12-18) et Amberdata (depuis 2021-09) vendent un historique de
  liquidations multi-venues plus profond que ce que ce dépôt collecte
  gratuitement. Coût d'acquisition non évalué ici — décision séparée si
  jamais nécessaire.

## Listing / delisting

Réutilise tel quel le travail déjà fait et vérifié pour
`cross_sectional_momentum_v1` (787 symboles, délistements réels
spot-vérifiés) — voir son propre `DATA_INVENTORY.yaml`, pas re-vérifié ici
pour éviter un travail redondant.

## Fees, tick size, lot size

- `fapi/v1/exchangeInfo` (public, sans clé) donne les valeurs ACTUELLES de
  `PRICE_FILTER.tickSize`, `LOT_SIZE.stepSize` et `liquidationFee` par
  symbole — pas d'archive historique connue de ces valeurs.
- Frais réels (maker/taker par compte) nécessitent une clé API signée
  (`fee_registry.fetch_binance_commission`) — aucune disponible ; les
  défauts assumés de `configs/alpha20.yaml` restent la seule figure
  utilisable, comme pour toutes les autres pistes cette session.
