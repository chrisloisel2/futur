# W9_ENRICHED_DEEP_MINE — RAPPORT

Worker `W9_ENRICHED_DEEP_MINE`, Alpha Hunt Round 4 (`alpha_hunt_2026-09-03_round4`).
Axe : `data/enriched/*_1h_enriched.parquet` (48 Go sur disque, 50 fichiers parquet,
4 178 colonnes distinctes, barres 1 h, 2017-08-17 → 2026-09-04).
Préenregistrement : `PREREGISTRATION.md` (écrit avant tout calcul de rendement).
Scripts ré-exécutables : `evidence/`. Résultats machine : `RESULTS.json`.

> **Note de reprise.** Ce worker a été interrompu par une limite de session le 2026-09-03
> après la construction du panel et avant l'écriture du rapport. La reprise (2026-09-05) a
> relu le préenregistrement, **relu et corrigé le moteur de gate** (§4.0), ajouté trois
> tests d'audit qui manquaient (A8 concordance V2, attribution de source, détection de
> coutures) et ajouté deux tests décisifs de Phase 2 (fenêtre OOS 2026 et réplication sur
> l'univers PIT). Toutes les corrections rendent le gate **plus strict** ; aucune ne peut
> promouvoir un mécanisme.


## EN TÊTE — les deux choses à retenir avant de lire

**1. Aucun résultat publié du projet ne repose sur une colonne pourrie de `data/enriched`.**
Le piège documenté (`taker_buy_*` = `volume/2`) est confirmé et quantifié (§1.2a), mais le
balayage de tout le dépôt (§1.6) montre qu'il a été correctement évité :
`CROSS_SECTIONAL_MOMENTUM_CVD` et `CROSS_SECTIONAL_MOMENTUM_LIVE_V1` sont **innocentés**,
documents à l'appui. **Un seul point reste à vérifier** :
`ai/level_0/institutional_features.py` déclare `taker_buy_ratio_base` **et**
`taker_buy_ratio_quote` dans `FEATURES_INST_LONG` ; sur BTCUSDT ces deux colonnes valent
**0,5 constant sur toute la période d'entraînement** — si la collection Mongo qui alimente ce
bloc vient du même pipeline, ces features n'apportent rien. Bug de générateur trouvé au
passage : `exit_pressure_score_*` (11 colonnes) est identiquement nul (§1.2e).

**2. ⚠ Alerte méthodologique qui dépasse cet axe (§5.3).** Le contrôle « moyenne du **jour
calendaire** » — celui du briefing §1.3 — **fabrique jusqu'à +80 bps d'edge entièrement
fictif** sur un panel intraday, avec des t-stats de 16 à 20 et une stabilité parfaite sur
9 années. Prouvé par un placebo : un signal **aléatoire** obtient +79,3 bps là où le vrai
signal obtient +82,4. Corrigé (contrôle à la **barre horaire**), le seul mécanisme qui passait
le gate préenregistré tombe de **+42,8 bps (t = 8,5) à −4,7 bps (t = −0,83)**, et le signe de
la conclusion préenregistrée de deux autres familles **s'inverse**. Tout worker de ce round
utilisant un panel intraday et un contrôle journalier devrait rejouer ses bras avec un
placebo avant de livrer. **Livrable alpha de ce worker : 0 `VALIDATED_FOR_FORWARD`.**

---

# 1. LIVRABLE PRINCIPAL — TABLE D'AUDIT DES COLONNES DE `data/enriched/`

Table complète, une ligne par colonne : **`evidence/COLUMN_VERDICTS.csv`** (4 178 lignes,
colonnes `column, family, present_in, verdict, canonical, audit_test, usage_scope, reason`).
Résumé par famille : `evidence/AUDIT_SUMMARY_BY_FAMILY.csv` (685 familles).
Liste noire : `evidence/NEVER_USE.csv` (267 colonnes).

> **Note de versionnement (résolue).** À la rédaction, `.gitignore:22` (`*.csv`, global)
> emportait ces tables : elles n'auraient jamais été commitées. Le coordinateur a ajouté
> l'exception `.gitignore:33` `!reports/edge_discovery/**/*.csv`. Vérifié après correctif
> (`git add --dry-run`) : **les 18 CSV du round, dont les 7 de ce worker, sont bien
> versionnables**. `evidence/COLUMN_AUDIT.json` (produit par `export_audit_json.py` avant le
> correctif) reste disponible comme copie JSON à contenu identique, pratique pour une lecture
> programmatique, mais n'est plus nécessaire à la survie de l'audit.

## 1.1 Répartition des verdicts

| verdict | colonnes | part | ce que ça veut dire |
|---|---:|---:|---|
| `USABLE` | 2 911 | 69,7 % | causale (fenêtres `.rolling()` strictement *trailing*, vérifié dans le générateur), non dégénérée, présente sur 50/50 symboles |
| `REDUNDANT_ALIAS` | 816 | 19,5 % | **bit-à-bit identique** à une autre colonne : utilisable, mais n'apporte zéro information. Un modèle qui les compte comme features distinctes surestime sa largeur de ~20 % |
| `PLACEHOLDER` | 264 | 6,3 % | constante, nulle, ou remplie par une valeur de repli. **À ne jamais utiliser** |
| `NOT_UNIVERSAL` | 142 | 3,4 % | absente d'une partie des 50 symboles → interdit en cross-section |
| `DEGRADED_PERIOD` | 36 | 0,9 % | saine seulement sur un sous-ensemble symbole × période (voir `usage_scope`) |
| `METADATA` | 6 | 0,1 % | pas des features |
| `LOOKAHEAD` | 3 | 0,1 % | labels forward. **Fuite garantie si utilisées comme features** |

## 1.2 Les colonnes à ne JAMAIS utiliser (et pourquoi)

### (a) `taker_buy_quote_asset_volume`, `taker_buy_quote`, `taker_buy_ratio_quote`, `taker_sell_quote` — PLACEHOLDER absolu

Le piège documenté du projet est **confirmé et quantifié** : ces colonnes valent
`quote_asset_volume × 0,5` **exactement, sur 100 % des barres, pour 50/50 symboles, sur
toutes les années** (test en tolérance relative 1e-6, recoupé contre le panel V2 —
`evidence/SOURCE_ATTRIBUTION.csv`, colonne `tbq_is_half_qav` = 1,0000 partout).
Tout CVD / order-flow imbalance / taker pressure construit dessus est **identiquement nul**.

### (b) `taker_buy_base_asset_volume`, `taker_buy_base`, `taker_buy_ratio_base`, `taker_sell_base` — DEGRADED_PERIOD

Même placeholder (`volume × 0,5`) **sauf** pour 7 symboles où la valeur est réelle et
concorde *exactement* avec `futur-data-v2` : **ADA, AVAX, BNB, DOGE, LINK, SOL, XRP**.
Réel seulement sur la queue récente pour **BTC, DOT, ETH** (BTC à partir de 2026-01-01,
ETH 44 % de 2026, DOT 27 % de 2026). Placeholder pour les **40 autres symboles**.

### (c) `number_of_trades` / `trades` — DEGRADED_PERIOD (et alias l'un de l'autre)

`= 0` sur 100 % des barres sauf pour **ADA, AVAX, BNB, BTC, DOGE, ETH, LINK, SOL, XRP**.
`trades` est un alias bit-à-bit de `number_of_trades`.

### (d) `future_ret_8h`, `future_ret_h16_min`, `future_ret_h16_max` — LOOKAHEAD

Vérifié empiriquement : `future_ret_8h[t] == log(close[t+8]/close[t])` à 1e-15 près sur
**100 %** des barres (BTC, SOL, AAVE). Ce sont les cibles d'apprentissage (`ai/level_0/labels.py`,
`TARGET_COL` de la TRM Fleet). Second défaut : elles ne sont **pas recalculées sur la queue
appendée** — le dernier `future_ret_8h` non nul est daté du **2026-08-08** alors que les
fichiers des 10 symboles live vont jusqu'au 2026-09-04 (≈ 27 jours de labels manquants).

