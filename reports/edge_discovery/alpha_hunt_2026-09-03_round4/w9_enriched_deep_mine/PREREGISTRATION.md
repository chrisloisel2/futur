# W9_ENRICHED_DEEP_MINE — PREREGISTRATION

**Date d'écriture : 2026-09-03, AVANT tout test conditionné sur les rendements.**
Worker : `W9_ENRICHED_DEEP_MINE` — Alpha Hunt Round 4.
Axe : `data/enriched/*_1h_enriched.parquet`.

Ce document est écrit en deux temps, honnêtement déclarés :

- **§A (Phase 1 — audit)** : protocole d'audit purement descriptif, écrit avant d'ouvrir
  les fichiers de données. Aucun rendement futur n'intervient. Il n'y a rien à
  préenregistrer au sens statistique : un audit ne peut pas être « refité ».
- **§B (Phase 2 — mine)** : hypothèses et seuils fixés AVANT le premier calcul de
  rendement forward. Les colonnes exactes utilisées ne peuvent être nommées qu'après
  l'audit (elles dépendent de son verdict) ; la **famille de mécanisme, la direction du
  signe, les seuils, la définition du gate et les critères de kill sont figés ici**.

---

## §A — Protocole d'audit (Phase 1)

Livrable : table `colonne → verdict ∈ {USABLE, SUSPECT, PLACEHOLDER, LOOKAHEAD_RISK,
DEGRADED_PERIOD, NOT_UNIVERSAL}`.

Tests appliqués, dans l'ordre :

| # | test | critère de disqualification |
|---|---|---|
| A1 | présence dans le schéma des 50 symboles | présent < 50/50 → `NOT_UNIVERSAL` |
| A2 | taux de nuls | > 20 % hors warm-up → `SUSPECT` ; = 100 % → `PLACEHOLDER` |
| A3 | dégénérescence | std = 0, ou n_unique ≤ 2 sur un continu → `PLACEHOLDER` |
| A4 | recopie | colonne bit-à-bit identique à une autre → `PLACEHOLDER` (alias) |
| A5 | invariance cross-symbole | série identique sur ≥ 2 symboles distincts pour une feature censée être propre au symbole → `PLACEHOLDER` |
| A6 | rupture de distribution | \|Δmoyenne\| / σ_pooled > 1,0 entre segments de génération (`feature_count`), ou apparition/disparition de nuls à une date de coupure → `DEGRADED_PERIOD` |
| A7 | causalité | preuve par lecture du générateur `data_pipeline/enriched_ohlcv_features.py` : toute normalisation/filtrage non roulant, tout `shift(-n)`, tout `center=True`, toute FFT/ondelette non fenêtrée → `LOOKAHEAD_RISK` |
| A8 | concordance V2 | écart médian absolu vs `futur-data-v2/data_v2/normalized/` sur colonnes communes > 1 bp sur OHLCV → `SUSPECT` |

**Seuil de warm-up** : les 200 premières barres de chaque symbole sont exclues des tests
A2/A3 (les features à fenêtre 200 y sont mécaniquement dégradées) ; leur dégradation est
rapportée séparément comme `DEGRADED_PERIOD (warm-up)`, pas comme un défaut.

**Règle d'usage aval, figée ici** : seules les colonnes `USABLE` peuvent entrer dans un
backtest de la Phase 2. Une colonne `SUSPECT` ou `DEGRADED_PERIOD` ne peut être utilisée
que sur la période où elle est saine, et le rapport doit le dire.

---

## §B — Hypothèses de la Phase 2 (figées avant tout rendement)

### Contexte qui dicte le choix des hypothèses

Le goulot du projet n'est pas le bps, c'est l'**ETA de confirmation forward** : les alphas
validés demandent 9 à 17 ans. `enriched` est à cadence **horaire × 50 symboles**, soit
le dataset avec la meilleure fréquence d'épisodes potentiels du projet. **Je préenregistre
donc que je privilégie explicitement la fréquence d'épisodes indépendants sur le bps**, et
qu'un mécanisme à +200 bps mais 40 épisodes/an sera classé `UNCONFIRMABLE_IN_HORIZON`
sans discussion.

### Définitions figées

- **Barre d'entrée** : décision prise à la clôture de la barre `t` (toute l'information
  utilisée est ≤ `t`). Entrée au close de `t`. Sortie au close de `t+H`.
- **Horizons testés** : `H ∈ {1, 4, 8, 24}` heures. Fixé ici, pas d'ajout après coup.
- **Rendement** : `gross_bps = 1e4 · (close[t+H] / close[t] − 1) · side`.
- **Coûts** : `net_bps = gross_bps − 14`. Stress obligatoire : `gross_bps − 28`.
- **Benchmark** : jamais « > 0 ». Toujours `bras_signal − bras_contrôle` sur la **même
  population de barres** (mêmes symboles, mêmes heures, même régime de vol). Le contrôle
  est la moyenne inconditionnelle du même horizon sur la même population.
