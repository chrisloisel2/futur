# W3_LISTINGS_LIFECYCLE — PREREGISTRATION
**Written 2026-09-03, BEFORE running any test.** Alpha Hunt Round 4, projet `/home/qbee/futur`.
Axe : cycle de vie des instruments (listings, delistings, âge, maturation).

---

## 0. Ce que je sais déjà avant de commencer (état de l'art interne)

Contrairement à ce que dit le briefing (« jamais miné »), `data/listings_backfill/binance/`
**a déjà été miné une fois** : `reports/LISTING_EVENT_STUDY.md` (juillet 2026, 518 listings
2023-01-17→2026-07-03) + `scripts/test_perp_listing_event_study.py`. Résultat de cette étude :

- Le drift post-listing est **négatif en absolu** sur *toutes* les fenêtres testées
  (delay 0.5h→168h × horizon 1h→336h), médiane jusqu'à −1690 bps à 336h.
- Conclusion opérationnelle prise : `src/institutional/portfolio/listing_age_gate.py`
  (`min_age_days=30`, aucun LONG sur un perp de moins de 30 jours). Décision utilisateur 2026-07-18.
- Le côté SHORT n'a pas été tradé (`SHORT_REJECTED`, cf. audit mai 2026).

**Faille méthodologique de cette étude que je préenregistre comme mon apport principal :**
elle compare le rendement post-listing **à zéro**, jamais **à la coupe transversale
contemporaine**. Or 2023-2026 est une période où l'alt médian a fortement sous-performé.
« Les nouveaux listings baissent » peut n'être que « les alts baissent ». La règle §1.3 du
briefing (comparer les bras entre eux, jamais à zéro) impose de refaire la mesure en
**neutralisé marché / cohorte**. C'est le cœur de mon axe A.

Autres acquis repris tels quels (non re-testés) :
- Cascades de liquidation : ne paient que sur répétition (1re ≈ −6/−19 bps, 3e+ ≈ +42/+87 bps).
- Funding/basis épuisé par l'arbitrage 2025-26.
- Momentum cross-sectionnel 7d→7d = +89 bps mais hebdomadaire → ETA catastrophique.
  Amihud +105 bps validé mais ETA ~17 ans.
- **Implication directe pour moi : tout mécanisme cross-sectionnel à rebalancement
  hebdomadaire est mort d'avance sur le critère ETA.** Je préenregistre donc que je teste
  systématiquement l'horizon **1 jour** en plus de 7 jours pour l'axe « âge », parce que
  c'est le seul moyen d'avoir un taux d'épisodes indépendants suffisant.

---

## 1. Données et univers (fixés avant tests)

| bloc | source | univers | fenêtre |
|---|---|---|---|
| **Event-time listings** | `data/listings_backfill/binance/{listings_calendar,klines_1h,klines_5m,funding}` | 518 symboles ayant des klines | 2023-01-17 → 2026-07-03 |
| **Panel calendaire** | `/home/qbee/futur-data-v2/data_v2/normalized/{perp_ohlcv,event_feature_panel}` (venue=binance, 5m → agrégé daily) | 312 symboles | 2020-01 → 2026-09 |
| **Calendrier de vie** | `listings_calendar.parquet` (683 symboles) | `onboard_ts`, `status` | 2019-09 → 2026-07 |
| **Événements** | `data/events/liq_cascade_dataset.parquet` | pour l'axe F | — |

### Survivorship — vérification obligatoire préenregistrée
Je dois publier dans le REPORT le décompte exact des noms MORTS inclus dans chaque univers.
Statuts morts = `SETTLING` (en cours de radiation) + `DELISTED` + `DELISTED_NO_DATA`.
Comptage attendu (vérifié en amont, à re-confirmer dans le rapport) :
- panel 312 : 259 TRADING + 38 SETTLING + 14 DELISTED + 1 DELISTED_NO_DATA → **53 noms morts (17,0 %)**
- univers klines listings 518 : 419 TRADING + 95 SETTLING + 4 DELISTED → **99 noms morts (19,1 %)**
Si un test tourne sur un univers sans noms morts, il est stampé `SURVIVORSHIP_BIASED` et non promu.
Limite connue et à déclarer : les perps radiés **avant 2023** et absents de fapi ne sont pas
récupérables (cf. `listings_backfill_store.yaml _meta.missing_delisted`) — l'axe A est donc
propre sur 2023+ seulement.

---

## 2. Conventions de mesure (fixées avant tests)

**PIT.** `onboard_ts` = `onboardDate` d'exchangeInfo, connu à l'avance (annonce officielle) —
aucun lookahead. L'âge d'un instrument à `t` est `t − onboard_ts`, causal par construction.
Toute feature roulante est calculée en fenêtre fermée à gauche (`shift(1)`).