### (e) `exit_pressure_score_*` (11 colonnes) ≡ 0,0 — **bug de générateur identifié**

`data_pipeline/enriched_ohlcv_features.py:899` :
```python
f["exit_pressure_score_%s" % prefix] = f.get("reversal_score_%s" % prefix, 0.0)
```
La clé cherchée est `reversal_score_<n>` ; la clé réellement produite (ligne 912) est
`reversal_score`, **sans suffixe**. Le `.get(..., 0.0)` renvoie donc toujours la valeur par
défaut. Les 11 colonnes `exit_pressure_score_{1,2,3,5,10,14,20,30,50,100,200}` sont
identiquement nulles sur les 7 symboles audités. Correctif : `f.get("reversal_score", 0.0)`.

### (f) Familles constantes par construction

- `pullback_volume_*` (7/11 colonnes constantes) : `mean(volume, n) / vol_mean` où `vol_mean`
  est la même moyenne → ≡ 1,0.
- `volatility_contraction_pattern_*` (7/11) : fenêtre `min(max(n,3),10)` → toutes les variantes
  n ≥ 10 sont **la même colonne** (fenêtre 10) et valent ~toujours 0.
- `volatility_ratio_1_200`, `funding_accel`, `short_term_volume_spike`, `ttm_squeeze_200`.
- **140 des 264 placeholders sont les variantes à fenêtre courte** (`_1` : 140, `_2` : 38,
  `_3` : 27, `_5` : 22, `_10` : 11) de familles par ailleurs saines : une fenêtre de 1 à 5
  barres dégénère mécaniquement (percentile roulant sur 1 point, écart-type sur 2 points…).
  **Règle pratique : n'utiliser que les suffixes ≥ 14.**

## 1.3 ⚠ L'univers de `data/enriched` n'est PAS point-in-time

Les 50 fichiers sont la **liste « frozen-50 » figée en 2026, appliquée rétroactivement**
jusqu'en 2017. Il n'y a **aucun symbole délisté** dans le répertoire, alors que la période
2017-2026 en compte des dizaines ; le seul vestige est `RNDRUSDT`, dont la série s'arrête au
2024-07-30 (renommé `RENDERUSDT`), et `MKRUSDT` dont le volume tombe à 0 le 2025-09-08.

