# FORCED_FLOW_DATA_READINESS — la tape des flux forcés (P2B)

*2026-09-10. Service `futur-forced-flow-tape.service` (systemd utilisateur, `Restart=always`), premier enregistrement à None. Chiffres de la première fenêtre de collecte ; se régénèrent avec `data_lake/collectors/tape_io.read_tape`.*

## Ce que la tape enregistre

Une ligne par liquidation : `event_ts_exchange` (T de l'ordre forcé), `recv_ts_local`, `latency_ms`, côté forcé et position liquidée, prix, quantité, notional — et **ce que le marché disait avant** : `spread_before_bps`, `book_imbalance_before` (BBO), `mark_price`, `index_price`, `funding_rate`, `open_interest` (Bybit), chacun avec son âge (`bbo_age_ms`, `mark_age_ms`) **≥ 0 par construction** — la valeur retenue est la dernière dont l'horodatage précède la liquidation (tampon 15 s). Les rendements 5 s / 30 s / 5 m / 30 m sont `null` : le collecteur ne regarde jamais l'avenir ; la jointure hors ligne le fera.

## Sources et limites déclarées

| venue | flux | limite connue |
|---|---|---|
| binance | `!forceOrder@arr` + `!markPrice@arr@1s` (`/market/ws`), `!bookTicker` (`/public/ws`) — le chemin standard `/ws/` est muet sur cet hôte | **au plus une liquidation par symbole et par seconde** (la dernière) : le notional agrégé est une **borne basse** ; `stream_delay_ms` mesure ce throttle |
| bybit | `allLiquidation.<sym>` + `tickers.<sym>` sur les 60 premiers linear par turnover, validés contre `instruments-info` | agrégation par seconde ; `open_interest` présent |
| hyperliquid | — | aucun flux public de liquidations sans contexte utilisateur (`userEvents`) : absent en v1 |
| profondeur | — | `depth_10bps_before = null` : aucun flux de profondeur souscrit (BBO seulement) |

## Première fenêtre

| mesure | valeur |
|---|---|
| liquidations enregistrées | **0** ({}) |
| enrichies (spread d'avant non nul) | 0/0 |
| latence exchange → local, ms (n / min / méd / max) | — |
| throttle Binance `stream_delay_ms` (n / min / méd / max) | — |
| âge de la cote d'avant, ms (n / min / méd / max) | — |
| âge du mark d'avant, ms (n / min / méd / max) | — |
| notional | — |
| liquidations ≥ 50 k USD (seuil d'entrée de `forced_liquidation_reaction_v1`) | 0 |

## Ce qu'il faut avant le premier regard

- ≥ **300 événements indépendants** (`complete_link`, 5 min) au-dessus du seuil de notional, avec `bbo_age_ms ≥ 0` — écrit dans la spec
- la jointure rendements (5 s → 30 m) contre `microstructure_reduced` (Binance BTC/ETH/SOL) et, pour les autres symboles, contre des klines 1 m à backfiller
- la partition de l'heure courante n'a pas de marqueur de fin gzip : la lire avec `tape_io.read_tape` (tolérant), jamais `gzip.open`

Critère dur, inchangé : on ne garde que les événements dont le mouvement brut moyen attendu dépasse **3 × coût** (P1 : 9,5-11,3 bps aller-retour au VIP0 → ≥ 30 bps bruts).
