# ALPHA HUNT ROUND 4 — BRIEFING COMMUN (2026-09-03)

Lis ce fichier EN ENTIER avant de commencer. Il remplace toute supposition.

## 0. Ce qui change par rapport aux rounds 1-3

Rounds 1-3 (`reports/edge_discovery/alpha_hunt_2026-08-29/`, `alpha_hunt_2026-08-30/`,
`alpha_hunt_2026-09-01_round3/`) ont été des rounds de **largeur** : ~146+ mécanismes
essayés, verdicts `PROMISING` / `WEAK` / `DEAD` rendus sur la base de bps nets et d'une
stabilité par année. Résultat : sur ~17 candidats sérieux passés ensuite en validation
indépendante (`configs/validation_registry.yaml`), **5 ont été REJETÉS et 1 renvoyé en
NEEDS_MORE_RESEARCH parce que le déclustering détruisait la preuve**. Le goulot
d'étranglement du projet n'est PAS le nombre d'idées : c'est le nombre d'idées qui
survivent au protocole de validation.

**Round 4 est un round de PROFONDEUR.** Un mécanisme n'est un livrable que s'il a été
poussé jusqu'au verdict de validation, pas jusqu'au bps. Un worker qui rend 30 mécanismes
`PROMISING` non validés a échoué. Un worker qui rend 3 mécanismes dont 1 passe le gate
complet a réussi.

## 1. Discipline scientifique — NON NÉGOCIABLE

Ces règles ont été payées cher (4 workers brûlés sur le déclustering au round 2, 5 rejets
au round de validation). Toute violation invalide ton rapport.

1. **PIT strict.** Toute condition de déclenchement n'utilise que des données disponibles
   à `event_time` ou avant. Les fenêtres A→B sont *forward-only*. Les features roulantes
   sont causales par construction. Si tu ne peux pas prouver la causalité d'un champ, ne
   l'utilise pas — ou stampe le résultat `PIT_UNVERIFIED`.
2. **Déclustering à 3 niveaux, TOUJOURS.** Reporte N brut ET N indépendant sous trois
   unités : (a) même-symbole / fenêtre 24h, (b) jour calendaire (tous symboles), (c)
   l'unité macro naturelle du mécanisme (régime de vol, semaine, épisode). Le crypto est
   massivement clusterisé : un « N=2000 » est régulièrement un N indépendant de 200. Le
   piège du déclustering a été redécouvert 4 fois dans ce projet. Ne le redécouvre pas :
   décluste dès le premier calcul.
3. **Comparer les signaux entre eux, pas à zéro.** Ce marché a une dérive inconditionnelle
   forte. Un conditionnement de régime se juge sur `bras_A − bras_B` sur la même
   population, jamais sur « le bras A est positif ».

   ⚠ **CORRECTIF (2026-09-05, après le round 4 — cette consigne était incomplète et a
   produit de faux positifs).** Dire « comparer les bras entre eux » ne suffit pas : il
   faut préciser **l'unité d'agrégation du contrôle**. Contrôler par la moyenne du
   **jour calendaire** ne retire PAS le facteur marché dès que les événements sont
   concentrés dans le temps à l'intérieur de cette unité. W9 l'a mesuré : un signal
   ALÉATOIRE obtenait **+79,3 bps** là où son vrai signal obtenait +82,4 ; son mécanisme
   H2b passait de +42,8 bps (t=8,5, ETA 2,7 ans) à **−4,7 bps (t=−0,83)** une fois le
   contrôle ramené à la **barre horaire**. Le biais gonfle, il ne déprime pas — il ne
   peut donc pas créer de faux négatif, mais il fabrique des faux positifs à t=16-20.

   Règle : **contrôler à la granularité de la barre**, pas du jour. Un bras dont les
   épisodes sont uniformément répartis sur 24 h n'est pas exposé ; un bras déclenché par
   du stress de marché l'est massivement, parce que c'est exactement la corrélation entre
   l'heure de déclenchement et le rendement du facteur qui se retrouve dans l'edge.

   **Placebo obligatoire, et lequel** : doubler chaque bras d'un signal aléatoire sur la
   même population et le même contrôle. La bonne construction est une **permutation
   cross-symbole à instant égal** — elle conserve le « quand » et ne détruit que le
   « qui », donc elle isole précisément cette composante. Si le placebo capte une part
   significative de l'edge, l'edge est le facteur, pas le signal.
4. **Coûts.** Convention projet : `net_bps = gross_bps − 14` (5bps taker + 2bps slippage,
   aller-retour). Stress obligatoire : `− 28`. Un mécanisme qui ne survit pas au stress
   n'est pas `PROMISING`, il est `COST_FRAGILE`.
