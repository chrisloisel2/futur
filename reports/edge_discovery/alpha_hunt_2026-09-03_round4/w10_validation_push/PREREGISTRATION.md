# W10_VALIDATION_PUSH — PRÉENREGISTREMENT

**Écrit AVANT tout test.** Round 4 (2026-09-03), axe : débloquer/trancher les candidats en
suspens du `validation_registry`. Aucune découverte de nouveau mécanisme.

Horodatage de rédaction : 2026-09-03 (avant exécution de tout script `evidence/`).

---

## Règles générales que je m'impose

1. **Aucun choix de signe fondé sur le PnL.** Interdit explicite du projet (registre live,
   note `LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1`). La convention de signe est tranchée
   par la **sémantique des données brutes** (qui est liquidé, dans quel sens le flux forcé
   pousse le prix), décidée AVANT de regarder le moindre rendement forward.
2. **Déclustering à 3 niveaux systématique** : L1 = même symbole / fenêtre 24h,
   L2 = jour calendaire tous symboles, L3 = épisode cross-symbole chaîné (unité macro).
3. **Coûts** : `net = gross − 14bps`, stress obligatoire `− 28bps`.
4. **ETA** : `n_required / event_rate`, `n_required` calculé sur un edge **haircuté 50 %**,
   power 80 %, alpha 5 % unilatéral, variance estimée sur les épisodes **indépendants**.
   ETA > 3 ans ⇒ `UNCONFIRMABLE_IN_HORIZON`.
5. **Écriture** : uniquement dans mon dossier. `src/`, `configs/`, `data/`,
   `reports/live_alpha_lab/` en lecture seule. Je ne modifie pas `validation_registry.yaml`.
6. Une réimplémentation historique n'est **jamais** une confirmation forward.

---

## CIBLE 1 — `LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION` (statut BLOCKED)

### Ce qui est bloqué exactement

Le détecteur figé (`src/institutional/engines/liq_cascade/detector.py:95`) étiquette un
event `SHORT_SQUEEZE` quand `px_ret_30m > 0` et `oi_drop_z ≤ −3`, `LONG_CASCADE` sinon.
Le pipeline aval (`dataset.py`, `repeat_variant.py`) utilise `fwd_4h` **brut** pour les deux
`kind` — c'est-à-dire une position **LONG** dans les deux cas ; il ne flippe jamais le PnL.
`repeat_variant.py:53` exclut explicitement `SHORT_SQUEEZE` du trading.

Deux questions ont été confondues sous le nom « convention de signe » :

- **Q1 (sémantique, factuelle)** : `kind = SHORT_SQUEEZE` (prix ↑ + OI ↓) correspond-il
  réellement à des **shorts** liquidés (flux forcé ACHETEUR) ? Question de données, réponse
  unique, vérifiable sur `forceOrder`.
- **Q2 (direction de trade, économique)** : la version « exhaustion » symétrique de
  `LIQ_CASCADE_REPEAT_V1` implique-t-elle d'être LONG ou SHORT sur un `SHORT_SQUEEZE` ?

### Hypothèses préenregistrées

**H1 (sémantique).** Dans les données brutes de liquidation, un ordre de liquidation
**SELL** est la fermeture forcée d'un **LONG**, un ordre **BUY** la fermeture forcée d'un
**SHORT**. Test : sur des barres 5-min, le déséquilibre
`imb = (short_liq_usd − long_liq_usd) / (short_liq_usd + long_liq_usd)` doit être
**positivement** corrélé au rendement contemporain du prix (achats forcés → prix ↑).

- Critère de succès : `corr(imb, ret_5m) > 0` avec `|t| > 5`, **et le même signe sur les
  trois sources indépendantes** : OKX (`posSide` explicite, non ambigu), Bybit
  (`allLiquidation`, normalisation appliquée par le collecteur), Binance Vision COIN-M
  (champ `side` brut Binance, jamais normalisé par ce projet).
- Si les trois sources s'accordent → Q1 est **TRANCHÉE** et ne peut plus être rouverte.
- Si elles se contredisent → `DATA_LIMITED` sur Q1, et la cible 1 reste bloquée pour une
  raison désormais *documentée* et non plus « ambiguë ».

**H2 (mapping détecteur → sémantique).** Les events du détecteur figé étiquetés
`SHORT_SQUEEZE` présentent, dans une fenêtre ±30 min, une dominance de liquidations de
**shorts** (`side = BUY`), et les `LONG_CASCADE` une dominance de liquidations de **longs**
(`side = SELL`). Test sur la fenêtre où les deux sources coexistent (forceOrder collecté
depuis 2026-07-04 ; events reconstruits par le détecteur figé sur
`binance_vision_metrics` jusqu'au 2026-09-01 — **reconstruction en lecture seule, aucun
fichier `src/` modifié**).

