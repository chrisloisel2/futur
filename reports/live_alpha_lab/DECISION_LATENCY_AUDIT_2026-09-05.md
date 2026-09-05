# Audit de latence de décision — Live Alpha Lab

Date : 2026-09-05. Déclencheur : la variante `LIQ_CASCADE_REPEAT_SYSTEMIC_V1`
(déployée le 2026-09-03 depuis le candidat validé `LIQ_REPEAT_DENSITY`)
affichait 0 décision forward alors que son parent en avait 12. La vérification
du filtre de densité a été concluante — le filtre fonctionne — mais elle a
exposé une cause bien plus large.

## 1. Le constat

Pour chaque alpha, `decided_at − <temps de l'événement>` sur les seules
décisions `FORWARD_LIVE`, comparé à son propre horizon de détention :

| alpha | forward | lag médian | horizon | périmées à l'arrivée |
|---|---|---|---|---|
| SHORT_COVERING_CONTINUATION_V1 | 360 | 2,7 h | 4 h | **160/360 (44 %)** |
| WHALE_LSR_SCREEN_V1 (gate) | 262 | 8,8 h | 24 h | 52/262 (20 %) |
| LIQ_CASCADE_FAR_FROM_LOW_V1 | 22 | 47,5 h | 4 h | **22/22 (100 %)** |
| LIQ_CASCADE_REPEAT_V1 | 12 | 45,5 h | 4 h | **12/12 (100 %)** |
| VOL_FORECAST_LAYER_V1 (overlay) | 5 | 8,3 h | 24 h | 2/5 |

« Périmée à l'arrivée » = le lab apprend l'événement après l'expiration de
l'horizon de l'alpha. Aucun capital ne peut être engagé sur ces décisions, quel
que soit le mérite du signal.

**La famille cascade de liquidation est à 100 %.** Le retard minimum observé y
est de 33,6 h — soit huit fois son horizon. Ce n'est pas un creux de marché ni
une rareté d'événements : c'est une impossibilité d'architecture.

## 2. Cause racine

Le détecteur figé lit `data/derivatives_backfill/binance_vision_metrics/*_metrics_5m.parquet`
(`detector.py::METRICS_DIR`). C'est un **backfill d'archives quotidiennes Binance
Vision**, pas un flux live. Vérifié ce jour : les 50 symboles de l'univers figé
s'arrêtent au **2026-09-02 23:55**, alors que les fichiers ont été réécrits le
2026-09-04 08:30 — le producteur tourne, mais la source publie avec 1 à 2 jours
de décalage.

Quatre alphas figés en dépendent : `LIQ_CASCADE_REPEAT_V1`,
`LIQ_CASCADE_REPEAT_SYSTEMIC_V1`, `LIQ_CASCADE_FAR_FROM_LOW_V1`,
`BTC_LEAD_ALT_CASCADE_V1`.

Le reste du laboratoire n'est pas concerné : les autres familles lisent des
sources fraîches (`SHORT_COVERING` a déclenché il y a 0,2 h).

Note distincte, relevée au passage : hors de l'univers tradé, la même
arborescence est bien plus périmée (échantillon `0GUSDT`, `ZRXUSDT` : arrêt au
2026-08-12, mtime 2026-08-14). Seuls les 50 symboles de l'univers figé sont
rafraîchis. Sans effet sur les alphas actuels, mais tout élargissement d'univers
buterait dessus.

## 3. Ce que ça invalide, et ce que ça n'invalide pas

**Ça n'invalide pas** la preuve de signal. Les décisions restent causales, PIT,
correctement étiquetées, et le compteur forward mesure bien ce qu'il prétend :
le mécanisme se déclenche-t-il, à quelle fréquence, dans quel régime.

**Ça invalide** toute lecture de ces alphas comme « en paper trading ». Ils
n'ont jamais pu prendre une position et ne le pourront pas dans cette
architecture. Les 5 portefeuilles à plat côté liquidation ne sont donc pas
seulement expliqués par les portes de capital du 2026-09-04 : même capital
accordé, les intents arrivent expirés.

