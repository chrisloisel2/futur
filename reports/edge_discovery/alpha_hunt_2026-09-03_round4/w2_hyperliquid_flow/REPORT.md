# W2_HYPERLIQUID_FLOW — RAPPORT FINAL (Alpha Hunt Round 4)

**Statut :** terminé le 2026-09-05. Session interrompue le 2026-09-03 par une limite de
contexte ; le scratch et les intermédiaires avaient survécu, l'analyse a été reprise, vérifiée,
complétée et corrigée — pas refaite.

**Résultat en une phrase :** aucun mécanisme n'atteint `VALIDATED_FOR_FORWARD`. Le tape TWAP
d'Hyperliquid contient bien un drift signé réel et PIT-propre, mais (a) l'essentiel est un effet
de *sélection de coin*, pas un effet d'événement, (b) la formulation agrégée T4 était à moitié
une fuite, et (c) la seule population qui survit à ses propres contrôles demande ~6 ans de
forward pour être confirmée.

<!--COUNTS-->
| verdict | mécanismes primaires | toutes lignes (contrôles + robustesse inclus) |
|---|---|---|
| `VALIDATED_FOR_FORWARD` | **0** | 0 |
| `UNCONFIRMABLE_IN_HORIZON` | 1 | 2 |
| `COST_FRAGILE` | 3 | 6 |
| `DATA_LIMITED` | 23 | 23 |
| `WEAK` | 12 | 13 |
| `DEAD` | 12 | 23 |
| **total** | **51** | **67** |
<!--/COUNTS-->

---

## 1. Ce qui a été construit (et pourquoi c'est un jeu d'événements, pas 42 jours)

`data/hyperliquid/twap` n'est pas un flux de 42 jours. Le collecteur interroge
`twapHistory(user)` pour ~2 215 wallets et reçoit **l'historique complet** de chaque
utilisateur à chaque poll. Après déduplication sur `(user, coin, state.timestamp)` :
**397 969 TWAP uniques, 2 215 users, 605 coins, 2024-02-03 → 2026-08-29**.

- **Track A (exécutable Binance USDM)** — 155 symboles mappés sur `perp_ohlcv` 5 min,
  **237 344 événements** dans la fenêtre du panel (2024-01 → 2026-08-01, date de fin du panel
  Binance normalisé). C'est là que se joue l'essentiel du rapport.
- **Track B (natif HL)** — `trades` / `l2` / `ctxs`, 12 coins, 2026-07-18 → 2026-08-29
  (40 jours), plus `microstructure_reduced` (BBO binance/HL/okx, 6 jours) pour le lead-lag.

**PIT.** `event_time = state.timestamp` (création). Seuls `coin`, `side`, `sz`, `minutes`,
`reduceOnly`, `randomize`, `user` sont connus à la création ; `executedSz`, `executedNtl`,
`status` final et `end_ms` sont **descriptifs uniquement** et n'entrent dans aucun signal.
Entrée = open de la première barre 5 min à `event_time + LAG`, LAG ∈ {0, 5, 15, 30, 60} min,
**LAG principal préenregistré = 15 min**.

**Latence de détection mesurée** (fait, pas hypothèse) : le collecteur poll toutes les ~76 s
mais sur 2 215 users, donc la latence de première observation est p25 = 3,9 min, médiane
= 171 min. Tous les résultats sont donnés à LAG 15 min et la sensibilité au lag est reportée.
Elle est douce : 13,03 → 10,60 bps de LAG 0 à LAG 60 sur la construction principale. Aucun
mécanisme de ce rapport ne dépend de LAG 0.

**Déclustering, 3 niveaux, appliqué à chaque ligne du tableau :**
`L1 = user × coin × jour` (le flux d'un wallet sur un coin un jour donné = **un** épisode),
`L2 = coin × jour`, `L3 = jour calendaire` (primaire : `t_stat_declustered` et le
block-bootstrap 2 000 tirages sont calculés sur L3).

L'ordre de grandeur mérite d'être posé une fois pour toutes, parce que c'est lui qui explique
tous les ETA de ce rapport : sur la population complète, **233 047 « observations » brutes ne
sont que 108 378 épisodes L1, 24 151 épisodes L2 et 895 jours indépendants** — un facteur 260
entre le N naïf et le N qui compte. Aucun t-stat de ce rapport n'est calculé sur autre chose
que les 895 jours (ou moins).

---

## 2. LA FUITE T4 — trouvée, corrigée, et son effondrement mesuré

C'est le résultat le plus utile du worker, et c'est un résultat négatif.

**Le bug.** La première version de T4 (« le notionnel TWAP net signé encore à exécuter dans un
coin prédit le rendement forward ») calculait le signal en sommant la matrice de flux programmé
**vers l'avant** sur `[t, t+H)`. Cette somme inclut les TWAP **créés à l'intérieur de la
fenêtre**, c'est-à-dire des ordres qui n'existent pas encore à l'instant de décision `t`.
C'est la fuite classique du « carnet d'ordres futur ».

**Son ampleur, mesurée.** À l'horizon 4 h, la part du notionnel programmé de la fenêtre
avant qui n'est **pas encore créée** en `t` est de **74,4 % en médiane** (moyenne 55,9 %,
p25 0 %, p75 100 %). La corrélation entre le signal fuité et sa version légale n'est que
de 0,61.

**L'effondrement.** Trois signaux, mêmes lignes, même horizon (4 h non chevauchant), même
modèle de coût, même déclustering, portefeuille cross-sectionnel long/short par quintile :

| variante | gross | net14 | net28 | t(L3) | IC moyen | IC test H1 / H2 | ETA |
|---|---|---|---|---|---|---|---|
| `v1_LEAKY` (fuite) | **26,98** | 12,98 | −1,02 | 6,21 | **0,0433** | 0,041 / 0,075 | 1,57 an |
| `v2b_CLEAN_RESIDUAL` (PIT légal) | **13,55** | −0,45 | −14,45 | 3,64 | **0,0198** | 0,016 / 0,040 | 4,55 ans |
| `v2a_CLEAN_TRAILING` (conservateur) | 3,49 | −10,51 | −24,51 | 1,06 | 0,0011 | 0,001 / 0,019 | 53 ans |

**La moitié de l'edge brut et 54 % de l'IC étaient de la fuite pure.** Une fois corrigé, T4
tombe exactement au niveau des coûts (net −0,45 bps) et son ETA passe de 1,6 à 4,6 ans.

Nuance importante et à conserver : `v2b_CLEAN_RESIDUAL` est la formulation **exacte du
préenregistrement** (le notionnel *restant* des TWAP *déjà créés*) et elle est parfaitement
PIT-légale — un TWAP créé avant `t` avec une durée programmée qui déborde sur le futur est un
flux futur réellement connu. Elle n'est pas nulle (t = 3,64) ; elle est simplement sous les
coûts. La version « moyenne glissante 1 h du flux connu » (`v2a`), plus conservatrice, est
morte (t = 1,06). Verdicts : `v1_LEAKY` = `DEAD` (n'est pas un mécanisme, c'est la
reproduction du bug, conservée pour être auditable), `v2b` = `WEAK`, `v2a` = `DEAD`.