- Critère : part moyenne de liquidation short > 50 % sur `SHORT_SQUEEZE` et < 50 % sur
  `LONG_CASCADE`, écart significatif (test de proportions, |z| > 3).

**H3 (direction de trade — décidée AVANT tout PnL).** Le mécanisme économique de
`LIQ_CASCADE_REPEAT_V1` est : *après la 3ᵉ cascade sur un symbole en 24 h, le flux FORCÉ est
épuisé, donc le prix cesse d'être poussé par lui et se rétablit* → la position est prise
**contre le flux forcé** (LONG après des ventes forcées). La version **symétrique**
appliquée à `SHORT_SQUEEZE` est donc, mécaniquement et sans regarder un seul rendement :

> **SHORT_SQUEEZE_EXHAUSTION symétrique = SHORT** (contre les achats forcés).

Corollaire préenregistré : le chiffre `+40.0bps plein / +114.6bps OOS` du round 2 a été
mesuré sur `fwd_4h` **brut**, donc **LONG**, donc **AVEC** le flux forcé — ce n'est **pas**
le symétrique de l'alpha existant mais un mécanisme économique **différent**
(« continuation »). Je nomme et teste les deux séparément :

| id de test | direction | logique économique | statut a priori |
|---|---|---|---|
| `SSE_MEANREV` (symétrique) | SHORT | épuisement des achats forcés → repli | hypothèse principale |
| `SSE_CONT` (round 2 tel quel) | LONG | continuation du squeeze | hypothèse secondaire |

**Aucune des deux n'est retenue parce qu'elle paie mieux.** `SSE_MEANREV` est l'hypothèse
principale parce qu'elle est la symétrie du mécanisme déjà en shadow ; `SSE_CONT` est testée
parce que c'est le chiffre historique publié qu'il faut auditer.

**H4 (contrainte opérationnelle, préenregistrée).** Le projet est sous politique permanente
`SHORT_REJECTED` (exposition short directionnelle fermée, audit mai 2026). Donc :
- si `SSE_MEANREV` est l'edge, le verdict scientifique peut être positif mais le verdict
  **opérationnel** est `SHORT_REJECTED_INHERITED` — non déployable sans réouverture
  explicite par l'utilisateur ;
- si l'edge n'est que dans `SSE_CONT`, il faut expliquer *économiquement* pourquoi acheter
  avec le flux forcé après épuisement, sinon c'est un artefact de la dérive haussière
  inconditionnelle (règle §1.3 du briefing : comparer les bras entre eux, pas à zéro).

### Gate quantitatif (identique pour les deux directions)

Population : `liq_cascade_dataset.parquet`, `kind == SHORT_SQUEEZE`,
`repeat_bucket == exhaustion` (`n_events_sym_24h ≥ 2`), horizon `fwd_4h`, `label_full`.
Bras de comparaison obligatoire : `onset` (`n_events_sym_24h == 0`) sur la **même**
population `SHORT_SQUEEZE` — c'est le `bras_A − bras_B` du §1.3, pas un test contre zéro.

Champs produits : `n_raw`, `n_indep_L1/L2/L3`, `net_bps`, `net_bps_stress28`,
`t_stat_declustered`, `bootstrap_ci95` (block-bootstrap par épisode), `year_by_year`,
`ex_best_year`, `n_required`, `event_rate` (6 derniers mois), `eta_forward_confirmation`.

**Règle de décision préenregistrée** :
- `VALIDATED_FOR_FORWARD` si : le delta exhaustion − onset est du signe attendu, survit à
  −28bps, IC95 bootstrap déclusterisé exclut 0, pas concentré sur une seule année
  (`ex_best_year` garde le signe), ETA < 3 ans.
- `UNCONFIRMABLE_IN_HORIZON` si l'effet tient mais ETA ≥ 3 ans.
- `REJECTED` si le delta est nul/inversé ou meurt au stress ou ne survit pas à
  `ex_best_year`.
- Q1/Q2 sont tranchées **dans tous les cas** — le statut `BLOCKED` disparaît quoi qu'il
  arrive.

---

## CIBLE 2 — `LIQ_REPEAT_VOL_GATE` (NEEDS_MORE_RESEARCH)

Problème connu : le gate « vol réalisée BTC 24 h élevée » est un état **macro lent** →
949 trades gatés ≡ 268 épisodes indépendants → ETA 28–38,5 ans.

Question préenregistrée : **existe-t-il une formulation du même mécanisme économique avec
un taux d'épisodes indépendants nettement plus élevé ?**

Reformulations testées (les 3 sont décidées maintenant, avant tout résultat) :