Corollaire sur `SHORT_COVERING_CONTINUATION_V1`, le seul alpha à confiance
`MEANINGFUL` (85 épisodes indépendants) : 44 % de ses décisions sont périmées, et
les 56 % restantes n'ont plus qu'environ 1,3 h de leur horizon de 4 h. Son PnL
paper, quand il sera calculé, portera sur une fenêtre tronquée — pas sur la
stratégie telle que spécifiée.

## 4. Options — et pourquoi le raccordement naïf est refusé

Le collecteur live `futur-derivatives.service` écrit bien un flux d'OI frais
(`data/derivatives_raw/.../stream=open_interest`, 47 des 50 symboles de
l'univers, profondeur 66 jours, dernier point à quelques minutes). Le
raccorder semble évident. Mesuré sur la période de recouvrement (2 784 barres
5 m communes, BTCUSDT), il ne l'est pas :

- **Le prix n'est pas défini pareil.** Le détecteur utilise `px = sum_open_interest_value / sum_open_interest` (prix implicite Vision) ; le flux live n'expose que `mark_price`. Écart médian 3,7 bps, **p95 20,0 bps, max 108,6 bps**. `px_ret_30m` fait partie du déclencheur figé : greffer une autre définition de prix sur la queue de la série changerait ce que le seuil signifie, sans changer le code — exactement la dérive de spec silencieuse que la discipline du registre interdit.
- **L'échantillonnage n'est pas aligné.** Vision donne des barres 5 m fermées ; le live est un sondage à cadence médiane 355 s à des offsets arbitraires. Écart d'OI médian 2,1 bps, p95 12,2 bps.
- **Le flux live a ses propres trous** : intervalle maximum observé de 48 h sur la fenêtre examinée.
- **3 symboles manquants** (`MKRUSDT`, `PEPEUSDT`, `RNDRUSDT`) — le décalage de nommage déjà connu (renommages Binance vers `1000PEPEUSDT` / `RENDERUSDT`).

Les trois voies honnêtes, par ordre de coût croissant :

1. **Acter le statut.** Ces alphas restent des instruments de preuve de signal, pas des candidats au capital. Coût nul, mais la Validation Factory continuerait d'alimenter une famille non déployable.
2. **Collecter la vraie source live.** Binance expose `/futures/data/openInterestHist?period=5m`, qui renvoie exactement `sumOpenInterest` et `sumOpenInterestValue` — les deux colonnes dont le détecteur a besoin, à la même granularité et avec la même définition que Vision. Un collecteur dédié supprimerait le décalage sans toucher à la spec figée. C'est la seule option qui rend la famille exécutable **à spec inchangée**. À chiffrer : quota API, volume disque, et une frontière de segment de données à déclarer au basculement.
3. **Re-figer sur le flux existant.** Accepter `mark_price`, donc de nouveaux `alpha_id`, nouveaux freeze, track record à zéro. Le plus rapide à écrire, mais jette la preuve forward déjà accumulée et rouvre la question de la validation.

**Recommandation : option 2**, parce qu'elle est la seule qui préserve à la fois
la spec figée et la preuve déjà accumulée. Elle n'a pas été engagée dans cette
session : c'est un nouveau collecteur en production, sur une machine dont le
disque et la RAM sont déjà contraints — la décision revient à l'utilisateur.

## 5. Ce qui a été fait

- `decision_lag_med_h` et `expired_on_arrival` ajoutés au scoreboard : la mesure est désormais permanente et visible à chaque cycle, plus un constat ponctuel.
- Angle mort corrigé : `AMIHUD_ILLIQUIDITY_PREMIUM_V1` et `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` étaient absents de `_TIME_COL`/`_SYMBOL_COL` depuis leur déploiement — toutes leurs métriques temporelles sortaient vides, sans erreur. Elles sont maintenant renseignées (67,0 h et 46,0 h d'âge forward).
- `tests/test_scoreboard_executability.py` : 10 cas verrouillant les deux régressions, dont le fait qu'une latence non mesurable renvoie `None` et jamais `0` (« pas mesuré » ≠ « instantané »).