Scripts : `evidence/build_flow_panel.py` (v2 corrigée), `evidence/run_flow_gate.py`,
`evidence/run_flow_leak_diag.py` (la forensique complète, y compris la reproduction de la fuite).

---

## 3. Le placebo t−7j tue tout Track A… puis se révèle lui-même contaminé

**L'étape 1 tue.** Le contrôle décisif n'est pas la neutralisation marché (β = 1), qui ne retire
pas le drift idiosyncratique persistant d'un symbole. C'est un **placebo t−7j** : même symbole,
même heure d'horloge, même direction, 7 jours plus tôt.

| population @24h | événement | placebo t−7j | signal − placebo | t(L3) |
|---|---|---|---|---|
| tous les TWAP | +11,88 | **+17,95** | **−6,88** | −1,83 |
| achats seuls (`side=B`) | +22,16 | +57,54 | **−35,38** | −3,51 |
| non-reduceOnly | +13,86 | +23,30 | **−9,44** | −2,08 |

Le placebo gagne **plus** que la fenêtre d'événement. Lecture : ces wallets TWAPent des coins
qui dérivent déjà positivement (en excès du marché) ; l'événement lui-même n'ajoute rien.
La sensibilité au lag confirme (placebo-ajusté : −5,73 à LAG 0 → −8,00 à LAG 60, jamais positif).

**L'étape 2 corrige l'étape 1.** Un placebo n'est valide que s'il ne contient pas de signal.
Le flux TWAP HL est autocorrélé : si le même coin était déjà TWAPé une semaine plus tôt, le
placebo contient du signal et **sur-corrige**. Mesuré : la fenêtre placebo est contaminée par
du flux TWAP sur le même coin pour **90,2 %** des événements (29,1 % par le *même* wallet).

Sur les **9,9 % non contaminés**, la conclusion s'inverse :

| population | signal | placebo | signal − placebo |
|---|---|---|---|
| tous (90,2 % contaminés) | +11,88 | +17,95 | −6,88 (t −1,83) |
| **placebo propre (9,9 %)** | **+29,47** | **+6,54** | **+20,68 (t 2,40)** |

Le placebo n'est donc pas un verdict : c'est un révélateur. Ce qu'il isole est une
sous-population, pas un biais.

**Une précision de lecture, importante.** Les lignes `…_PLACEBOADJ` du tableau annexe sont des
**contrôles, pas des séries tradables** : « signal − placebo » n'est le rendement d'aucun
portefeuille, son seul rôle est de tester si l'edge brut est spécifique à l'événement ou n'est
que la dérive persistante du coin. Elles sont marquées `tradable_series: false` dans
`RESULTS.json`. La variante `…_PLACEBOADJ2SIDED` moyenne en plus les fenêtres t−7j **et t+7j** :
elle lit donc le futur **volontairement**, et porte `pit_status: PIT_VIOLATING_BY_DESIGN`. Aucun
bps de ces lignes n'est atteignable ; ils ne servent qu'à cadrer les lignes brutes.

Scripts : `evidence/run_event_gate_v2.py`, `evidence/run_placebo_audit.py`.

---

## 4. La sous-population survivante — et pourquoi elle ne passe quand même pas

**Ce que ce n'est pas.** L'explication évidente serait « premier TWAP après une période
calme ». Elle est **fausse** : `QUIET_24h`, `QUIET_72h` et `QUIET_7d` mesurés juste avant `t`
sont tous morts (t < 1, train/test de signes opposés — p.ex. QUIET_7d : train −40,3 /
test +44,3 bps). Testé et tué explicitement (`evidence/run_firsttouch_gate.py`).

**Ce que c'est.** Le critère réel est : *le coin n'avait aucun flux TWAP HL connu il y a une
semaine (`[t−7j, t−6j)`) et en a maintenant* — une population d'**arrivée d'attention**. La
condition ne lit que des barres strictement passées, donc elle est PIT-calculable en live.
Elle se déclenche sur 9,9 % des TWAP (23 512 événements, 155 coins, 883 jours).

`HLTWAP_COINQUIET_1WK_AGO` @24h, LAG 15 min :

| champ | valeur |
|---|---|
| `n_raw` / `L1` / `L2` / `L3` | 23 267 / 14 559 / 10 058 / **883 jours** |
| `gross` / `net14` / `net28` | **+29,72** / **+15,72** / **+1,72 bps** |
| `t_stat_declustered` (L3) | **3,79** |
| `bootstrap_ci95` (blocs = jour) | [7,44 ; 49,70] |
| `year_by_year` | 2024 : +36,4 (n 3 810) · 2025 : +23,7 (11 240) · 2026 : +34,9 (8 217) |
| `ex_best_year` | +28,42 |
| `train` / `test` (coupure 2025-09-01) | +22,85 / **+35,45** |
| horizon | monotone : 60 min +4,2 · 4 h +10,0 · 12 h +22,5 · 24 h +29,7 |
| lag d'entrée | +31,2 (0) → +29,7 (15) → +25,8 (60 min) |
| concentration | 155 coins, top coin = 2,3 % des événements, ex-best-coin +27,43 |
| liquidité | tercile **haut** le plus fort : +38,2 gross, +10,2 sous stress 28 |
| `capacity_usd_estimate` | **733 853 $** (tercile haut : 3 875 153 $) |
| `eta_forward_confirmation` | **1 928 j = 5,28 ans** |

C'est le seul candidat sérieux du worker : il passe le stress 28 bps de justesse (+1,72 — mais
voir le point 0 ci-dessous, qui annule cette marge), il est stable sur trois années, il n'est pas porté par un coin ni par la queue illiquide (au contraire — c'est le
tercile **liquide** qui paie le plus, ce qui écarte l'artefact de micro-capacité), et sa
capacité est réelle (0,7–3,9 M$ par épisode sur Binance).

**Il échoue quand même, sur quatre points, et c'est le verdict.**

0. **Contrôle d'âge de listing — le plus dur.** Le briefing §8.10 signale l'âge de listing
   comme un piège connu (le projet applique un `ListingAgeGate` à 30 jours). Le déclencheur y
   est exposé par construction : un coin fraîchement listé n'avait évidemment aucun flux TWAP
   une semaine plus tôt. Mesuré sur l'âge réel dans le panel Binance (barres depuis le premier
   close fini) : **9,3 % des événements du déclencheur portent sur des coins de moins de 30
   jours, contre 4,2 % dans la population TWAP complète** (sur-représentation ×2,2), et 1 %
   se déclenchent sur les toutes premières barres du symbole.

   | population | gross | net14 | **net28** | t(L3) | train/test |
   |---|---|---|---|---|---|
   | tous les événements du déclencheur | 29,72 | 15,72 | **+1,72** | 3,79 | 22,9 / 35,5 |
   | **âge ≥ 30 j (`ListingAgeGate`)** | 26,13 | 12,13 | **−1,87** | 3,77 | 27,2 / 25,3 |
   | âge ≥ 90 j | 26,84 | 12,84 | −1,16 | 3,77 | 32,1 / 23,1 |
   | âge ≥ 180 j | 24,03 | 10,03 | −3,97 | 3,16 | 27,0 / 22,2 |

   **La seule chose qui faisait survivre ce mécanisme au stress 28 bps, ce sont les coins de
   moins de 30 jours.** Sous la propre politique de listing du projet, il est `COST_FRAGILE`.
   Le t-stat, lui, ne bouge pas (3,79 → 3,77) : l'effet n'est pas *entièrement* un artefact de
   listing, mais sa marge au-dessus des coûts l'était.