- **R1 — vol locale par symbole.** Remplacer la vol BTC 24 h macro par la vol réalisée 24 h
  **du symbole lui-même** (`vol_24h`, déjà dans le dataset, causale). 49 symboles × états
  locaux ⇒ l'unité de déclustering devient (symbole × épisode de régime), beaucoup plus
  nombreuse qu'un état macro unique.
- **R2 — vol rapide.** Vol réalisée **courte** (fenêtre ~2 h) par symbole, qui change
  d'état plusieurs fois par semaine au lieu de plusieurs fois par mois. Proxy causal
  disponible sans recalcul : `|px_ret_1h|` et `px_accel` à l'event. Seuil : décile
  supérieur, percentile **causal trailing** par symbole.
- **R3 — intensité de l'event lui-même.** `|oi_drop_z|` (déjà causal, déjà à l'event) comme
  conditionneur : « stress » mesuré par la violence de la cascade et non par un régime
  macro. C'est l'unité la plus rapide possible : elle change à chaque event.

Seuils : décile/tercile fixés a priori (top 30 % = « stress ») pour R1/R2/R3, percentile
causal trailing 30 j par symbole quand la grandeur n'est pas déjà standardisée.
Bras de comparaison : gate ON − gate OFF sur la même population de trades
`LIQ_CASCADE_REPEAT_V1` (LONG_CASCADE exhaustion), horizon `fwd_4h`.

**Règle de décision** :
- Une reformulation est **récupérée** (`VALIDATED_FOR_FORWARD`) si delta ON−OFF > 0 net,
  survit au stress 28bps, IC95 déclusterisé exclut 0, ET **ETA < 3 ans**.
- Si aucune des trois ne descend sous 3 ans en préservant l'effet →
  `UNCONFIRMABLE_IN_HORIZON` **définitif** pour la famille, dossier clos.
- Je ne teste **pas** d'autres variantes que R1/R2/R3 ; toute quatrième idée venue après
  avoir vu les résultats serait un refit et sera déclarée telle.

---

## CIBLE 3 — Audit de second regard des 5 REJETÉS

`CROSS_SECTIONAL_MOMENTUM_CVD`, `BTC_ETH_CURVE_STEEPNESS`, `POSITIONING_TAKER_FLOW`,
`GLOBAL_ACCOUNT_LSR_FADE`, `OI_CVD_MEMORY_OVERLAP`.

**Grille de décision préenregistrée**, appliquée à chacun (audit documentaire + vérifications
ciblées, pas de re-backtest complet) :

Un rejet est classé **MECHANISM_DEAD** (dossier clos définitivement) si au moins un de :
- (a) le mécanisme économique lui-même a été mesuré du **signe opposé** avec un t
  déclusterisé décisif ;
- (b) le mécanisme s'avère **non indépendant** (chevauchement élevé avec un alpha existant) ;
- (c) la donnée source ne couvre qu'**un seul régime** et ne peut pas être étendue ;
- (d) l'ETA de toute expression du mécanisme est structurellement > 3 ans par la fréquence
  intrinsèque du phénomène (ex. rebalance hebdomadaire).

Un rejet est classé **EXPRESSION_DEAD_MECHANISM_OPEN** (une autre expression mériterait un
test) **uniquement si** je peux nommer, **avant** de lancer quoi que ce soit, la raison
économique pour laquelle la première expression était mal choisie, et que cette raison
n'est pas « les paramètres étaient mauvais ». Une reformulation dont la seule justification
est « en changeant X ça repasserait positif » est refusée d'office.

Par défaut, en cas de doute : **le rejet tient**. Je m'attends à clore la majorité.

---

## CIBLE 4 — `MICROSTRUCTURE_ALL_ROUND3` (DATA_ACCUMULATION)

Livrable : nombre de **jours indépendants complets** actuellement sur disque
(`data/microstructure_reduced/raw/`), le `N_required` en jours indépendants pour que les
mécanismes microstructure de round 3 deviennent jugeables, et la **date calendaire cible**
qui en découle.

Convention préenregistrée : l'unité indépendante pour un signal microstructure intraday est
le **jour calendaire** (un régime de book/flux persiste sur la journée), pas le tick ni la
minute. Un jour partiel ne compte pas. La date cible est calculée à partir du taux de
collecte réel (1 jour/jour, sous réserve d'interruption du service).

Seuil préenregistré : je considère la famille jugeable quand elle atteint
**≥ 60 jours indépendants ET ≥ 2 régimes de vol distincts** (un mono-régime reste
`REGIME_DEPENDENT` quel que soit le N — leçon `market_physics_v3`).

---

## Ce que je ne ferai PAS

- Aucun retuning de seuil après avoir vu un résultat sans le déclarer `REFIT`.
- Aucun choix de direction basé sur le PnL.
- Aucune promotion d'un candidat sur la base d'une réimplémentation historique seule.
- Aucune écriture hors de mon dossier ; aucun gros intermédiaire sur disque (97 % plein).