5. **Préenregistrement.** Écris tes hypothèses et tes seuils dans `PREREGISTRATION.md`
   AVANT de lancer les tests. Tout seuil ajusté après avoir vu le résultat est un
   refit : soit tu le déclares (`REFIT`), soit tu le testes sur une période disjointe.
6. **Stabilité temporelle.** Toujours une décomposition par année. Un edge concentré sur
   une seule année (typiquement 2021 ou 2025) est `REGIME_DEPENDENT`, pas `PROMISING`.

## 2. Le gate de validation (ce qui fait la profondeur du round 4)

Pour CHAQUE mécanisme que tu classes mieux que `WEAK`, tu dois produire :

| champ | ce que c'est |
|---|---|
| `n_raw` / `n_independent_L1/L2/L3` | tailles d'échantillon aux 3 niveaux de déclustering |
| `net_bps` / `net_bps_stress28` | edge net et sous stress de coût |
| `t_stat_declustered` | t calculé sur les épisodes INDÉPENDANTS, pas sur N brut |
| `bootstrap_ci95` | IC 95% par block-bootstrap (blocs = unité de déclustering) |
| `year_by_year` | table par année |
| `ex_best_year` | l'edge en retirant la meilleure année (test de concentration) |
| `n_required` | N indépendant requis pour confirmer forward à power 80%, alpha 5%, **sur un edge haircuté de 50%** (le haircut est obligatoire : la découverte surestime) |
| `event_rate` | épisodes indépendants / semaine, mesuré sur les 6 derniers mois (conservateur) |
| `eta_forward_confirmation` | `n_required / event_rate`, en jours ET en années |
| `verdict` | voir §3 |

**`eta_forward_confirmation` est le champ le plus important du round.** Le projet a
découvert au round de validation que plusieurs edges « significatifs » demandaient 9 à 46
ans de forward pour être confirmés — ils sont donc inutilisables comme alphas confirmables,
quel que soit leur bps. Un mécanisme avec un ETA > 3 ans doit être classé
`UNCONFIRMABLE_IN_HORIZON`, même si son bps est superbe. **Cherche activement des
mécanismes à haute fréquence d'épisodes indépendants** — c'est le vrai critère manquant
dans ce projet.

## 3. Verdicts autorisés (n'en invente pas d'autres)

- `VALIDATED_FOR_FORWARD` — passe tout le gate §2, ETA < 3 ans, survit au stress 28bps,
  pas concentré sur une année, déclusterisé. **C'est le seul livrable qui compte vraiment.**