**Coûts.** Convention briefing §1.4 : `net_bps = gross_bps − 14`, stress `gross − 28`.
Mais deux surcharges préenregistrées, plus conservatrices, qui priment pour le VERDICT :
- **Livres long/short** (4 jambes) : `net_ls = gross − 28`, stress `gross − 56`.
- **Entrée sur perp de moins de 24 h** (books très fins ; avertissement explicite de
  l'étude de juillet) : stress supplémentaire `gross − 60`. Tout mécanisme de l'axe A à
  `delay < 4h` qui ne survit pas à −60 est stampé `COST_FRAGILE`.

**Déclustering — mapping des 3 niveaux, fixé maintenant :**

| | mécanismes event-time (axe A, D) | mécanismes panel/livre (axes B, C, E, F) |
|---|---|---|
| **L1** | 1 événement par (symbole, fenêtre 24 h) | 1 rendement de livre par période de détention **non chevauchante** |
| **L2** | 1 épisode par **jour calendaire** (tous symboles) | 1 épisode par **jour calendaire** |
| **L3** | 1 épisode par **vague de cotation** (cluster de listings séparé du suivant par ≥ 7 jours sans listing) | 1 épisode par **mois calendaire** |

`t_stat_declustered` et `bootstrap_ci95` du gate §2 sont calculés **au niveau L3** (le plus
conservateur). Le t-stat L2 est reporté en parallèle pour information, jamais comme verdict.
Block-bootstrap : 5000 tirages, blocs = unité L3.

**ETA.** `n_required = 7.849 / (0.5 · d)² = 31.4 / d²`, où `d` = Sharpe par épisode L3
(`mean/sd` des rendements d'épisodes L3), power 80 %, alpha 5 % bilatéral, haircut 50 %
obligatoire. `event_rate` = épisodes L3 / semaine mesurés **sur les 6 derniers mois**
(2026-03-03 → 2026-09-03). `eta = n_required / event_rate` en semaines → jours et années.
ETA > 3 ans ⇒ `UNCONFIRMABLE_IN_HORIZON` quel que soit le bps.

**Stabilité.** Décomposition année par année obligatoire + `ex_best_year`.

---

## 3. Hypothèses et seuils préenregistrés

### Axe A — effet de cotation (event-time, 518 listings)

- **A1 `LIST_DRIFT_XSNEUTRAL`** — H : après neutralisation par le rendement equal-weight de
  la coupe transversale éligible sur la MÊME fenêtre, le drift post-listing reste
  significativement négatif (sous-performance relative, analogue IPO underperformance).
  Grille préenregistrée : delay ∈ {1h, 4h, 24h}, horizon ∈ {24h, 72h, 168h, 720h}.
  Bras comparés : *nouveau listing* vs *panier equal-weight des noms éligibles (âge ≥ 30 j,
  liquidité ≥ 1 M$/j)* sur la même fenêtre. Direction tradable = short le nouveau / long le panier.
  **Seuil de promotion** : |spread net| ≥ 50 bps ET t_L3 ≥ 2,0 ET signe stable ≥ 3 années sur 4.
- **A2 `LIST_D0_CONDITIONAL_SPREAD`** — H : le drift dépend de la réaction jour-0.
  Bras A = listings avec `ret_24h > +20 %` (pump), bras B = `ret_24h < 0` (dump).
  **Mesure = A − B**, jamais A vs 0. Seuil : |A−B| ≥ 100 bps ET t_L3 ≥ 2,0.
- **A3 `LIST_FUNDING_CARRY_YOUNG`** — H : le funding des 30 premiers jours est
  structurellement plus extrême que celui des contrats matures ⇒ le carry (funding encaissé
  en short-perp) est plus riche. Mesure : funding cumulé 30 j des jeunes vs funding cumulé
  30 j (même dates calendaires) des contrats matures. Seuil : différentiel ≥ 100 bps/30 j
  ET t_L3 ≥ 2,0. Note : c'est un flux de carry, à évaluer **séparément** du prix.
- **A4 `LIST_WAVE_SIZE_COND`** — H : plus la vague de cotation est large, plus la
  sous-performance est forte (dilution d'attention). Bras = vagues grandes (≥ médiane)
  vs petites. Mesure = grand − petit. Seuil : ≥ 100 bps ET t_L3 ≥ 2,0.

### Axe B — l'âge comme facteur transversal (panel 312, 2020-2026)

- **B1 `XSEC_AGE_FACTOR_1D` / `_7D`** — H : `log(age_days)` est un facteur transversal payé.
  Livre : quintile le plus vieux (long) − quintile le plus jeune (short), equal-weight,
  rebalancement quotidien (1D) ou hebdomadaire (7D), rendements forward winsorisés 1 %/99 %
  sur toute la coupe éligible. Éligibilité : médiane roulante causale 30 j du quote volume
  ≥ 1 M$ ; **pas** de plancher d'âge (c'est l'objet du test). Seuil : net ≥ 20 bps/période
  ET t_L3 ≥ 2,0 ET signe stable ≥ 5 années sur 7.
  *C'est le candidat à ETA court de mon axe — 1D donne ~250 épisodes L1/an.*
- **B2 `XSEC_AGE_RESID_LIQ`** — H : l'effet d'âge n'est PAS un simple repackaging de la
  prime d'illiquidité Amihud (déjà validée, +105 bps) ni de la taille. Mesure : régression
  transversale quotidienne du rendement forward sur `log(age)`, `log(qvol)`, `amihud` ;
  je garde le coefficient sur `log(age)`. Seuil : coefficient d'âge de même signe que B1 et
  t_L3 ≥ 2,0 après contrôles. **Si B1 passe mais B2 échoue, le verdict de B1 est
  `WEAK` (redondant avec Amihud), pas `PROMISING`.**
- **B3 `AGE_BUCKET_MONOTONICITY`** — H : le rendement forward est monotone en âge.
  Buckets préenregistrés : `<30j`, `30-90j`, `90-180j`, `180-365j`, `1-2a`, `>2a`.
  Comparaison de bras deux à deux (jamais à zéro) + test de monotonie (Spearman des moyennes
  de bucket vs rang de bucket). Seuil : rho ≥ 0,8 en valeur absolue avec ≥ 5 buckets peuplés.

### Axe C — maturation de la microstructure

- **C1 `AGE_VOL_MATURATION`** — descriptif : vol réalisée 24 h par bucket d'âge.
  Pas un trade ; sert à savoir si un livre âge doit être vol-scalé. Livrable = table.
- **C2 `AGE_FUNDING_EXTREMITY`** — H : `|funding|` et le percentile de funding décroissent
  avec l'âge ⇒ carry plus riche sur les jeunes. Tradable testé : livre carry
  *short-perp jeunes à funding positif* vs *short-perp matures à funding positif*,
  mesure = différentiel de funding encaissé, net de coûts. Seuil : ≥ 50 bps/30 j, t_L3 ≥ 2,0.
- **C3 `AGE_LIQUIDITY_MATURATION`** — descriptif : quote volume, nombre de trades, Amihud
  par bucket d'âge. Sert à borner la capacité de tout livre « jeunes ».

### Axe D — fin de vie / radiation

- **D1 `DELIST_PRE_DRIFT`** — H : dérive négative relative dans les N jours précédant la
  radiation. N ∈ {7, 30, 90}. Univers = 53 noms morts du panel (+ 99 de l'univers listings
  si datable). Neutralisé coupe transversale. **Prédiction préenregistrée : N est trop
  faible, l'issue attendue est `UNCONFIRMABLE_IN_HORIZON` ou `DATA_LIMITED`.**
  Je le teste quand même pour documenter l'ETA.
- **D2 `DELIST_FUNDING_BASIS_DISLOCATION`** — H : le funding/basis devient extrême en fin de
  vie (positions forcées de se déboucler). Mesure : |funding| et |basis_z| dans les 30
  derniers jours vs le reste de la vie du MÊME symbole (comparaison intra-nom, donc pas
  polluée par la sélection). Seuil : ratio ≥ 1,5 et t_L3 ≥ 2,0.

### Axe E — vagues de cotation comme marqueur de régime (méta-signal)

- **E1 `LISTING_WAVE_RISK_REGIME`** — H : une intensité de cotation élevée marque
  l'euphorie ⇒ rendement forward du marché plus faible. Signal PIT : nombre de listings sur
  30 j glissants (fenêtre fermée), converti en percentile sur l'historique **expanding**
  (aucun lookahead). Bras : régime top-tercile vs bottom-tercile. Cible : rendement forward
  7 j et 30 j du panier equal-weight éligible ET de BTC. Mesure = haut − bas.
  Seuil : ≥ 100 bps sur 30 j ET t_L3 ≥ 2,0 ET signe stable ≥ 4 années sur 6.
  Usage visé si validé : **réduction de risque**, pas un trade directionnel.
- **E2 `WAVE_COND_XSEC_MOM`** — H : le régime de vague conditionne le momentum transversal
  7d (déjà connu, +89 bps). Mesure = momentum en régime haute-vague − en régime basse-vague.
  Seuil : ≥ 100 bps ET t_L3 ≥ 2,0.

### Axe F — interaction âge × alphas existants

- **F1 `AGE_X_XSEC_MOM_7D`** — H : le momentum transversal 7d paie différemment sur les
  jeunes. Bras : livre momentum restreint à la moitié jeune vs restreint à la moitié vieille
  (âge médian PIT). Mesure = jeune − vieux. Seuil : ≥ 50 bps ET t_L3 ≥ 2,0.
- **F2 `AGE_X_LIQ_CASCADE_REPEAT`** — H : l'effet « cascade répétée » (acquis du projet)
  est plus fort sur les jeunes contrats (books fins, positionnement plus fragile).
  Bras : cascades 3e+ occurrence sur jeunes vs sur vieux. Mesure = jeune − vieux.
  Seuil : ≥ 50 bps ET t_L3 ≥ 2,0. **Je ne re-teste PAS l'effet repeat lui-même** — il est acquis.

---

## 4. Règles de décision préenregistrées (anti-refit)

1. Aucun seuil ci-dessus n'est modifié après avoir vu un résultat. Si je change quoi que ce
   soit, le mécanisme est stampé `REFIT` dans RESULTS.json et ne peut pas dépasser
   `PROMISING_NEEDS_VALIDATION`.
2. Grilles fixées ici (delays, horizons, buckets, quintiles). Aucune grille n'est étendue
   après coup pour aller chercher un résultat.
3. Un mécanisme dont l'ETA L3 dépasse 3 ans est `UNCONFIRMABLE_IN_HORIZON`, **même si son
   bps et son t sont superbes**. Je m'y engage à l'avance parce que je m'attends à ce que ce
   soit le sort de la majorité de mon axe (~50-250 listings/an, fortement clusterisés).
4. Prédiction préenregistrée globale de l'axe : je m'attends à ce que **l'axe A soit
   `UNCONFIRMABLE_IN_HORIZON`** (N intrinsèque faible + clustering en vagues) et que le seul
   espoir d'ETA court soit **B1 en version 1 jour**. Écrire cette prédiction maintenant me
   permet de mesurer honnêtement si je me suis laissé entraîner par les données.
5. Tout résultat dont la causalité d'un champ n'est pas prouvable est stampé `PIT_UNVERIFIED`.

---

## 5. AMENDEMENT (écrit 2026-09-03, après construction du panel, AVANT tout test de résultat)

Deux corrections de cohérence, faites en regardant uniquement la *structure* des données
(comptages, calendrier), jamais un rendement. Elles sont consignées ici pour être auditables.

**(a) Ordre des niveaux de déclustering pour les livres panel.** Le mapping du §2 était
incohérent pour un livre à détention 7 j : « L2 = jour calendaire » y produit PLUS
d'observations que « L1 = période non chevauchante ». Or L1 ⊇ L2 ⊇ L3 par construction.
Mapping corrigé, appliqué à tous les mécanismes panel (B, C, E, F) :
- **L1** = 1 observation par jour calendaire de rebalancement (= `n_raw` pour un livre quotidien)
- **L2** = 1 observation par **période de détention non chevauchante** (jour si h=1 j, semaine si h=7 j)
- **L3** = 1 observation par unité macro (voir (b))
Aucun seuil, aucune direction, aucun horizon n'est modifié. C'est une correction d'étiquettes.

**(b) Choix de l'unité macro L3 pour les livres panel.** Le briefing §1.2 donne comme
exemples d'unité macro « régime de vol, semaine, épisode ». Fixer L3 = mois pour un livre
quotidien est si conservateur que tout mécanisme échouerait mécaniquement à l'ETA
(≈ 0,23 épisode/semaine), ce qui rendrait le test non informatif. Règle fixée maintenant,
appliquée uniformément et sans exception :
- livre à détention ≤ 1 j → **L3 = semaine calendaire**
- livre à détention 7 j  → **L3 = mois calendaire**
- **et je reporte SYSTÉMATIQUEMENT l'ETA sous les deux unités** (`eta_L3` en tête,
  `eta_L3_month` en stress) pour qu'aucun choix ne puisse être suspecté de cherry-picking.
Le verdict `UNCONFIRMABLE_IN_HORIZON` est prononcé sur `eta_L3` (unité de tête).

**(c) Définition des vagues de cotation (L3 de l'axe A).** Prévue à « gap ≥ 7 jours sans
listing ». Si le rythme de cotation 2025-2026 est tel que cette règle agrège tout
l'historique en une poignée de vagues géantes (dégénérescence), je bascule sur un
bucketing **semaine calendaire ISO** comme unité L3 et je le déclare. Décidé avant de
regarder le moindre rendement.