C'est un défaut réel et il interdit toute conclusion cross-sectionnelle prise au sérieux sur
ce répertoire. **Mais — et c'est le résultat le plus utile de ce worker — ce n'est PAS ce qui
gonfle les edges qu'on y mesure.** Le mécanisme principal trouvé en Phase 2 a été rejoué à
l'identique sur l'univers PIT de `futur-data-v2` (312 symboles, délistés inclus, 8,78 M
barres 1 h) : l'edge n'y est pas plus faible, il est **du même ordre et parfaitement
monotone** (§5.3). L'hypothèse « c'est du survivorship » a donc été **testée et réfutée**.
Ce qui gonfle l'edge est autre chose, et c'est le §5.4 qui le montre.

## 1.4 Périmètre temporel — bascule de source PERP → SPOT (test A8)

`evidence/SOURCE_ATTRIBUTION.csv` (50 symboles × année, close 1 h comparé au panel V2) et
`evidence/SEAMS.csv` (coutures détectées).

| constat | détail |
|---|---|
| 237 (symbole, année) | **PERP** Binance, close ET volume concordants à 100 % avec `futur-data-v2` (écart médian 0,000 bp) |
| **DOGEUSDT, XRPUSDT** | **SPOT sur TOUT leur historique** (2020→2026), pas perp. Écart au perp : 5-10 bps médian sur >93 % des heures |
| **BTCUSDT** | bascule perp → **spot au 2026-01-01** (volume × 0,18, `obv` −1 940 682 → −135 056) |
| **ADA, AVAX, BNB, ETH, LINK, SOL** | bascule perp → spot au **2026-05-20** (volume × 0,07 à 0,19) |
| **DOGE, XRP** | couture de génération au 2026-05-24 ; **DOT** au 2026-06-28 |
| 13 (symbole, année) | pas de référence V2 (BTC/ETH 2017-2018, XRP 2018-2019, PYTHUSDT, RNDRUSDT) |

Les 40 autres symboles sont **figés depuis le 2026-06-29** (aucun service systemd n'alimente
`data/enriched`), les 10 symboles « live » vont jusqu'au 2026-09-04.

**Conséquence opérationnelle : la queue 2026 de `data/enriched` — exactement la période où
opèrent le live alpha lab et la TRM Fleet — change de sous-jacent en cours de route.** Les
niveaux de volume y chutent d'un facteur 5 à 14, ce qui casse toute normalisation de volume
à fenêtre longue (200 barres = 8 jours) pendant plus d'une semaine après chaque couture, et
remet à zéro toutes les features cumulatives (`obv`, `accumulation_distribution_line`,
`anchored_vwap`, `volume_price_trend`, `force_index`, `cumulative_return`, `*_drawdown`…).

## 1.5 Ce que l'audit **innocente**

- **Aucune fuite temporelle dans les features.** Lecture intégrale de
  `data_pipeline/enriched_ohlcv_features.py` (1 804 lignes) : tous les helpers
  (`_rolling_percentile_rank`, `_rolling_z`, `_rolling_autocorr`, `_hurst_array`,
  `_efficiency_ratio`, `_rolling_slope`, `_fractal_dimension`…) utilisent `.rolling(n)`
  strictement *trailing*. **Aucun `center=True`, aucune normalisation plein-échantillon.**
- Le bloc `_label_features` (ligne 1080) produit **19 familles en `shift(-n)`**
  (`future_return_*`, `direction_*`, `triple_barrier_label_*`, `best_action_*`,
  `max_future_upside_*`…). **Vérifié : aucune de ces colonnes n'est matérialisée** dans les
  parquets (le générateur est appelé avec `include_labels=False`). Seules les 3 colonnes du
  §1.2(d) existent. **C'est une bonne nouvelle qu'il fallait vérifier.**
- **`hour_of_day` est bien l'heure UTC** (100 % de concordance, 3 symboles testés) — pas
  l'heure locale. Utilisable tel quel (utile pour W1_CALENDAR_CLOCK).
- **Le volume est fiable** partout où le close l'est : sur les 252 couples (symbole, année)
  dont le close concorde avec V2, **0** présente une divergence de volume
  (`evidence/VOLUME_CONCORDANCE.csv`). Les sauts de niveau de volume détectés
  (`SEAMS.csv`, ×3 à ×70) sont **présents à l'identique dans V2** : ce sont de vraies
  migrations de liquidité, pas un artefact d'enrichissement — mais ils dégradent quand même
  les normalisations à fenêtre longue autour de leur date.

## 1.6 Recoupement : un résultat antérieur repose-t-il sur une colonne pourrie ?

Balayage de tout le dépôt (`src`, `scripts`, `configs`, `reports`, `ai`, `frontend_pipeline`,
`trading-system`) contre les 267 colonnes `PLACEHOLDER`/`LOOKAHEAD` et les 36 `DEGRADED_PERIOD`.