- **Déclustering 3 niveaux, calculé dès le premier passage** :
  - L1 : au plus un épisode par symbole par fenêtre de 24 h (on garde le premier).
  - L2 : jour calendaire, tous symboles confondus (moyenne intra-jour → 1 observation).
  - L3 : unité macro = **semaine calendaire** (le crypto co-bouge à l'échelle de la semaine).
  - `t_stat_declustered` et le bootstrap sont calculés sur **L2** (le plus conservateur
    exploitable) et le bootstrap est un **block-bootstrap par semaine** (blocs L3).
- **`n_required`** : N indépendant pour power 80 %, alpha 5 % bilatéral, sur un edge
  **haircuté de 50 %** : `n_required = (1,96 + 0,84)² · σ² / (0,5 · μ)²` avec `μ, σ` les
  moments de la distribution déclusterisée L2.
- **`event_rate`** : épisodes indépendants L2 par semaine, mesuré **sur les 6 derniers
  mois de données disponibles pour le symbole** (conservateur).
- **`eta_forward_confirmation` = `n_required / event_rate`**, en jours et en années.

### Seuils de verdict, figés

| verdict | condition |
|---|---|
| `VALIDATED_FOR_FORWARD` | `net_bps_stress28 > 0` ET `t_stat_declustered ≥ 2,5` ET `bootstrap_ci95` exclut 0 ET `ex_best_year > 0` ET aucune année < −50 % de l'edge moyen ET `eta < 3 ans` |
| `PROMISING_NEEDS_VALIDATION` | edge net > 0 et t ≥ 2,0 mais une case manque (dire laquelle) |
| `UNCONFIRMABLE_IN_HORIZON` | tout le reste OK mais `eta ≥ 3 ans` |
| `COST_FRAGILE` | `net_bps > 0` et `net_bps_stress28 ≤ 0` |
| `REGIME_DEPENDENT` | `ex_best_year ≤ 0` |
| `WEAK` | `|t_declustered| < 2,0` |
| `DEAD` | `net_bps ≤ 0` |
| `DATA_LIMITED` | l'audit interdit la colonne, ou N indépendant L2 < 100 |

### Hypothèses testées (familles, direction figée)

Contrainte d'éligibilité : **uniquement des colonnes classées `USABLE` en Phase 1**, et
uniquement des mécanismes non déjà couverts par les rounds 1-3 (cascades de liquidation
répétées, funding/basis, momentum cross-sectionnel 7d, Amihud hebdomadaire, microstructure
HF sont exclus par construction).

- **H1 — Compression de volatilité → expansion directionnelle.**
  Après une compression de la volatilité réalisée (ratio vol courte / vol longue dans son
  décile bas roulant), le rendement absolu à H heures est supérieur à la normale, et le
  *signe* suit la cassure du range. Signe prédit : **positif** sur `|ret|`, et positif sur
  le rendement signé par la direction de cassure. Seuil : décile bas = percentile roulant
  ≤ 10 sur fenêtre 500 barres.
- **H2 — Épuisement intrabar (mèche + volume) → réversion à 1-4 h.**
  Une barre à mèche extrême (percentile roulant ≥ 95) avec volume au percentile roulant
  ≥ 95 est suivie d'une réversion. Signe prédit : **contraire au sens de la mèche**.
- **H3 — Persistance directionnelle / efficiency ratio comme filtre de régime.**
  Le même signal de momentum court (retour 6-12 h) paie mieux quand l'`efficiency_ratio`
  roulant est haut (marché tendanciel) que quand il est bas. Test = **différence entre
  bras**, pas niveau absolu.
- **H4 — Divergence prix / OBV-CVD proxy.**
  Un nouveau plus-haut de prix non confirmé par le proxy de flux cumulé est suivi d'une
  sous-performance à H. Signe prédit : **négatif**.
- **H5 — Effet horaire × régime.**
  L'heure UTC de la barre (`hour_of_day`) module le rendement conditionnel des signaux
  H1-H4. Testé comme **interaction seulement**, jamais comme alpha autonome (un effet
  horaire nu est déjà couvert par W1_CALENDAR_CLOCK de ce round).

### Règles anti-refit, figées

1. Aucun seuil n'est ajusté après lecture d'un résultat. Si je change un seuil, le
   mécanisme est stampé `REFIT` dans `RESULTS.json` et n'est **jamais** promu au-dessus
   de `PROMISING_NEEDS_VALIDATION`.
2. Toute famille H1-H5 est testée sur les 4 horizons ; **je rapporte les 4**, pas le
   meilleur. Le nombre de tests est déclaré pour la correction de multiplicité
   (5 familles × 4 horizons = 20 tests principaux ⇒ seuil de Bonferroni t ≈ 3,0
   rapporté à titre indicatif à côté du t brut).
3. Toute colonne non `USABLE` utilisée par erreur invalide le mécanisme, pas seulement
   son bps.