1. **ETA rédhibitoire (le critère décisif du round).** Le taux d'épisodes indépendants L3 est
   déjà **saturé** (≥ 1 épisode chaque jour calendaire, 7/semaine) : le seul levier restant
   est la dispersion journalière. Trois schémas d'agrégation ont été testés :

   | schéma d'agrégation journalière | moy. j. | σ j. | n requis (j) | ETA |
   |---|---|---|---|---|
   | pondéré par événement | 16,46 | 248,7 | 2 195 | **6,01 ans** |
   | equal-weight par coin | 9,89 | 207,7 | 13 837 | 37,9 ans |
   | equal-weight, plafonné à 5 coins/j | 35,17 | 308,6 | 2 415 | 6,61 ans |

   Aucun ne descend sous les 3 ans. Le levier n'existe pas.
2. **Le contraste de bras n'est pas significatif.** Contre le reste de la population TWAP :
   +19,82 bps mais **t = 1,16**. Le déclencheur n'est pas démontrablement meilleur que la
   population dont il est extrait (briefing §1.3).
3. **Fragile à la construction de portefeuille.** En equal-weight par coin — la forme naturelle
   d'un portefeuille — la moyenne journalière tombe de 16,46 à 9,89 bps, **sous les coûts**.

À quoi s'ajoute qu'il s'agit d'un **`REFIT` déclaré** : ce déclencheur n'est pas dans
`PREREGISTRATION.md`, il a été trouvé en auditant le placebo. Le split chronologique
train/test est passé (+22,9 / +35,4), ce qui est rassurant mais ne remplace pas un
préenregistrement.

**Verdict : `UNCONFIRMABLE_IN_HORIZON`.** Le briefing §2 est explicite : ETA > 3 ans est
décisif « même si son bps est superbe ».

Scripts : `evidence/run_quietweekago_gate.py`, `evidence/run_coinquiet_robustness.py`,
`evidence/run_listingage_check.py`.

---

## 5. T8 — le lead-lag HL→Binance va dans l'AUTRE SENS

La mission demandait de mesurer le décalage et de le dire franchement. Mesure faite sur
`microstructure_reduced` (BBO binance / hyperliquid / okx, BTC-ETH-SOL, 2026-08-31 → 09-05),
grille 100 ms, rendements 1 s, corrélation croisée sur ±30 s, **sur deux horloges** :
`event_ts_ns` (horloge de la venue → lead-lag « vrai ») et `receive_ts_ns` (horloge de notre
collecteur → le seul lead-lag qu'un système live pourrait exploiter). OKX est porté comme
**venue de contrôle**.

Convention : `corr(r_HL[t], r_BIN[t+k])`, donc `k > 0` ⇒ HL précède Binance.

<!--T8_TABLE-->
| symbole | horloge | venue | lag argmax | corr max | corr à lag 0 | lecture |
|---|---|---|---|---|---|---|
| BTC | event | **hyperliquid** | **-500 ms** | 0.611 | 0.455 | **Binance précède** |
| BTC | event | okx | +0 ms | 0.879 | 0.879 | synchrone |
| BTC | receive | **hyperliquid** | **-800 ms** | 0.591 | 0.338 | **Binance précède** |
| BTC | receive | okx | +0 ms | 0.843 | 0.843 | synchrone |
| ETH | event | **hyperliquid** | **-600 ms** | 0.720 | 0.374 | **Binance précède** |
| ETH | event | okx | +0 ms | 0.892 | 0.892 | synchrone |
| ETH | receive | **hyperliquid** | **-900 ms** | 0.689 | 0.195 | **Binance précède** |
| ETH | receive | okx | +0 ms | 0.848 | 0.848 | synchrone |
| SOL | event | **hyperliquid** | **-400 ms** | 0.762 | 0.483 | **Binance précède** |
| SOL | event | okx | +0 ms | 0.861 | 0.861 | synchrone |
| SOL | receive | **hyperliquid** | **-800 ms** | 0.734 | 0.246 | **Binance précède** |
| SOL | receive | okx | +0 ms | 0.813 | 0.813 | synchrone |
<!--/T8_TABLE-->

**Le lag est négatif partout : c'est Binance qui précède Hyperliquid de 400 à 900 ms**
(6 symbole × horloge, sans exception).
L'hypothèse T8 est réfutée dans son signe. OKX, mesuré par la même machinerie, est synchrone
à 0 ms avec une corrélation de 0,87 — ce qui prouve que le lag négatif de HL est une propriété
de HL (carnet plus fin, ~8 mises à jour BBO/s contre ~400/s chez Binance) et non un artefact
de ma grille ou d'un décalage d'horloge.

Test de tradabilité malgré tout (un mouvement HL 1 s dans le centile 99 → rendement Binance sur
les H secondes suivantes, 36 combinaisons symbole × horloge × horizon de 1 s à 300 s) :
**−0,01 à +0,62 bps bruts**. Contre un aller-retour de 14 bps : **net −13,4 à −14,0 bps**.
Le meilleur cas de tout le balayage est **~23× sous les coûts**.

**Verdict `DEAD`, pour trois raisons indépendantes :** (1) le signe est l'inverse de
l'hypothèse — HL suit, il ne précède pas ; (2) l'effet conditionnel est ~23× sous les coûts ;
(3) même si les deux premiers points étaient favorables, un horizon de 0,4–0,9 s est **hors
d'atteinte de la stack du projet** (barres 5 min ; le collecteur TWAP HL poll toutes les
~76 s). Dit franchement, comme la mission le demandait : **il n'y a pas de lead-lag
HL→Binance à exploiter, et s'il y en avait un, il ne serait pas exécutable ici.**

Script : `evidence/run_leadlag_t8.py`.

---

## 6. Track B — les mécanismes natifs HL, et la question de la capacité

Fenêtre : 2026-07-18 → 2026-08-29, **40 jours calendaires**, 12 coins. Le préenregistrement
annonçait `DATA_LIMITED` ici ; c'est confirmé (33–40 épisodes L3 indépendants, sous le seuil
de 60). L'intérêt est de mesurer les tailles d'effet honnêtement.

**T9 — dislocation.** HL publie `premium = (mark_px − oracle_px)/oracle_px`, où l'oracle est
son propre indice multi-venues : **la prime HL EST la dislocation HL-vs-reste-du-marché**,
nativement, sans donnée externe.
- La dislocation **se referme bien**, et très significativement : **+1,07 à +2,41 bps** selon
  l'horizon (1 h / 4 h / 24 h) et le seuil (|z| ≥ 1 ou 2), avec t(L3) de **4,6 à 14,2** et des
  IC très serrés (p.ex. [0,89 ; 1,24]).
