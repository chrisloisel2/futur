# EVENT_TAPE_CATALOG — tape officielle des événements de marché

*Généré le 2026-09-10T11:06:41+00:00 depuis `data_lake/events/official_event_tape.jsonl` (sha256 `6e957851e452395f…`), 7449 enregistrements, schéma v1.*

## Ce que c'est

Un enregistrement par (source, annonce, actif). Trois horodatages qui ne se confondent jamais : `publication_ts_exchange` (déclaré par la venue), `first_seen_ts_local` (ce collecteur), `trading_start_ts` (si le texte le dit). Chaque ligne porte `raw_url`, `raw_title`, `raw_body_hash` (hash de l'enregistrement API, **pas** du corps HTML — voir limites).

## Sources — officielles seulement

| source | type d'accès | historique | enregistrements |
|---|---|---|---|
| binance | CMS `bapi` articles (catalog 48 New Listings, 161 Delisting) | oui, paginé | 2951 |
| okx | `GET /api/v5/support/announcements` (new-listings, delistings, trading-updates) | oui, paginé | 884 |
| bybit | `GET /v5/announcements/index` (new_crypto, delistings, product_updates) | oui, paginé | 2543 |
| coinbase | `GET /products` (status, auction_mode, post_only, limit_only, cancel_only) | **snapshot + diff** (first_seen seul) | 837 |
| hyperliquid | `POST /info meta` (univers, isDelisted, maxLeverage) | **snapshot + diff** (first_seen seul) | 234 |

Plage de publication (historiques) : **2017-07-21 → 2026-09-10**. Part backfillée : 86% (la latence ne se mesure que sur ce qu'on voit arriver).

## Par source et type

| source | type | n |
|---|---|---|
| binance | alpha | 59 |
| binance | delisting | 512 |
| binance | futures_delisting | 90 |
| binance | futures_listing | 610 |
| binance | launchpool | 64 |
| binance | listing | 1469 |
| binance | product_add | 147 |
| bybit | delisting | 272 |
| bybit | futures_delisting | 274 |
| bybit | futures_listing | 824 |
| bybit | launchpool | 19 |
| bybit | listing | 920 |
| bybit | other | 229 |
| bybit | suspension | 3 |
| bybit | tick_size_change | 2 |
| coinbase | snapshot_baseline | 837 |
| hyperliquid | snapshot_baseline | 234 |
| okx | delisting | 21 |
| okx | futures_delisting | 8 |
| okx | futures_listing | 68 |
| okx | listing | 355 |
| okx | other | 372 |
| okx | suspension | 24 |
| okx | tick_size_change | 36 |

## Par source et année de publication (historiques)

| source | année | n |
|---|---|---|
| binance | 2017 | 108 |
| binance | 2018 | 89 |
| binance | 2019 | 108 |
| binance | 2020 | 360 |
| binance | 2021 | 278 |
| binance | 2022 | 265 |
| binance | 2023 | 397 |
| binance | 2024 | 421 |
| binance | 2025 | 552 |
| binance | 2026 | 373 |
| bybit | 2022 | 245 |
| bybit | 2023 | 363 |
| bybit | 2024 | 594 |
| bybit | 2025 | 741 |
| bybit | 2026 | 600 |
| okx | 2023 | 490 |
| okx | 2024 | 153 |
| okx | 2025 | 174 |
| okx | 2026 | 67 |

## Qualité du parsing

- actif identifié : **4465/7449** · symbole identifié : **3095/7449** · `trading_start_ts` extrait : **889/6378** (historiques)
- le classement est une fonction pure de `raw_title` (`--reclassify` le refait sans retélécharger) ; `event_type = other` signale un titre non reconnu, jamais un événement perdu

## Limites déclarées

- `raw_body_hash` hache l'enregistrement API (titre, code, dates), pas le corps HTML : le corps n'est pas encore collecté
- Coinbase et Hyperliquid n'ont pas d'historique d'annonces exploitable : la première passe est une **baseline** (`snapshot_baseline`), les vrais événements naissent des diffs à partir de maintenant, avec `publication_ts_exchange = null`
- Binance CMS : `releaseDate` est l'horodatage de publication de l'article ; la première ouverture de marché est dans le texte quand elle y est (`trading_start_ts`), sinon `null` — jamais devinée
- aucune jointure prix n'est faite ici : c'est le rôle de `mechanisms/event_reaction_v1`, qui n'a **pas** encore tourné (RESEARCH_ONLY, aucun essai débité)

## Hypothèses testables (brut visé ≥ 10 bps, écrites avant tout regard)

- fuite pré-annonce : rendement de l'actif entre −60 min et la publication, vs univers
- réaction retardée sur petites venues : même actif, écart Binance → OKX/Bybit après publication
- listing perp : spot → perp au `trading_start_ts`, première minute / première heure
- pression de delisting : de la publication à la date de retrait
- pump initial puis retour à la moyenne : +1 h → +6 h
- déséquilibre d'enchère (Coinbase `auction_mode`) : aucun cas observé dans la baseline (837 produits, 0 en auction)