| référence trouvée | verdict |
|---|---|
| `reports/edge_discovery/validation_2026-09/CROSS_SECTIONAL_MOMENTUM_CVD/REPORT.md` | ✅ **INNOCENTÉ.** Le rapport construit son CVD depuis `data_v2/normalized/perp_ohlcv` et **documente explicitement** le placeholder de `enriched` (§1.2). Recoupé en plus contre `agg_trades_flow`. Aucune contamination. |
| `reports/live_alpha_lab/CROSS_SECTIONAL_MOMENTUM_LIVE_V1/freeze_spec.json` | ✅ **INNOCENTÉ.** Le freeze-spec dit noir sur blanc que l'alpha « ne touche jamais `taker_buy_*` » et rejette `data/enriched` comme source (staleness des 40/50 fichiers). |
| `ai/level_0/institutional_features.py` (`FEATURES_INST_LONG`, bloc « order flow — taker pressure », marqué *v2 : ajout P0.3*) | ⚠ **À VÉRIFIER AVANT USAGE.** Déclare `taker_buy_ratio_base` **et** `taker_buy_ratio_quote` comme features. Sur BTCUSDT — le seul symbole de cette collection — `taker_buy_ratio_quote` vaut **0,5 constant sur 100 % de l'historique** et `taker_buy_ratio_base` vaut **0,5 constant de 2017 à 2025**, réel seulement à partir de 2026-01-01. Si la collection Mongo `ohlcv_institutional_features_btcusdt` est construite par le même pipeline (à confirmer), **ce bloc de features n'apporte aucune information sur toute la période d'entraînement**, et les features dérivées `taker_flow_imbalance_20` / `taker_flow_momentum_5` sont dégénérées par construction. (Note MEMORY : P0.3 est marqué « pas commencé » — le risque est donc probablement latent, pas réalisé.) |
| `reports/repro_audit/enriched_reference_schema.json` | Simple inventaire de schéma, pas un résultat. Sans risque. |
| `data_pipeline/enriched_ohlcv_features.py` | Le générateur lui-même. Bug `exit_pressure_score` signalé en §1.2(e). |

**Aucun résultat publié du projet ne s'appuie sur une colonne `taker_buy_*` de `enriched`.**
Le piège documenté a bien été évité par les workers précédents. En revanche, le défaut du
§1.3 (univers non-PIT) n'avait jamais été formulé pour `data/enriched`, et il est
**beaucoup plus dangereux** que le piège `taker_buy_*` parce qu'il ne se voit pas.

## 1.7 Mode d'emploi pour les workers suivants

1. Ne prendre que `verdict == USABLE`, et **uniquement les suffixes de fenêtre ≥ 14**.
2. Dédupliquer par `canonical` : 816 colonnes sont des alias bit-à-bit.
3. Ne **jamais** faire de cross-section ni d'achat-de-faiblesse sur cet univers (§1.3) —
   passer par `futur-data-v2/data_v2/normalized/perp_ohlcv` (312 symboles, PIT).
4. Couper la période au **2026-05-19** (au 2025-12-31 pour BTCUSDT) ou traiter la queue
   comme un autre instrument.
5. Exclure `RNDRUSDT` (série morte au 2024-07-30) et tronquer `MKRUSDT` au 2025-09-08
   (volume = 0 ensuite, 14,6 % de barres plates).
6. Pour toute donnée de flux taker : aller chercher `futur-data-v2`, pas `enriched`.

---

# 2. MÉTHODE (Phase 1 — audit)

Huit tests, appliqués dans l'ordre du `PREREGISTRATION.md` §A. Les 200 premières barres de
chaque symbole sont exclues des tests A2/A3 (warm-up des fenêtres 200).

| test | ce qu'il fait | script |
|---|---|---|
| A1 présence | comptage dans les 50 schémas parquet | `audit_columns.py` |
| A2 nullité | taux de nuls hors warm-up, par année et par génération | `audit_columns.py` |
| A3 dégénérescence | std = 0, n_unique, taux de zéros | `audit_columns.py` |
| A4 recopie | hash MD5 de chaque colonne → groupes bit-à-bit identiques | `audit_columns.py` |
| A6 rupture | segments de `feature_count`, sauts de niveau de volume (médianes 168 h), resets `obv` | `seam_scan.py` |
| A7 causalité | **lecture du générateur** `data_pipeline/enriched_ohlcv_features.py` + vérification empirique des labels | manuel + `audit_columns.py` |
| A8 concordance V2 | close/volume/taker/trades 1 h vs `futur-data-v2` agrégé 5 m → 1 h | `crosscheck_v2.py`, `source_attribution.py`, `volume_concordance.py` |
| — synthèse | assemblage colonne → verdict + périmètre d'usage | `build_verdicts_v2.py` |

7 symboles ont été audités colonne par colonne sur les 4 178 colonnes (BTC, ETH, SOL, ADA,
LINK, AAVE, PEPE — un gros/moyen/petit de chaque génération) ; les tests A1 et A8 portent sur
les **50** symboles.

# 3. PANEL DE LA PHASE 2

`build_panel.py` extrait, en streaming DuckDB **sans jamais copier les 48 Go**, les colonnes
`USABLE` nécessaires : **2 032 957 lignes × 49 symboles**, 2017-08-17 → 2026-09-03, float32,
260 Mo. Exclusions décidées par l'audit (hygiène de source, pas un réglage de seuil) :
`RNDRUSDT` retiré, `MKRUSDT` tronqué au 2025-09-08, `DOGEUSDT`/`XRPUSDT` marqués `src_spot`.