- `PROMISING_NEEDS_VALIDATION` — edge réel mais une case du gate manque (dis laquelle).
- `UNCONFIRMABLE_IN_HORIZON` — edge peut-être réel, ETA rédhibitoire. Documente l'ETA.
- `COST_FRAGILE` — meurt entre 14 et 28bps.
- `REGIME_DEPENDENT` — ne vit que dans un régime/une année.
- `WEAK` / `DEAD` — pas d'edge.
- `DATA_LIMITED` — le dataset ne permet pas de trancher (dis ce qu'il faudrait).

Un verdict négatif honnête et bien étayé vaut mieux qu'un `PROMISING` mou. Le projet a
explicitement valorisé les KILL honnêtes (Event Scanner V1 : 4/4 KILL, 0% retuning).

## 4. Ce qui est DÉJÀ connu — ne le redécouvre pas

Lis ces fichiers avant de commencer (ils te diront ce qui a déjà été essayé) :
- `reports/edge_discovery/alpha_hunt_2026-08-30/SCOREBOARD.md` (round 2, ~146 mécanismes)
- `reports/edge_discovery/alpha_hunt_2026-09-01_round3/w*/REPORT.md` (round 3, ton axe voisin)
- `reports/edge_discovery/validation_2026-09/VALIDATION_AND_FORWARD_SCOREBOARD.md`
- `configs/validation_registry.yaml` (verdicts de validation + raisons de rejet)
- `configs/live_alpha_registry.yaml` (les 16 alphas déjà figés/en shadow)

Résultats structurants déjà acquis (les REPRENDRE tels quels, ne pas les re-tester) :
- **Les cascades de liquidation ne paient que sur répétition** (1re occurrence ≈ −6 à
  −19bps, 3e+ ≈ +42 à +87bps). Corroboré 2 fois. Déjà en shadow (`LIQ_CASCADE_REPEAT_V1`).
- **Funding / basis est épuisé par l'arbitrage 2025-26** — les edges y sont fins et rares.
- **Le momentum cross-sectionnel 7d→7d existe** (+89bps) mais est hebdomadaire → ETA
  catastrophique. Même chose pour Amihud (+105bps validé, ETA ~17 ans).
- **La microstructure haute fréquence est DATA_LIMITED** : `market_physics_v3` = 2 jours,
  `microstructure_reduced` a démarré le 2026-08-31. Tout résultat y est mono-régime.
- **La sonde d'exécution est DEAD en standalone** (A16) — mais pas testée comme couche de
  coût (voir W8).

## 5. Contraintes matérielles — IMPORTANT

- **Disque à 97% (28 Go libres).** N'écris AUCUN gros intermédiaire dans `data/` ou
  `reports/`. Utilise ton scratch, garde les parquets intermédiaires < 1 Go au total, et
  nettoie derrière toi. Un worker qui remplit le disque casse les collecteurs live.
- **Lecture seule** sur tout `data/`, `reports/live_alpha_lab/`, `src/institutional/`,
  `configs/live_alpha_registry.yaml`. Tu n'écris QUE dans ton dossier
  `reports/edge_discovery/alpha_hunt_2026-09-03_round4/<ton_id>/`.
- **Ne touche JAMAIS** : la TRM Fleet, les alphas FROZEN, les services systemd, le
  worktree `/home/qbee/futur-data-v2` en écriture.
- Python : `.venv` (python3.8) à la racine. DuckDB disponible et recommandé pour les
  panels larges.

## 6. Inventaire des données disponibles

| chemin | taille | contenu |
|---|---|---|
| `data/enriched/*_1h_enriched.parquet` | 86 Go | 61 symboles, barres 1h enrichies. ⚠ PIÈGE CONNU : les colonnes `taker_buy_*` sont des placeholders dans certains fichiers — vérifie avant usage |
| `data/derivatives_raw/` | 16 Go | forceOrder / OI / funding / taker, Binance USDM, collecte continue |
| `data/derivatives_backfill/` | 5,7 Go | Vision quarterly + metrics (LSR, taker ratios) |
| `data/hyperliquid/{trades,l2,ctxs,twap}` | 3,5 Go | métaordres HL, TWAP history — **sous-exploité** |
| `data/microstructure_reduced/raw/{bbo,trades}` | 2,4 Go | BBO + trades, binance/okx/HL, BTC/ETH/SOL, depuis 2026-08-31 |
| `data/options_backfill/deribit/` | 585 Mo | trades BTC, DVOL BTC/ETH |
| `data/execution_probe/date=*/` | 214 Mo | sonde de fill maker (ordres post-only virtuels) depuis 2026-07-12 |
| `data/listings_backfill/binance/` | 70 Mo | dates de listing/delisting — **jamais miné** |
| `data/positioning/` | 48 Mo | top/global LSR, taker ratios |
| `data/news_raw/date=*/` | 16 Mo | RSS + Fear&Greed + CoinGecko — **jamais miné** |
| `data/events/*.parquet` | 35 Mo | liq_cascade, cascade, crowding, ignition, premium, spillover |
| `/home/qbee/futur-data-v2/data_v2/normalized/` | — | panel PIT 312 symboles : perp_ohlcv, spot_ohlcv, basis, agg_trades, agg_trades_flow, event_feature_panel |
| `/home/qbee/futur-data-v2/data/market_physics_v3/raw/` | — | book_events + trades, 2 jours (2026-08-15, 2026-08-28) |

## 7. Livrables (dans `reports/edge_discovery/alpha_hunt_2026-09-03_round4/<ton_id>/`)

1. `PREREGISTRATION.md` — hypothèses et seuils, écrit AVANT les tests.
2. `REPORT.md` — méthodologie, table de TOUS les mécanismes testés avec les colonnes du
   gate §2, verdicts, et une section « ce que j'ai tué et pourquoi ».
3. `RESULTS.json` — machine-readable : une entrée par mécanisme avec tous les champs du §2.
4. `evidence/` — tes scripts (ils doivent être ré-exécutables) et tes JSON de résultats.

Cible : **profondeur avant largeur**. 8 à 20 mécanismes poussés jusqu'au gate complet vaut
mieux que 40 mécanismes survolés. Si tu trouves UN mécanisme `VALIDATED_FOR_FORWARD` avec
un ETA < 1 an, c'est le meilleur résultat possible du round.

---

## 8. ADDENDUM 2026-09-03 10:20 — discipline ressources RENFORCÉE (lancement réel du round)

Le round tourne avec **16 workers simultanés** (8 hunt + 8 validation) sur une machine partagée
avec les collecteurs live. État mesuré au lancement : disque **27,2 Go libres** (déjà en zone
WARNING du watchdog), RAM 31 Go dont ~17 Go disponibles, 16 cœurs.

**Watchdog disque global (`scripts/global_disk_watchdog.py`) :** WARNING < 30 Go (log seul),
**CRITICAL < 20 Go → il ARRÊTE les collecteurs live**, EMERGENCY < 12 Go. Un worker qui écrit
7 Go casse la collecte microstructure/dérivés de tout le projet. Donc :

1. **Scratch ≤ 250 Mo par worker à tout instant**, exclusivement dans
   `/tmp/claude-1000/-home-qbee-futur/df793692-b596-4e93-91e2-bc55f257c909/scratchpad/<ton_id>/`
   (déjà créé). Jamais dans le scratch d'un autre worker, jamais dans `/tmp` racine.
2. Avant toute écriture > 50 Mo : `df -h /`. Si libre < 24 Go : n'écris plus AUCUN intermédiaire,
   passe en requêtes DuckDB streaming directement sur les parquets sources.
3. Tu peux supprimer UNIQUEMENT les fichiers intermédiaires que TOI tu as créés dans TON scratch.
   Rien d'autre, nulle part (règle projet absolue : aucune suppression de données/rapports/runs).
4. **DuckDB, à chaque connexion :** `SET memory_limit='1500MB'; SET threads=2;`
   **Pandas :** toujours `columns=[...]`, filtrer par symbole/année, jamais `read_parquet` entier
   d'un fichier > 1 Go. `agg_trades_flow` (52 Go) et `event_feature_panel` (14 Go) : DuckDB
   seulement, avec prédicats de partition.
5. Avant toute étape estimée > 1 Go RAM : `free -g` → si « available » < 4 Go, `sleep 60` et
   réessaye (max 10 fois). Jamais de pool multiprocessing > 2 process.
6. Python : `.venv/bin/python` (3.8 ; duckdb 1.3.2, pandas 2.0.3, numpy 1.24.4). Jamais `python`.
7. Interdit d'écrire dans `src/`, `tests/`, `configs/`, `scripts/`, `data/`,
   `reports/live_alpha_lab/`. Tes scripts vont dans `<ton_dossier>/evidence/`.
8. `PREREGISTRATION.md` écrit dans les 30 premières minutes. `REPORT.md` écrit
   PROGRESSIVEMENT (un résultat partiel écrit vaut mieux qu'un worker perdu). Si l'outil `Write`
   refuse un fichier nommé `REPORT.md`, passe par un heredoc Bash.
9. Coût : pour un mécanisme à rebalancement (quotidien/intraday), le coût est compté sur le
   TURNOVER réel (aller-retour effectivement exécuté), pas par signal — dis-le explicitement.
10. Pièges data connus : `taker_buy_*` placeholders dans certains enriched ; `liquidationSnapshot`
    cm-only → 2024-10 ; `data/derivatives_raw` `timestamp` = epoch **millisecondes** ;
    PEPEUSDT→1000PEPEUSDT, RNDRUSDT→RENDERUSDT renommés ; MKRUSDT délisté ; âge de listing
    ≥ 30 j (même seuil que ListingAgeGate) ; univers qui grandit (28→49 frozen-50, 0→411→283
    PIT) — toute densité cross-symbole avant 2022 est un artefact de couverture.
11. **Politique SHORT du projet : pas de short directionnel standalone** (SHORT_REJECTED,
    mai 2026). Un résultat « short-shaped » n'est livrable que comme SCREEN/GATE ou comme jambe
    de couverture d'un spread relative-value (précédent AMIHUD_ILLIQUIDITY_PREMIUM_V1) — et dans
    ce cas la jambe LONG seule doit TOUJOURS être reportée séparément.
12. Rapport final au coordinateur (ta dernière réponse) : ≤ 40 lignes — table
    mécanisme × verdict × net14/net28 × N_indep_L3 × ETA, chemins des livrables, bugs/pièges
    trouvés, incidents ressources. Pas de prose longue : tout le détail est dans REPORT.md.

## 9. NOTE DE COORDINATION (2026-09-03 10:25) — axes H1-H8 RETIRÉS

Les axes H1-H8 initialement rédigés ici ont été retirés : au moment de leur rédaction, une
session concurrente (`d5f771d6`, démarrée 10:06) avait déjà lancé le round 4 complet avec les
workers `w1_calendar_clock`, `w2_hyperliquid_flow`, `w3_listings_lifecycle`, `w4_news_sentiment`,
`w5_execution_cost_layer`, `w6_high_frequency_episodes`, `w7_options_vol_surface`,
`w8_signal_ensembling`, `w9_enriched_deep_mine`, `w10_validation_push` (10:12-10:17), qui
couvrent ces axes. Les dossiers vides `h*_...` créés par erreur ont été déplacés dans
`_unused_h_axes_superseded_by_w1-w10/` (rien supprimé). Le §8 (discipline ressources) reste
valable pour tout worker de ce round.

La **campagne de validation wave 2** (8 candidats/paires jamais validés indépendamment) tourne
en parallèle depuis la session `df793692` : voir
`reports/edge_discovery/validation_2026-09/VALIDATION_BRIEFING_WAVE2.md`. Ses workers écrivent
uniquement sous `reports/edge_discovery/validation_2026-09/<CANDIDATE_ID>/`.