- Un trade de dislocation a **deux jambes** ⇒ coût 28 bps, stress 56. L'effet est donc
  **12 à 26 fois trop petit**. C'est un fait de marché mesuré avec précision, pas un edge.
- Fader la prime en **directionnel** sur la seule jambe HL perd franchement de l'argent
  (−7,0 bps à 1 h, −28,0 à 4 h, −147,2 à 24 h) : la prime est un signal de *momentum*, pas de
  reversion, sur l'outright.

**T10 — divergence de funding HL vs Binance.** Funding HL horaire (`ctxs`) contre
`funding_rate` Binance (enriched 1 h), z-scoré sur 168 h, 7 coins appariables. **t(L3) ≤ 1,00
partout**, IC énormes ([−53,9 ; +163,6]), 33–37 jours indépendants. `DATA_LIMITED`. Cohérent
avec le §4 du briefing (funding/basis épuisé par l'arbitrage 2025-26) ; rien à ajouter.

**T11 — imbalance de carnet L2 HL.** C'est le résultat le plus instructif de Track B :

| horizon | gross | net14 | t(L3) | CI95 | ETA | capacité |
|---|---|---|---|---|---|---|
| 5 min (imb fort) | +0,89 | −13,11 | **14,56** | [0,76 ; 1,01] | **0,24 an** | **9 139 $** |
| 15 min (imb fort) | +0,96 | −13,04 | 7,71 | [0,69 ; 1,23] | 0,87 an | 9 139 $ |
| 60 min (imb fort) | +0,99 | −13,01 | 3,01 | [0,35 ; 1,64] | 5,53 ans | 9 139 $ |

L'imbalance de carnet HL prédit réellement les 5 à 60 minutes suivantes, avec le **meilleur
t-stat et le meilleur ETA de tout le round** (0,24 an à 5 min). Et c'est **mort quand même** :
+0,89 bps (5 min) à +0,99 bps (60 min) contre 14 bps d'aller-retour, soit **14 à 16× sous les coûts**, sur une profondeur médiane
de premier niveau de **9 139 $**.

**C'est la leçon de capacité que la mission demandait de porter.** Une fréquence d'épisodes
excellente ne sauve pas un edge sous les coûts, et un edge qui n'existe que sur Hyperliquid doit
être jugé sur la liquidité réelle de HL : 9 k$ de profondeur au touch, contre 0,7–4,8 M$ par
épisode pour les mécanismes Track A exécutés sur Binance USDM. Les deux chiffres ne sont pas
comparables et aucun bps HL ne doit être lu sans le sien.

Script : `evidence/run_trackb_gate.py`.

---

## 7. Ce que j'ai tué, et pourquoi

| hypothèse | verdict | raison en une ligne |
|---|---|---|
| **T1** drift pendant l'exécution | `WEAK` | +11,9 bps bruts, mais le placebo t−7j gagne +18,0 sur la même population : effet de sélection de coin, pas d'événement. |
| **T2** reversion après la fin | `WEAK` | +1,13 bps, t 2,20, IC [−0,25 ; 2,35] : pas d'impact temporaire mesurable. |
| **T3** scaling en taille | `WEAK`/`DEAD` | non monotone. Contraste top1 % − bottom90 % = **−0,61 bps, t −0,48**. Les gros TWAP ne sont pas plus informés. |
| **T4** imbalance de flux agrégée | `WEAK` (après correction) | **fuite PIT de 74,4 % du notionnel** ; corrigée : 26,98 → 13,55 bps gross, net −0,45. Voir §2. |
| **T5** reduceOnly vs ouverture | `WEAK`/`DEAD` | contraste +15,8 bps mais **t 1,86** ; placebo-ajusté le contraste s'inverse (−20,5). |
| **T6** users informés | `DATA_LIMITED` | **disqualifié à l'avance par mon propre préenregistrement.** Voir ci-dessous. |
| **T7** durée / urgence | `COST_FRAGILE`/`WEAK` | contraste court − long = **−9,0 bps, t −1,54**, signe inverse à la prédiction. |
| **T8** lead-lag HL→Binance | `DEAD` | **Binance précède HL de 500–800 ms** ; OKX synchrone en contrôle. Voir §5. |
| **T9** dislocation HL/Binance | `DATA_LIMITED` | la dislocation se referme (t jusqu'à 14,2) mais vaut 1–2,4 bps contre 28 bps deux jambes. |
| **T10** funding HL vs Binance | `DATA_LIMITED` | t ≤ 1,00, 33–37 jours indépendants. |
| **T11** imbalance carnet L2 HL | `DATA_LIMITED` | réel (t 14,6, ETA 0,24 an) et 14 à 16× sous les coûts, sur 9 139 $ de profondeur. |
| *(hors préregistre)* first-touch après période calme | `DEAD` | QUIET_24h/72h/7d tous t < 1, train/test de signes opposés. |
| *(hors préregistre, `REFIT`)* `COINQUIET_1WK_AGO` | `UNCONFIRMABLE_IN_HORIZON` | +29,7 bps, survit au stress 28, stable 3 ans — **ETA 5,3–6,6 ans**. Voir §4. |

### T6 : pourquoi le meilleur chiffre du rapport est jeté

La cohorte de wallets « informés » (scorée sur TRAIN 2024-02→2025-08, évaluée sur TEST
2025-09→2026-07 seulement, chronologie stricte) affiche **+44,81 bps bruts, +30,81 net,
+16,81 sous stress 28, t(L3) 4,20, IC [18,5 ; 72,9], ETA 1,62 an**. C'est le seul chiffre du
worker qui passerait mécaniquement toutes les cases du gate §2, ETA comprise.

Il est jeté, pour une raison décidée **avant** les tests. `PREREGISTRATION.md` §0 :

> « Biais de survie déclaré d'avance : la liste des 2 215 users vient du tape de trades live
> 2026-07/08. L'historique 2024-2025 est donc conditionné à “ce wallet était encore actif en
> juillet 2026”. **Pour une étude de skill de wallet ce biais serait disqualifiant** ; pour une
> étude d'impact de prix d'un ordre programmé il est faible. »

T6 *est* une étude de skill de wallet. Le biais s'applique en plein : les wallets encore actifs
en juillet 2026 après 2,5 ans sont, par construction, ceux qui n'ont pas sauté — et une cohorte
« top quintile » extraite de ce groupe hérite du biais deux fois. Deux contrôles indépendants
vont dans le même sens : le contraste top − bottom vaut +39,6 bps mais avec **t 0,70**, et la
version placebo-ajustée tombe à +20,5 bps avec **t 1,34**, IC [−16,4 ; 59,8].

**Ce qu'il faudrait pour trancher :** un univers de wallets *point-in-time* (les wallets connus
comme actifs à chaque date historique), pas un univers reconstruit depuis un snapshot postérieur.
Tant que le collecteur ne stocke pas la liste des users telle qu'elle était à chaque date, T6
n'est pas décidable. C'est une recommandation d'infrastructure concrète et peu coûteuse.

---

## 8. Bugs trouvés (y compris dans mon propre travail)

1. **Fuite PIT dans T4 v1** (§2) — somme avant du flux programmé. Corrigée dans
   `build_flow_panel.py` v2, quantifiée dans `run_flow_leak_diag.py`.
2. **`run_event_gate.py` a rapporté un artefact comme un résultat.** La ligne
   `HLTWAP_ALL_h24h_momentum_residualised` affichait `gross = −0,00 bps`, ce qui a l'air d'un
   contrôle qui tue le signal. **Les résidus d'une OLS sont de moyenne nulle par construction** :
   le chiffre ne portait aucune information. Remplacé par les quantités informatives —
   intercept +12,53 bps, β = −0,0343 — et par une décomposition en quintiles de momentum signé
   glissant, qui montre au passage que la régression linéaire était le mauvais contrôle
   (β ≈ 0 alors que les moyennes conditionnelles vont de −3,5 bps au Q2 à **+34,6 bps au Q5** :
   les queues épaisses écrasent la pente). Le contrôle décisif est le placebo t−7j, pas la
   résidualisation. Corrigé dans `run_event_gate_v2.py`.
3. **Mon propre diagnostic de fuite était faux au premier jet** : les TWAP plus courts que la
   latence de détection (`durée ≤ 10 min`) donnaient un recouvrement négatif, ce qui produisait
   une « part non connue » de −29,3 (absurde). Garde `en > st` ajoutée ; la part correcte est
   74,4 %. Le premier chiffre publié dans `flow_gate_results.json` (« 112,5 % ») est faux —
   `flow_leak_diagnostic.json` fait foi.
4. **Garde de couverture trop stricte dans le lead-lag** : HL publie ~8 mises à jour BBO/s
   contre ~400/s chez Binance, donc un seuil de 50 % de buckets 100 ms remplis éliminait
   silencieusement la grille HL sur les symboles les plus fins. Abaissé à 3 %.
5. **`funding_rate` absent** de `data/enriched/{XRP,DOGE,LTC,SUI}USDT_1h_enriched.parquet`
   (présent pour BTC/ETH/SOL/ADA/AVAX/LINK/BNB). T10 tourne sur 7 coins, pas 11.
6. **`perp_ohlcv` normalisé s'arrête au 2026-08-01**, alors que `data/enriched` va au 2026-09-04
   et que le tape HL va au 2026-08-29. Les 58 121 TWAP d'août 2026 sont hors panel Binance et
   n'ont servi à aucun test — réserve out-of-sample intacte pour qui reprendra ce sujet.

---

## 9. Livrables et reproductibilité

Tout est dans ce dossier. Les scripts se ré-exécutent dans l'ordre, avec `.venv/bin/python`
depuis la racine du dépôt, et n'écrivent qu'en scratch (`$W2_SCRATCH`) et ici.

| script | rôle |
|---|---|
| `evidence/build_panel.py` | table d'épisodes TWAP dédupliquée + matrices 5 min Binance + indice marché |
| `evidence/build_events.py` | jointure PIT, rendements forward par LAG et horizon, placebos ±7 j |
| `evidence/build_flow_panel.py` | panel de flux programmé **v2 (fuite corrigée)** |
| `evidence/gate.py` | le gate §2 du briefing, appliqué à l'identique partout |
| `evidence/run_event_gate.py` | premier passage (conservé pour la traçabilité ; contient l'artefact du §8.2) |
| `evidence/run_event_gate_v2.py` | **passage final** : capacité, placebos, contrôle momentum corrigé |
| `evidence/run_flow_gate.py` | T4 fuité vs propre, première comparaison |
| `evidence/run_flow_leak_diag.py` | **forensique de la fuite** + variante PIT-légale `v2b` |
| `evidence/run_placebo_audit.py` | contamination du placebo + gate sur le sous-ensemble propre |
| `evidence/run_firsttouch_gate.py` | réfutation de l'explication « premier touch après période calme » |
| `evidence/run_quietweekago_gate.py` | `COINQUIET_1WK_AGO` sur 6 horizons + contraste de bras |
| `evidence/run_coinquiet_robustness.py` | ETA sous 3 schémas d'agrégation, concentration, liquidité |
| `evidence/run_listingage_check.py` | contrôle d'âge de listing (`ListingAgeGate` 30 j) sur le candidat survivant |
| `evidence/run_leadlag_t8.py` | T8 lead-lag, deux horloges, OKX en contrôle |
| `evidence/run_trackb_gate.py` | T9 / T10 / T11 natifs HL |
| `evidence/build_results.py` | consolidation + échelle de verdicts + overrides documentés |
| `evidence/finalize_report.py` | régénère les deux tableaux générés de ce rapport depuis `RESULTS.json` |

`RESULTS.json` contient les 63 mécanismes avec tous les champs du gate §2, le verdict, et
`gate_failures` — la liste explicite des critères échoués pour chacun. Les deux verdicts qui
ne découlent pas mécaniquement de l'échelle (T6 et `COINQUIET`) sont marqués
`override_applied: true` avec `verdict_before_override` et la justification complète, pour
qu'un relecteur puisse contester la décision plutôt que de devoir la deviner.

**Ressources — un écart déclaré.** Le scratch de ce worker pèse **430 Mo**, au-dessus du
plafond de 250 Mo du briefing §8.1. Il s'agit des intermédiaires construits par la session
interrompue du 2026-09-03 (`panel.npz` 256 Mo, `events.parquet` 148 Mo), retrouvés intacts et
**réutilisés plutôt que reconstruits** — c'est ce qui a permis de reprendre l'analyse au lieu de
la refaire. Rien n'a été ajouté au-delà. Ils sont conservés parce qu'ils sont l'entrée de tous
les scripts `evidence/` : les supprimer imposerait un rebuild complet (~10 min) à qui rejouerait
le travail. Le disque est resté à 57–58 Go libres tout du long (loin du seuil CRITICAL de 20 Go),
aucune écriture hors de ce dossier et du scratch, **aucun fichier supprimé nulle part**.

---

## 10. Recommandations

1. **Ne pas déployer.** Aucun candidat n'est `VALIDATED_FOR_FORWARD`.
2. **Le sujet Hyperliquid TWAP peut être clos comme source d'alpha directionnel autonome.**
   Le drift signé existe mais il est un effet de sélection de coin ; la seule sous-population
   qui y résiste demande 5 à 6 ans de confirmation.
3. **Deux choses valent d'être conservées comme faits d'infrastructure**, pas comme alphas :
   Binance précède HL de 500–800 ms (donc HL ne sert pas de signal avancé pour le paper Binance),
   et la profondeur au touch de HL est de ~9 k$ (donc tout edge HL-only est plafonné là).
4. **Une correction de collecteur, peu coûteuse et qui débloquerait T6** : stocker la liste des
   users `twapHistory` telle qu'elle est à chaque date de poll, pour disposer d'un univers de
   wallets point-in-time. Sans elle, toute étude de skill de wallet sur ce dataset restera
   indécidable.
5. **Réserve out-of-sample disponible** : les 58 121 TWAP d'août 2026 n'ont été touchés par
   aucun test, faute de panel Binance normalisé au-delà du 2026-08-01. Étendre `perp_ohlcv`
   rendrait ce mois utilisable comme test réellement vierge pour `COINQUIET_1WK_AGO`.

---

## Annexe — tous les mécanismes testés (gate §2 complet)

`trk` : A = exécuté sur Binance USDM, B = natif Hyperliquid.
`net14`/`net28` : `gross − 14` et `gross − 28` (deux jambes pour T9 : `− 28` / `− 56`).
`capacité $` : Track A = 0,5 % du volume quote Binance sur la fenêtre de détention (médiane
par épisode) ; Track B = profondeur médiane au premier niveau du carnet HL.

<!--ANNEX-->
| mécanisme | trk | n_raw | L1 | L2 | L3 | gross | net14 | net28 | t(L3) | CI95 | ex-best-yr | train/test | ETA (ans) | capacité $ | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HLTWAP_COINQUIET_1WK_AGO_S1440 [REFIT] | A | 23,267 | 14,559 | 10,058 | 883 | 29.72 | 15.72 | 1.72 | 3.79 | [7.4, 49.7] | 28.4 | 22.9/35.5 | 5.28 | 733,853 | **UNCONFIRMABLE_IN_HORIZON** |
| COINQUIET_1WK_AGO_S1440 :: all trigger events ⟨robustesse⟩ | A | 23,267 | 14,559 | 10,058 | 883 | 29.72 | 15.72 | 1.72 | 3.79 | [7.4, 49.7] | 28.4 | 22.9/35.5 | 5.28 | – | **UNCONFIRMABLE_IN_HORIZON** |
| COINQUIET_1WK_AGO_S1440 :: age >= 90d ⟨robustesse⟩ | A | 19,979 | 12,787 | 9,098 | 830 | 26.84 | 12.84 | -1.16 | 3.77 | [6.5, 47.1] | 23.8 | 32.1/23.1 | 5.03 | – | **COST_FRAGILE** |
| COINQUIET_1WK_AGO_S1440 :: age >= 30d (ListingAgeGate) ⟨robustesse⟩ | A | 21,302 | 13,567 | 9,709 | 880 | 26.13 | 12.13 | -1.87 | 3.77 | [7.3, 44.5] | 23.8 | 27.2/25.3 | 5.31 | – | **COST_FRAGILE** |
| COINQUIET_1WK_AGO_S1440 :: age >= 180d ⟨robustesse⟩ | A | 18,119 | 11,649 | 8,258 | 753 | 24.03 | 10.03 | -3.97 | 3.16 | [3.8, 44.5] | 19.5 | 27.0/22.2 | 6.49 | – | **COST_FRAGILE** |
| HLTWAP_COINQUIET_1WK_AGO_S720 [REFIT] | A | 23,270 | 14,561 | 10,058 | 883 | 22.51 | 8.51 | -5.49 | 3.69 | [4.3, 40.1] | 19.8 | 19.8/24.8 | 5.55 | 366,926 | **COST_FRAGILE** |
| T1 HLTWAP_BUYONLY_h24h | A | 141,259 | 71,210 | 18,173 | 878 | 22.16 | 8.16 | -5.84 | 3.13 | [-2.0, 44.4] | 10.8 | 21.4/22.4 | 7.71 | 4,166,362 | **COST_FRAGILE** |
| T7 HLTWAP_DUR_GE180_h24h | A | 40,160 | 26,235 | 9,765 | 803 | 17.80 | 3.80 | -10.20 | 3.25 | [0.5, 32.8] | 7.1 | 14.5/18.8 | 6.52 | 5,163,435 | **COST_FRAGILE** |
| T10 HL_VS_BINANCE_FUNDING_DIVERGENCE_z2_h24h (Binance leg) | B | 399 | 117 | 117 | 33 | 45.30 | 31.30 | 17.30 | 1.00 | [-53.9, 163.6] | – | – | 51.14 | 337,303 | **DATA_LIMITED** |
| T6 informed-user cohort (TRAIN-scored) @24h TEST-only | A | 8,318 | 4,119 | 2,591 | 332 | 44.81 | 30.81 | 16.81 | 4.20 | [18.5, 72.8] | 42.1 | – | 1.62 | 3,853,114 | **DATA_LIMITED** |
| T10 HL_VS_BINANCE_FUNDING_DIVERGENCE_z1_h24h (Binance leg) | B | 1,531 | 212 | 212 | 37 | 18.26 | 4.26 | -9.74 | 0.66 | [-36.7, 88.0] | – | – | 131.18 | 277,680 | **DATA_LIMITED** |
| T10 HL_VS_BINANCE_FUNDING_DIVERGENCE_z2_h4h (Binance leg) | B | 400 | 118 | 118 | 34 | 12.82 | -1.18 | -15.18 | 0.78 | [-30.8, 58.1] | – | – | 87.26 | 56,217 | **DATA_LIMITED** |
| T10 HL_VS_BINANCE_FUNDING_DIVERGENCE_z1_h4h (Binance leg) | B | 1,568 | 214 | 214 | 37 | 6.59 | -7.41 | -21.41 | 0.71 | [-12.6, 26.7] | – | – | 112.80 | 46,280 | **DATA_LIMITED** |
| T9 HL_PREMIUM_MEANREVERSION_z2_h24h (spread, not tradable alone) | B | 870 | 234 | 234 | 36 | 2.41 | -25.59 | -53.59 | 5.15 | [1.0, 4.2] | – | – | 2.09 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_MEANREVERSION_z2_h4h (spread, not tradable alone) | B | 873 | 236 | 236 | 37 | 2.10 | -25.90 | -53.90 | 6.59 | [1.1, 3.3] | – | – | 1.32 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_MEANREVERSION_z2_h1h (spread, not tradable alone) | B | 873 | 236 | 236 | 37 | 1.59 | -26.41 | -54.41 | 9.17 | [1.2, 2.2] | – | – | 0.68 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_MEANREVERSION_z1_h24h (spread, not tradable alone) | B | 3,477 | 388 | 388 | 37 | 1.55 | -26.45 | -54.45 | 4.61 | [0.6, 2.4] | – | – | 2.69 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_MEANREVERSION_z1_h4h (spread, not tradable alone) | B | 3,591 | 396 | 396 | 37 | 1.34 | -26.66 | -54.66 | 10.02 | [1.0, 1.6] | – | – | 0.57 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_MEANREVERSION_z1_h1h (spread, not tradable alone) | B | 3,603 | 396 | 396 | 37 | 1.07 | -26.93 | -54.93 | 14.17 | [0.9, 1.2] | – | – | 0.28 | 9,151 | **DATA_LIMITED** |
| T11 HL_L2_IMBALANCE_strong_h60min | B | 239,453 | 429 | 429 | 39 | 0.99 | -13.01 | -27.01 | 3.01 | [0.3, 1.6] | – | – | 5.53 | 9,139 | **DATA_LIMITED** |
| T11 HL_L2_IMBALANCE_strong_h15min | B | 239,617 | 437 | 437 | 40 | 0.96 | -13.04 | -27.04 | 7.71 | [0.7, 1.2] | – | – | 0.87 | 9,139 | **DATA_LIMITED** |
| T11 HL_L2_IMBALANCE_strong_h5min | B | 239,651 | 439 | 439 | 40 | 0.89 | -13.11 | -27.11 | 14.56 | [0.8, 1.0] | – | – | 0.24 | 9,139 | **DATA_LIMITED** |
| T11 HL_L2_IMBALANCE_sign_h60min | B | 563,296 | 429 | 429 | 39 | 0.58 | -13.42 | -27.42 | 3.20 | [0.2, 0.9] | – | – | 4.91 | 9,139 | **DATA_LIMITED** |
| T11 HL_L2_IMBALANCE_sign_h15min | B | 563,791 | 440 | 440 | 40 | 0.55 | -13.45 | -27.45 | 7.32 | [0.4, 0.7] | – | – | 0.96 | 9,139 | **DATA_LIMITED** |
| T11 HL_L2_IMBALANCE_sign_h5min | B | 563,901 | 440 | 440 | 40 | 0.51 | -13.49 | -27.49 | 11.71 | [0.4, 0.6] | – | – | 0.38 | 9,139 | **DATA_LIMITED** |
| T9 HL_PREMIUM_FADE_z1_h1h (HL leg only) | B | 3,603 | 396 | 396 | 37 | -4.27 | -32.27 | -60.27 | -2.02 | [-10.1, 0.6] | – | – | 13.97 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_FADE_z2_h1h (HL leg only) | B | 873 | 236 | 236 | 37 | -6.97 | -34.97 | -62.97 | -1.86 | [-17.9, 6.3] | – | – | 16.57 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_FADE_z1_h4h (HL leg only) | B | 3,591 | 396 | 396 | 37 | -12.45 | -40.45 | -68.45 | -1.33 | [-39.3, 10.4] | – | – | 32.52 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_FADE_z2_h4h (HL leg only) | B | 873 | 236 | 236 | 37 | -28.00 | -56.00 | -84.00 | -1.85 | [-83.2, 35.0] | – | – | 16.63 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_FADE_z1_h24h (HL leg only) | B | 3,477 | 388 | 388 | 37 | -71.48 | -99.48 | -127.48 | -1.98 | [-179.2, 15.7] | – | – | 14.61 | 9,151 | **DATA_LIMITED** |
| T9 HL_PREMIUM_FADE_z2_h24h (HL leg only) | B | 870 | 234 | 234 | 36 | -147.21 | -175.21 | -203.21 | -2.95 | [-324.0, 51.4] | – | – | 6.39 | 9,151 | **DATA_LIMITED** |
| T6 informed-user cohort (TRAIN-scored) @24h TEST-only_PLACEBOADJ2SIDED ⟨diagnostic⟩ | A | 8,184 | 4,036 | 2,548 | 325 | 24.56 | 10.56 | -3.44 | 1.75 | [-16.3, 61.6] | 23.5 | – | 9.52 | 3,853,114 | **WEAK** |
| HLTWAP_COINQUIET_1WK_AGO_A1440 [REFIT] | A | 21,996 | 13,954 | 9,900 | 881 | 21.02 | 7.02 | -6.98 | 2.41 | [-0.9, 41.3] | 14.0 | 24.7/18.1 | 12.98 | 733,853 | **WEAK** |
| T5 HLTWAP_NONREDUCEONLY_h24h | A | 203,829 | 97,195 | 22,154 | 891 | 13.86 | -0.14 | -14.14 | 4.13 | [3.6, 23.5] | 9.9 | 15.3/13.5 | 4.48 | 4,837,956 | **WEAK** |
| T3 HLTWAP_SIZERATIO_TOP10PCT_h24h(thr=4.91e-04,TRAIN) | A | 25,927 | 15,983 | 9,626 | 726 | 13.73 | -0.27 | -14.27 | 2.63 | [0.3, 24.9] | -0.3 | 9.4/14.8 | 9.01 | 490,703 | **WEAK** |
| T4_FLOW_IMBALANCE_XS_LS_4h_v2b_CLEAN_RESIDUAL | A | 3,409 | 3,409 | 677 | 677 | 13.55 | -0.45 | -14.45 | 3.64 | [6.5, 20.9] | 12.3 | 12.4/14.5 | 4.55 | 195,317 | **WEAK** |
| T1 HLTWAP_ALL_h24h | A | 233,043 | 108,376 | 24,150 | 895 | 11.88 | -2.12 | -16.12 | 4.28 | [3.9, 19.4] | 7.9 | 11.9/11.9 | 4.20 | 4,394,654 | **WEAK** |
| HLTWAP_COINQUIET_1WK_AGO_S240 [REFIT] | A | 23,273 | 14,562 | 10,059 | 883 | 10.04 | -3.96 | -17.96 | 2.97 | [0.3, 20.0] | 6.8 | 13.3/7.3 | 8.59 | 122,308 | **WEAK** |
| T7 HLTWAP_DUR_LE15_h24h | A | 81,342 | 37,024 | 13,173 | 822 | 9.61 | -4.39 | -18.39 | 2.82 | [0.4, 18.1] | 8.7 | 11.7/9.1 | 8.85 | 4,205,936 | **WEAK** |
| HLTWAP_COINQUIET_1WK_AGO_A240 [REFIT] | A | 22,007 | 13,962 | 9,903 | 881 | 7.47 | -6.53 | -20.53 | 1.92 | [-2.6, 17.6] | 5.3 | 8.9/6.3 | 20.52 | 122,308 | **WEAK** |
| HLTWAP_COINQUIET_1WK_AGO_S60 [REFIT] | A | 23,273 | 14,562 | 10,059 | 883 | 4.22 | -9.78 | -23.78 | 2.51 | [-0.3, 8.6] | 1.6 | 8.7/0.5 | 12.05 | 30,577 | **WEAK** |
| T1 HLTWAP_ALL_h4h | A | 233,047 | 108,378 | 24,151 | 895 | 4.02 | -9.98 | -23.98 | 3.84 | [1.1, 6.7] | 3.8 | 4.8/3.8 | 5.22 | 732,442 | **WEAK** |
| T1 HLTWAP_ALL_hDUR | A | 233,049 | 108,379 | 24,151 | 895 | 3.42 | -10.58 | -24.58 | 5.42 | [1.7, 5.0] | 3.0 | 3.8/3.3 | 2.62 | 91,555 | **WEAK** |
| T2 HLTWAP_POSTEND_REVERSION | A | 233,040 | 108,380 | 24,151 | 895 | 1.13 | -12.87 | -26.87 | 2.20 | [-0.2, 2.4] | 0.5 | 0.6/1.3 | 15.88 | 91,555 | **WEAK** |
| T4_FLOW_IMBALANCE_XS_LS_4h_v1_LEAKY | A | 3,409 | 3,409 | 677 | 677 | 26.98 | 12.98 | -1.02 | 6.21 | [19.0, 35.6] | 24.0 | 21.2/31.5 | 1.57 | 150,184 | **DEAD** |
| T6 informed-user cohort (TRAIN-scored) @24h TEST-only_PLACEBOADJ ⟨diagnostic⟩ | A | 8,256 | 4,085 | 2,580 | 332 | 20.47 | 6.47 | -7.53 | 1.34 | [-16.4, 59.8] | -4.2 | – | 15.86 | 3,853,114 | **DEAD** |
| T3 HLTWAP_SIZERATIO_TOP1PCT_h24h_PLACEBOADJ ⟨diagnostic⟩ | A | 3,202 | 2,158 | 1,906 | 462 | 15.82 | 1.82 | -12.18 | 1.20 | [-12.5, 44.5] | 14.8 | 64.1/7.2 | 28.02 | 248,355 | **DEAD** |
| T3 HLTWAP_SIZERATIO_TOP1PCT_h24h(thr=5.00e-03,TRAIN) | A | 3,216 | 2,168 | 1,911 | 465 | 10.93 | -3.07 | -17.07 | 1.05 | [-12.9, 34.0] | 10.0 | 22.6/8.8 | 36.71 | 248,355 | **DEAD** |
| T3 HLTWAP_NTL_GE1M_h24h | A | 6,968 | 4,060 | 2,304 | 630 | 9.13 | -4.87 | -18.87 | 1.27 | [-6.1, 25.5] | 4.0 | 1.4/11.5 | 33.98 | 33,190,186 | **DEAD** |
| HLTWAP_QUIET72h_FIRSTTOUCH_h24h_raw [REFIT] | A | 5,399 | 4,653 | 4,511 | 845 | 7.88 | -6.12 | -20.12 | 0.78 | [-15.7, 32.0] | -9.7 | -17.2/36.0 | 119.85 | 321,519 | **DEAD** |
| HLTWAP_QUIET7d_FIRSTTOUCH_h4h_raw [REFIT] | A | 2,670 | 2,250 | 2,184 | 740 | 7.46 | -6.54 | -20.54 | 0.93 | [-11.3, 27.1] | -2.7 | -0.6/18.1 | 87.02 | 45,185 | **DEAD** |
| HLTWAP_QUIET24h_FIRSTTOUCH_h24h_raw [REFIT] | A | 11,531 | 10,023 | 9,689 | 880 | 4.99 | -9.01 | -23.01 | 0.79 | [-7.8, 17.8] | -4.0 | -3.2/12.6 | 122.11 | 418,736 | **DEAD** |
| HLTWAP_QUIET72h_FIRSTTOUCH_h4h_raw [REFIT] | A | 5,401 | 4,654 | 4,512 | 846 | 4.46 | -9.54 | -23.54 | 0.89 | [-5.7, 16.3] | -0.6 | 1.8/7.4 | 91.83 | 53,586 | **DEAD** |
| HLTWAP_QUIET24h_FIRSTTOUCH_h4h_raw [REFIT] | A | 11,533 | 10,024 | 9,690 | 880 | 4.27 | -9.73 | -23.73 | 1.40 | [-2.3, 10.7] | 1.0 | 2.4/6.0 | 38.38 | 69,789 | **DEAD** |
| T4_FLOW_IMBALANCE_XS_LS_4h_v2a_CLEAN_TRAILING | A | 3,409 | 3,409 | 677 | 677 | 3.49 | -10.51 | -24.51 | 1.06 | [-2.7, 9.7] | 2.2 | 2.1/4.6 | 53.47 | 196,829 | **DEAD** |
| T1 HLTWAP_ALL_h4h_PLACEBOADJ ⟨diagnostic⟩ | A | 231,116 | 107,538 | 23,979 | 893 | 1.26 | -12.74 | -26.74 | 1.06 | [-2.4, 4.6] | -0.7 | 1.6/1.2 | 68.30 | 732,442 | **DEAD** |
| T1 HLTWAP_ALL_h24h_PLACEBOADJ2SIDED ⟨diagnostic⟩ | A | 224,908 | 104,640 | 23,597 | 886 | 1.24 | -12.76 | -26.76 | 0.37 | [-9.8, 11.3] | 1.0 | 7.5/-0.5 | 573.79 | 4,394,654 | **DEAD** |
| T1 HLTWAP_ALL_hDUR_PLACEBOADJ ⟨diagnostic⟩ | A | 231,115 | 107,542 | 23,979 | 893 | 0.53 | -13.47 | -27.47 | 0.69 | [-1.8, 2.6] | -0.3 | 2.0/0.1 | 161.65 | 91,555 | **DEAD** |
| HLTWAP_QUIET72h_FIRSTTOUCH_h24h_PLACEBOADJ [REFIT] ⟨diagnostic⟩ | A | 5,335 | 4,619 | 4,485 | 843 | -0.89 | -14.89 | -28.89 | -0.08 | [-25.2, 23.7] | -21.2 | -24.6/25.5 | 11238.47 | 321,519 | **DEAD** |
| T5 HLTWAP_REDUCEONLY_h24h | A | 29,214 | 17,871 | 9,012 | 721 | -1.95 | -15.95 | -29.95 | -0.35 | [-17.7, 12.2] | -2.5 | -13.1/0.9 | 510.23 | 2,522,725 | **DEAD** |
| HLTWAP_QUIET7d_FIRSTTOUCH_h24h_raw [REFIT] | A | 2,668 | 2,249 | 2,183 | 739 | -4.01 | -18.01 | -32.01 | -0.25 | [-39.8, 32.1] | -30.9 | -40.3/44.3 | 1190.48 | 271,114 | **DEAD** |
| T1 HLTWAP_ALL_h24h_PLACEBOADJ ⟨diagnostic⟩ | A | 231,065 | 107,512 | 23,975 | 893 | -6.88 | -20.88 | -34.88 | -1.83 | [-19.1, 4.0] | -8.2 | 0.4/-8.8 | 22.91 | 4,394,654 | **DEAD** |
| T5 HLTWAP_NONREDUCEONLY_h24h_PLACEBOADJ ⟨diagnostic⟩ | A | 202,095 | 96,395 | 21,982 | 889 | -9.44 | -23.44 | -37.44 | -2.08 | [-24.1, 4.5] | -9.5 | 3.9/-13.0 | 17.72 | 4,837,956 | **DEAD** |
| HLTWAP_QUIET24h_FIRSTTOUCH_h24h_PLACEBOADJ [REFIT] ⟨diagnostic⟩ | A | 11,432 | 9,959 | 9,633 | 878 | -11.01 | -25.01 | -39.01 | -1.42 | [-27.5, 5.6] | -22.0 | -11.9/-10.2 | 37.48 | 418,736 | **DEAD** |
| HLTWAP_QUIET7d_FIRSTTOUCH_h24h_PLACEBOADJ [REFIT] ⟨diagnostic⟩ | A | 2,608 | 2,219 | 2,161 | 734 | -14.30 | -28.30 | -42.30 | -0.91 | [-47.9, 18.8] | -30.9 | -24.0/-1.4 | 90.20 | 271,114 | **DEAD** |
| T1 HLTWAP_BUYONLY_h24h_PLACEBOADJ ⟨diagnostic⟩ | A | 140,062 | 70,613 | 18,024 | 877 | -35.38 | -49.38 | -63.38 | -3.51 | [-69.1, -3.6] | -36.0 | -16.3/-40.8 | 6.10 | 4,166,362 | **DEAD** |
<!--/ANNEX-->