Les 30 features retenues sont **toutes classées `USABLE`** par la table du §1 — vérification
explicite ré-exécutable, aucune colonne `PLACEHOLDER`/`DEGRADED`/`NOT_UNIVERSAL` n'entre en
Phase 2.

# 4. LE GATE

## 4.0 Corrections apportées au moteur v1 (`gate.py` → `gate2.py`)

La relecture du moteur écrit avant l'interruption a trouvé **trois défauts**. Les trois
corrections rendent le gate plus strict ; **aucune ne peut promouvoir un mécanisme.**

1. **Incohérence de signe (bug réel).** Le v1 testait `|t| < 2 → WEAK` puis `net ≤ 0 → DEAD`.
   Un mécanisme dont l'edge conditionnel était **franchement négatif** (t = −9) mais dont le
   rendement brut restait positif tombait dans la branche `PROMISING_NEEDS_VALIDATION`.
   v2 : `edge_demeané ≤ 0 OU net ≤ 0 → DEAD`, évalué en premier.
2. **Un seul t-stat pour deux quantités différentes.** Le v1 calculait le t sur le rendement
   *demeané par jour* mais le `net_bps` sur le rendement *brut*. v2 rapporte les deux
   (`t_stat_declustered` conditionnel **et** `t_stat_net_tradable`), plus un IC bootstrap sur
   chacun. Le demeanage reste un **diagnostic** (montre que ce n'est pas la dérive du marché),
   il n'est **pas** une P&L : le jour-moyen utilise des barres postérieures à `t`, il n'est
   donc pas couvrable tel quel. La quantité tradable est toujours `net_bps`.
3. **Politique SHORT du projet non appliquée.** v2 : `side = −1` est plafonné à
   `PROMISING_NEEDS_VALIDATION` et étiqueté `deliverable_form = SHORT_SCREEN_OR_SPREAD_LEG_ONLY`.

Ajouts : block-bootstrap vectorisé (mêmes blocs = semaines, même graine 20260903), et une
**fenêtre OOS** (§4.2) qui n'existait pas.

## 4.1 Définitions (figées au préenregistrement)

Décision au close de `t` avec information ≤ `t`, entrée close `t`, sortie close `t+H`,
`H ∈ {1,4,8,24}`. `gross_bps = 1e4·(close[t+H]/close[t]−1)·side`, `net = gross − 14`,
stress `gross − 28`. Déclustering **L1** (≤ 1 épisode/symbole/24 h) → **L2** (jour calendaire)
→ **L3** (semaine). `t` et `n_required` sur L2 ; block-bootstrap par blocs L3.
`n_required = (1,96+0,84)²σ²/(0,5μ)²` (haircut 50 % obligatoire).
`event_rate` = épisodes L2/semaine sur les 6 derniers mois de la fenêtre.
`eta = n_required / event_rate`. Multiplicité déclarée : 20 tests principaux → Bonferroni
`t ≈ 3,0`, rapporté à côté du `t` brut (`passes_bonferroni` dans `RESULTS.json`).

## 4.2 Fenêtre hors échantillon (ajoutée à la reprise)

- **Découverte** : tout ce qui précède le **2026-01-01**.
- **OOS** : 2026-01-01 → 2026-06-29, **expurgée des périodes de source contaminée**
  identifiées par l'audit A8 (BTC dès 2026-01-01 ; ADA/AVAX/BNB/ETH/LINK/SOL dès 2026-05-20 ;
  DOGE/XRP dès 2026-05-24 ; DOT dès 2026-06-28). Ces lignes sont marquées
  `src_contaminated` dans `gate2.load()` et retirées de l'OOS.

Cette fenêtre n'avait **jamais été touchée** au moment où les hypothèses ont été figées : elle
constitue un vrai test hors échantillon, y compris pour les variantes de signe inversé.

---

# 5. RÉSULTATS (Phase 2)

> **Avertissement de lecture.** Les §5.1-5.2 donnent les résultats tels que le gate
> préenregistré les a produits. **Le §5.3 montre qu'ils sont faux** — un test placebo ajouté
> à la relecture prouve qu'un signal *aléatoire* obtenait le même edge. Le §5.4 donne les
> résultats corrigés, qui sont la conclusion réelle de ce worker. Les §5.1-5.2 sont conservés
> parce que l'écart entre les deux est le résultat le plus utile du round.

## 5.1 Ce que le gate préenregistré a rendu (AVANT correction)

| famille | hypothèse préenregistrée | découverte, H=24, contrôle JOUR | verdict d'alors |
|---|---|---|---|
| H1a | compression puis cassure haute → hausse | net −55,8 ; t = −14,5 | `DEAD`, signe inverse |
| H1b | compression puis cassure basse → baisse | net −76,4 ; t = −7,8 | `DEAD`, signe inverse |
| H2a | mèche haute p95 + volume p95 → réversion | net −13,2 ; t = 1,4 | `WEAK` |
| **H2b** | mèche basse p95 + volume p95 → réversion | **net +42,8 ; t = 8,5 ; ETA 2,70 ans** | **`VALIDATED_FOR_FORWARD`** |
| H3 | momentum 8 h paie plus en régime tendanciel | différence A−B = −19,9 ; t = −2,5 | `DEAD`, signe inverse |
| H4a | haut de range + volume faible → réversion | net +21,7 ; t = 12,7 | `PROMISING` (SHORT) |
| H4b | haut de range + volume fort → hausse | net −73,2 ; t = −14,7 | `DEAD`, signe inverse |
| H5 | interaction horaire | OOS `DATA_LIMITED` (n_L2 = 69-122) | `DATA_LIMITED` |

