# Protocole PRÉ-ENREGISTRÉ — hyperliquid_metaorders v0 (collecte locale, sans AWS)

Date de gel : 2026-07-18, AVANT toute analyse. Aucune statistique signal→cible
ne sera calculée avant les seuils de collecte ci-dessous. Aucun AWS/S3/credential
cloud n'est utilisé.

## 1. Probe des endpoints publics (OBSERVÉ 2026-07-18, codes HTTP réels)

| Requête | Code | Contenu |
|---|---|---|
| POST `api.hyperliquid.xyz/info` `{"type":"meta"}` | 200 | univers (nom, szDecimals, maxLeverage) |
| `{"type":"metaAndAssetCtxs"}` | 200 | funding, openInterest, premium, oraclePx, markPx, dayNtlVlm par asset |
| `{"type":"l2Book","coin":"BTC"}` | 200 | carnet niveaux `{px,sz,n}` bids/asks + timestamp |
| `{"type":"recentTrades","coin":"BTC"}` | 200 | trades avec `users` = **adresses des 2 contreparties**, `tid`, `hash` |
| `{"type":"twapHistory"}` (sans user) | 422 | type existant mais **user-scopé** |
| `{"type":"twapHistory","user":<addr>}` | 200 | `[]` ou liste des TWAPs de l'adresse |
| `{"type":"userTwapSliceFills","user":<addr>}` | 200 | fills de slices TWAP de l'adresse |
| `{"type":"candleSnapshot",…}` | 200 | klines (lookback ~5000 barres seulement) |
| `{"type":"predictedFundings"}` | 200 | funding croisé Binance/Bybit/HL par asset |
| WS `wss://api.hyperliquid.xyz/ws` sub `trades` | OK | tape temps réel, même schéma que recentTrades |

**Conséquence factuelle** : il n'existe PAS de flux global public des TWAPs.
La vérité terrain est reconstruite en deux temps : (1) la tape publique expose
les adresses ; (2) `twapHistory(user)` confirme les TWAPs d'une adresse
candidate. L'horloge de l'historique démarre à la mise en service du collecteur
(aucun historique tick rétroactif gratuit).

## 2. Collecteur local (`scripts/hl_metaorders_collector.py`)

- WS `trades` sur 12 coins (BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, BNB,
  LTC, SUI, HYPE) — dédup `tid`, adresses buyer/seller conservées.
- REST : `l2Book` top-5 (spread, profondeur, imbalance) toutes les 20 s/coin ;
  `metaAndAssetCtxs` toutes les 60 s (funding/premium/OI/volume/mark/oracle).
- Détection candidats métaordre : ≥ 4 fills même (user, coin, side) sur 15 min
  avec espacement médian 15-60 s (les slices TWAP HL partent ~toutes les 30 s)
  → confirmation `twapHistory(user)` (cooldown 10 min/user), id = (user, coin,
  start_ms).
- Écriture locale **append-only** partitionnée :
  `data/hyperliquid/{trades,l2,ctxs,twap}/date=YYYY-MM-DD/part-<ms>.parquet` ;
  reprise sans perte (les parts ne sont jamais réécrites), dédup de lecture
  par clé métier (`read_table`). État/fraîcheur/trous : `data/hyperliquid/state.json`.
- UTC partout, `schema_v=1`, `source` sur chaque ligne ; logs sobres
  `reports/hl_collector.log` ; arrêt propre SIGTERM avec flush final.
- Service : `deploy/systemd/futur-hl-collector.service` (Restart=always).
  Machine allumée en continu ; alternative cron documentée :
  `@reboot /home/qbee/futur/.venv/bin/python /home/qbee/futur/scripts/hl_metaorders_collector.py >> /home/qbee/futur/reports/hl_collector.log 2>&1`
- Tests unitaires (parseur, dédup, reprise) : `tests/test_hl_collector.py` (7).
- Aucun secret nulle part (endpoints publics non authentifiés).

## 3. Hypothèses exclusives (figées avant collecte)

- **H1 continuation** : pendant et immédiatement après un métaordre public
  (TWAP confirmé), le prix continue dans le sens du métaordre (pression
  d'exécution + information).
- **H2 reversion** : après ÉPUISEMENT du métaordre (fin d'exécution), le prix
  revient (pression temporaire absorbée).

H1 et H2 sont mutuellement exclusives sur le même horizon ; une seule peut
être retenue.

## 4. Protocole d'évaluation (figé)

- Événement : TWAP confirmé via `twapHistory`, taille exécutée ≥ 50 000 USD.
  t_fin = fin d'exécution (status terminé ou executedSz plateau).
- Décision à t (H1 : t = détection ; H2 : t = t_fin) ; **exécution simulée à
  t+60 s minimum au mid du carnet** (données l2 collectées), jamais au prix de
  l'événement.
- Coûts aller-retour : 40 bps (coûts ×2 inclus). Horizons fixes : 1 h, 4 h, 24 h.
- Ne RIEN analyser avant : **≥ 30 jours de collecte ET ≥ 300 métaordres
  ÉLIGIBLES sur ≥ 10 symboles**. Éligible = TWAP confirmé dont la fenêtre
  d'exécution chevauche la période de collecte (snapshots l2/prix disponibles).
  Découverte du smoke-run 2026-07-18 : `twapHistory(user)` renvoie l'historique
  COMPLET de l'adresse (jusqu'à ~2 000 TWAPs anciens) — ces lignes rétroactives
  sont archivées mais NE COMPTENT PAS pour le seuil ni pour le test (pas de
  carnet contemporain → pas d'exécution simulée honnête).
- PASS seulement si UNE hypothèse satisfait, sur UN horizon préfixé :
  médiane nette ≥ +25 bps/événement, p < 0,01 (block-bootstrap par blocs
  d'événements contigus), même signe sur les deux moitiés chronologiques de
  la collecte. Sinon `NO_EDGE`.
- Aucun re-tuning des seuils de détection après lecture des résultats ; toute
  variante = nouveau protocole.

## 5. Limites honnêtes

- Biais de découverte : seuls les TWAPs détectables par la règle de slicing
  sont confirmés (les métaordres discrétionnaires/iceberg échappent au
  détecteur) — l'univers testé est « TWAPs publics détectés », pas « tous les
  métaordres ».
- `recentTrades`/WS ne donnent pas la profondeur historique : tout redémarrage
  prolongé du collecteur crée un trou irrécupérable (suivi dans state.json).
- Rate-limits publics HL : l2 20 s et cooldown twapHistory 10 min/user sont
  calibrés pour rester très en-dessous ; à surveiller dans les logs.
- Pas de simulation d'impact au-delà du spread/mid collecté (taille de papier
  petite par hypothèse).