## 5.2 Le seul lauréat meurt déjà hors échantillon

| H2b, H=24 | découverte | OOS 2026 (source expurgée) |
|---|---|---|
| n brut / n indép. L2 | 11 226 / 1 825 | 1 219 / 158 |
| net_bps | +42,75 | **−8,23** |
| t déclusterisé (contrôle JOUR) | 8,49 | 1,34 |
| verdict | `VALIDATED_FOR_FORWARD` | `DEAD` |

À ce stade j'aurais pu livrer « un candidat, à valider ». Trois choses m'en ont empêché :
un edge de +42,8 bps nets par épisode à 6 épisodes indépendants/semaine implique un **Sharpe
annualisé de 6 à 8** (`evidence/PLAUSIBILITY.json`) ; l'effet était **monotone sur les
10 déciles** d'une seule variable, ce qui signifiait qu'il n'y avait pas 4 mécanismes mais un
seul ; et cet effet unique se retrouvait, **identique**, sur un panel entièrement différent.
Un edge trop beau, trop propre et trop universel est un edge fabriqué par l'estimateur.

## 5.3 ⚠ CE QUI FABRIQUAIT L'EDGE : le contrôle par jour calendaire

**Test placebo** (`evidence/placebo_and_decay.py`) : on permute le signal **entre symboles à
instant égal**. Le signal perd tout contenu informatif cross-sectionnel mais conserve
exactement la distribution temporelle des événements. Population, déclustering, demeanage,
horizon : tout le reste est identique.

| découverte, H=24, contrôle JOUR | vrai signal | **placebo** |
|---|---|---|
| décile bas, long | +82,4 bps (t = 16,6) | **+79,3 bps (t = 16,8)** |
| décile haut, short | +80,5 bps (t = 17,0) | **+76,0 bps (t = 18,6)** |
| placebo sur 10 graines | — | **+80,3 bps, écart-type 2,4** |

**Un signal aléatoire rend le même edge que le vrai signal.** L'edge est fabriqué par
l'estimateur, pas par les données.

Diagnostic (`evidence/artifact_diagnosis.py`, `artifact_isolation.py`) :

- Le **déclustering L1 n'est pas en cause** : sans aucune sélection, la moyenne demeanée
  déclusterisée vaut **+0,23 bps**.
- L'**effet horaire n'est pas en cause** : le rendement 24 h demeané par heure UTC d'entrée
  varie de −1,1 à +1,0 bps.
- Un tirage **uniforme sur toutes les barres** à 10 % rend **−1,4 bps**, pas +80.
- Mais un tirage **stratifié par barre horaire** — qui conserve *quand* les événements
  tombent — rend **+80 bps**.

→ **L'edge venait entièrement du QUAND, pas du QUI.** Les barres qui sont au bas de leur
range 20 h le sont *toutes en même temps* (après une baisse de marché) ; les 24 h suivantes
sont un rebond de marché. Le contrôle « moyenne du **jour calendaire** » ne l'enlève pas,
parce que les événements se concentrent sur quelques heures précises du jour alors que le
contrôle moyenne les 24 heures. Le résidu de facteur marché — jusqu'à **+80 bps** — se
retrouve intégralement dans l'« edge ».

**Le contrôle correct est la moyenne cross-sectionnelle à la MÊME BARRE HORAIRE.**
`evidence/control_level_test.py` le vérifie sur les deux panels :

| découverte, décile bas, long | brut | contrôle JOUR | **contrôle HEURE** |
|---|---|---|---|
| `enriched` — vrai signal | +81,7 | +82,4 (t = 16,6) | **−13,5 (t = −4,8)** |
| `enriched` — placebo | +81,4 | +82,8 (t = 18,0) | **+1,9 (t = 0,9)** ✅ |
| PIT (data-v2) — vrai signal | +106,6 | +91,3 (t = 18,7) | **−7,1 (t = −2,9)** |
| PIT (data-v2) — placebo | +87,3 | +72,4 (t = 16,8) | **+0,5 (t = 0,4)** ✅ |

Sous le contrôle horaire le placebo retombe à zéro (comme il le doit) et l'effet réel
**change de signe et perd 90 % de son amplitude**.

Cela explique aussi pourquoi le §1.3 (survivorship) et le §5.2 (rebond bid-ask) avaient été
« réfutés » : les deux tests étaient corrects, mais ils testaient un edge qui n'existait pas.
Ils sont conservés en annexe car ils restent des contrôles valides :
- **Survivorship** : rejoué sur l'univers PIT 312 symboles (délistés inclus) — même résultat.
- **Rebond bid-ask** : sauter 1 barre entre le signal et l'entrée ne coûte que ~12 % de
  l'edge (`evidence/BOUNCE_TEST.json`) ; la décroissance complète (skip 0→24 barres :
  +82 → +70 → +57 → +46 → +25 → +14 → **−10**) est celle d'une composante transitoire de
  demi-vie ~5 h — cohérente avec le résidu de facteur marché, pas avec le spread.

## 5.4 RÉSULTATS CORRIGÉS — contrôle horaire, chaque bras doublé d'un placebo

`evidence/rerun_corrected.py` → `RESULTS.json` (`phase2_rerun_corrected`).
Validation de l'estimateur : sur les **48 bras placebo**, |t| médian **0,57**, p90 **1,31**,
max **3,09** ; |edge| max **9,3 bps**. L'estimateur corrigé ne fabrique plus d'edge.

**Découverte, H = 24 :**

| mécanisme | net_bps (tradable) | edge contrôle JOUR | **edge contrôle HEURE** | t | IC95 | ex-meilleure année |
|---|---:|---:|---:|---:|---|---:|
| H1a compression → cassure haute, long | −55,8 | −87,9 | +7,1 | 1,62 | [−1,1 ; 15,6] | +5,1 |
| H1b compression → cassure basse, short | −76,4 | −64,7 | +2,5 | 0,36 | [−12,1 ; 14,9] | −1,5 |
| H2a mèche haute, short | −13,2 | +17,4 | −23,9 | −1,98 | [−51,7 ; −3,6] | −24,7 |
| **H2b mèche basse, long** | **+42,8** | **+68,4** | **−4,7** | **−0,83** | [−15,8 ; 6,5] | −4,5 |
| **H3a momentum 8 h, régime tendanciel** | −48,4 | −60,4 | **+14,4** | **3,72** | [7,2 ; 22,1] | +12,7 |
| H3b momentum 8 h, régime haché | −33,9 | −47,1 | −4,9 | −1,79 | [−10,0 ; −0,1] | −7,7 |
| H4a haut de range, volume faible, short | +21,7 | +121,8 | −8,4 | −1,07 | [−25,3 ; 9,1] | −16,3 |
| **H4b haut de range, volume fort, long** | −73,2 | −105,5 | **+18,3** | **3,39** | [7,8 ; 29,6] | +12,3 |

**Deux renversements de conclusion par rapport au §5.1 :**

1. **H2b, le seul `VALIDATED_FOR_FORWARD`, tombe à −4,7 bps, t = −0,83.** Il n'y avait rien.
   Ses +42,8 bps nets étaient du facteur marché résiduel.
2. **H3 et H4b, déclarés « signe inverse », sont en fait dans la direction préenregistrée.**
   Le momentum 8 h paie bien **plus** en régime tendanciel qu'en régime haché
   (+14,4 contre −4,9 ; différence ≈ +19 bps) et le haut de range confirmé par le volume est
   bien **haussier** (+18,3). Le contrôle par jour calendaire avait **inversé le signe de la
   conclusion préenregistrée**. C'est l'illustration la plus nette du §5.3.

**Mais ni l'un ni l'autre n'est livrable :**

- Leur edge (+14,4 et +18,3 bps) est **hour-neutral** : il n'existe que relativement à la
  coupe transversale de la même barre. La jambe directionnelle est franchement négative
  (net −48,4 et −73,2 bps). Le livrable serait un **overlay market-neutral**, pas un alpha
  directionnel — et le projet n'a pas de brique d'exécution market-neutral horaire.
- Contre le plancher de coût du projet : 14,4 − 14 = **+0,4 bps** ; 18,3 − 14 = **+4,3 bps**,
  tous deux **négatifs au stress 28 bps** → `COST_FRAGILE`.
- Contre Bonferroni (20 tests principaux déclarés, t ≈ 3,0) ils passent de justesse
  (3,72 et 3,39), mais le placebo atteint |t| = 3,09 sur 48 tirages : **le seuil de bruit
  empirique de cet estimateur est de l'ordre de t ≈ 3**. Ils ne s'en détachent pas.
- **Aucun des deux ne survit hors échantillon** : H3a passe à −0,4 bps (t = −0,04) et H4b à
  +22,5 bps (t = 1,51) sur 2026. Aucun mécanisme n'a |t| > 1,6 en OOS.

## 5.5 Table finale des verdicts

| mécanisme | H | net14 | net28 | edge horaire | t | n_L2 | n_L3 | event/sem | ETA | **verdict livré** |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| H1a compression breakout long | 24 | −55,8 | −69,8 | +7,1 | 1,6 | 2257 | 431 | 6,00 | — | `DEAD` |
| H1b compression breakdown short | 24 | −76,4 | −90,4 | +2,5 | 0,4 | 2245 | 428 | 6,65 | — | `DEAD` |
| H2a upper wick exhaustion short | 24 | −13,2 | −27,2 | −23,9 | −2,0 | 1979 | 407 | 6,12 | — | `DEAD` |
| H2b lower wick exhaustion long | 24 | +42,8 | +28,8 | **−4,7** | −0,8 | 1825 | 416 | 5,65 | — | `DEAD` (artefact §5.3) |
| H3a momentum 8 h trending | 24 | −48,4 | −62,4 | +14,4 | 3,7 | 2590 | 436 | 6,96 | — | `COST_FRAGILE` |
| H3b momentum 8 h choppy | 24 | −33,9 | −47,9 | −4,9 | −1,8 | 2791 | 437 | 7,00 | — | `DEAD` |
| H4a unconfirmed range high short | 24 | +21,7 | +7,7 | −8,4 | −1,1 | 1447 | 372 | 5,12 | — | `DEAD` (artefact §5.3) |
| H4b confirmed range high long | 24 | −73,2 | −87,2 | +18,3 | 3,4 | 2237 | 422 | 6,35 | — | `COST_FRAGILE` |
| H5 interaction horaire × H2b | 1-24 | — | — | — | — | 69-122 (OOS) | — | — | — | `DATA_LIMITED` |
| SCREEN position dans le range (déciles) | 8/24 | — | — | −13,5 à +9,7 | ≤ 4,8 | 2441-2745 | — | ~6,8 | — | `DEAD` (artefact §5.3) |

**`VALIDATED_FOR_FORWARD` : 0.** Les `ETA` ne sont pas reportées pour les mécanismes morts :
l'ETA d'un edge nul n'a pas de sens. Pour les deux `COST_FRAGILE`, l'ETA calculée sur l'edge
horaire haircuté de 50 % (définition du préenregistrement) est rédhibitoire :

| | edge horaire | σ_L2 | `n_required` | `event_rate` | **`eta_forward`** |
|---|---:|---:|---:|---:|---:|
| H3a momentum 8 h trending | 14,4 bps | 197,6 bps | 5 872 jours L2 | 6,96/sem | **16,2 ans** |
| H4b confirmed range high long | 18,3 bps | 255,9 bps | 6 105 jours L2 | 6,35/sem | **18,5 ans** |

Ils seraient donc de toute façon `UNCONFIRMABLE_IN_HORIZON`. C'est le même mur que celui déjà
rencontré par le projet sur Amihud (~17 ans) et le momentum cross-sectionnel : **la cadence
horaire donne bien la fréquence d'épisodes espérée (~7/semaine), mais l'edge résiduel après
un contrôle correct est trop petit devant sa variance pour être confirmable.**

---

# 6. CE QUE J'AI TUÉ, ET POURQUOI

1. **H1 (compression → expansion directionnelle)** — `DEAD`. Ni dans le sens préenregistré ni
   dans l'autre une fois le contrôle corrigé (+7,1 bps, t = 1,6).
2. **H2a / H2b (épuisement par la mèche)** — `DEAD`. H2b passait le gate complet
   (+42,8 bps, t = 8,5) ; il tombe à −4,7 bps (t = −0,83) sous contrôle horaire. Le contrôle
   apparié « mèche vs pas de mèche » qui semblait significatif (+48,7 bps, t = 5,6) est le
   même artefact.
3. **H3 (filtre de régime par efficiency ratio)** — `COST_FRAGILE`. La différence entre bras
   existe (+19 bps) et va dans le sens préenregistré, mais elle est hour-neutral, sous le
   plancher de coût, et nulle hors échantillon.
4. **H4 (confirmation par le volume au haut de range)** — `COST_FRAGILE` pour la jambe longue,
   `DEAD` pour la jambe short. La différence entre bras (« la non-confirmation ajoute-t-elle
   à la réversion ? ») est **non significative** aux deux contrôles.
5. **H5 (interaction horaire)** — `DATA_LIMITED`, l'OOS n'a que 69-122 jours L2 par bucket.
6. **Le « screen de position dans le range »** — le candidat le plus spectaculaire du worker
   (monotone sur 10 déciles, +115 bps, t = 17,5, répliqué sur 2 panels et hors échantillon)
   — **`DEAD`, artefact d'estimateur à 96 %**.

# 7. RECOMMANDATIONS

**Pour le coordinateur du round — action prioritaire.**
Tout worker de ce round travaillant sur un panel **intraday** avec un contrôle
« moyenne du jour calendaire » a le même biais. Ordre de grandeur mesuré ici : **jusqu'à
+80 bps d'edge entièrement fictif**, avec des t-stats de 16 à 20 et une stabilité annuelle
parfaite. Deux gestes suffisent à trancher, pour un coût de calcul négligeable :
1. **contrôler à la barre, pas au jour** (moyenne cross-sectionnelle au même timestamp) ;
2. **doubler chaque bras d'un placebo** (permutation du signal entre symboles à instant égal)
   et exiger que le placebo soit nul. Un mécanisme dont le placebo fait feu est mort, quels
   que soient son t et sa stabilité.

**Pour les workers futurs sur `data/enriched`** : §1.7.

**Pour le projet** : la piste « fréquence d'épisodes élevée » que ce worker devait explorer
n'est pas invalidée — la cadence horaire × 49 symboles donne bien ~7 épisodes indépendants
L2/semaine et des ETA < 1 an. Mais aucun des mécanismes testés ici n'a d'edge réel à cette
cadence. L'obstacle n'est pas la fréquence, c'est que **les effets intraday survivants après
un contrôle correct sont de l'ordre de 5-20 bps**, c'est-à-dire au niveau du plancher de coût
de 14 bps. Toute recherche intraday utile pour ce projet doit donc commencer par **abaisser
le coût** (exécution maker — `data/execution_probe`, jamais utilisée comme couche de coût),
pas par chercher un bps plus gros.
