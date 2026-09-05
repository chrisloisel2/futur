# W10_VALIDATION_PUSH — RAPPORT

Round 4 (alpha_hunt_2026-09-03), axe : **transformer en verdicts définitifs les
candidats en suspens du `validation_registry`**. Aucun nouveau mécanisme cherché.

- Préenregistrement : `PREREGISTRATION.md` (écrit avant tout test, fait foi).
- Scripts ré-exécutables : `evidence/*.py` ; résultats bruts : `evidence/*.json`.
- Session interrompue le 2026-09-03 après `t1a`→`t2b(script)` ; **reprise le
  2026-09-05** : `t2b` exécuté, `t1e` et `t4` ajoutés, rapport rédigé.
- **Rien n'a été écrit hors de ce dossier.** `configs/validation_registry.yaml`
  n'est PAS modifié : les verdicts ci-dessous sont une proposition d'intégration.

## Synthèse des verdicts

| cible | statut entrant | **verdict rendu** | net14 / net28 (bps) | N_indep L3 | ETA |
|---|---|---|---|---|---|
| `LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION` | BLOCKED | **REJECTED** | +40,0 brut → **−8,7 déclusterisé** / −22,7 | 437 | 213 ans (sans objet) |
| `LIQ_REPEAT_VOL_GATE` | NEEDS_MORE_RESEARCH | **UNCONFIRMABLE_IN_HORIZON** (définitif) | delta +113,7 (unité optimiste) | 590 ép. de régime | **5,8 ans** (optimiste) → 54 ans (unité jour) |
| `CROSS_SECTIONAL_MOMENTUM_CVD` | REJECTED | **MECHANISM_DEAD** (d) | +4,5 (t=0,22) | 333 | 3 272 ans |
| `BTC_ETH_CURVE_STEEPNESS` | REJECTED | **MECHANISM_DEAD** (d)+(c) | +17,1 (t=0,28) | 57 | 860 ans |
| `POSITIONING_TAKER_FLOW` | REJECTED | **MECHANISM_DEAD** (d) | −18,1 (t=−0,97 décl.) | 49 clusters | 31,8 ans *(chiffres de la découverte)* |
| `GLOBAL_ACCOUNT_LSR_FADE` | REJECTED | **MECHANISM_DEAD** (d)+(b) | −47,6 (t=−1,54 décl.) | 63 clusters | 35,3 ans *(chiffres de la découverte)* |
| `OI_CVD_MEMORY_OVERLAP` | REJECTED | **MECHANISM_DEAD** (b)+(a) | −16,5 exhaustion | 3 530 | sans objet (edge négatif) |
| `MICROSTRUCTURE_ALL_ROUND3` | DATA_ACCUMULATION | **DATA_LIMITED — non jugeable, et pas sur la trajectoire de le devenir** | — | 3 jours complets | cible 2026-11-20, **bloquée disque au 2026-09-11** |


**Résultat net du worker : 0 alpha récupéré, 8 dossiers définitivement clos.**
C'est un résultat négatif, et il est le bon : dans les huit cas la preuve
manquante n'était pas une expression plus heureuse mais un fait mesurable —
un artefact de clustering (cible 1), une fréquence d'épisodes trop basse
(cible 2 et les 5 rejets), ou un budget disque (cible 4). Deux livrables
transverses en sortent, décrits en §3.3 et §4.3 : un **écran ETA applicable dès
la découverte** pour un coût nul, et une **échéance datée** pour la
microstructure assortie du blocage qui l'empêche.

---

## 1. CIBLE 1 — `LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION` : **REJECTED**

### 1.1 La convention de signe est tranchée (le statut BLOCKED disparaît)

Deux questions étaient confondues sous « convention de signe ». Les deux sont
désormais fermées, **sur les données brutes, sans recours à la documentation**.

**Q1 — sémantique.** `evidence/t1a_sign_semantics.py`. Sur barres 5 min,
`imb = (short_liq − long_liq)/(short_liq + long_liq)` contre le rendement
contemporain du prix implicite du détecteur figé, sous la convention testée
« ordre de liquidation `SELL` = fermeture forcée d'un **LONG** ».

| source | indépendance | n barres | corr(imb, ret) | t | ret moyen si liq. 100 % SHORT | si 100 % LONG |
|---|---|---|---|---|---|---|
| OKX `posSide` (BTC/ETH/SOL) | champ *position side* explicite de la venue — vérité terrain, lu **sans passer par la normalisation du projet** | 3 121–4 654 | +0,437 à +0,536 | +29,9 à +35,4 | +8,3 / +10,5 / +18,3 bps | −7,1 / −9,6 / −14,5 bps |
| Bybit `side` normalisé | normalisation appliquée par `derivatives_collector/collector.py:223` (`Buy`→`SELL`) | 3 589–4 061 | +0,449 à +0,484 | +32,0 à +33,2 | +8,2 / +12,4 / +15,1 bps | −7,1 / −10,9 / −12,8 bps |
| Binance Vision COIN-M `side` | archive brute Binance, **jamais retouchée par ce projet**, période disjointe 2023-06 → 2024-10 | 11 491 / 18 549 | +0,432 / +0,450 | **+65,3 / +54,0** | +12,8 / +16,8 bps | −12,2 / −16,5 bps |

Contrôle négatif inclus : la convention **inverse** sur Bybit (`side_raw='Buy'`
lu comme un côté d'ordre) donne exactement l'opposé (corr −0,449, t −32,0) —
le test discrimine bien.

> **Q1 TRANCHÉE.** Un `forceOrder` de côté `SELL` est la liquidation d'un **LONG**
> (vente forcée → prix ↓) ; un `BUY` est la liquidation d'un **SHORT** (achat forcé
> → prix ↑). Trois sources indépendantes, dont une archive brute externe sur une
> période disjointe, |t| = 30 à 65, même signe partout. **Non réouvrable.**

**Q1bis — le label du détecteur correspond-il au flux réel ?**
`evidence/t1b_kind_vs_liqflow.py` : détecteur figé rejoué en **import lecture
seule**, 1 107 events sur 2026-07-05 → 2026-09-01 (fenêtre où `forceOrder` et
events coexistent), part de liquidations de shorts dans ±30 min :

| `kind` | n | part USD liq. SHORT | part médiane par event | % events à majorité short |
|---|---|---|---|---|
| `SHORT_SQUEEZE` | 264 | **91,2 %** | 97,0 % | 78,4 % |
| `LONG_CASCADE` | 843 | **8,1 %** | 0,0 % | 11,4 % |

Welch t (SQUEEZE − CASCADE) = **26,16**. Le label du détecteur figé dit bien ce
qu'il prétend : `SHORT_SQUEEZE` = des **shorts** liquidés = **achats forcés**.

*Note* : la docstring du détecteur figé (`detector.py:9`) affirmait déjà
« prix ↑ + OI ↓ violent → SHORT_SQUEEZE (shorts liquidés) ». **Elle était
exacte.** Le test ci-dessus ne la corrige pas, il la *confirme sur les données* —
ce qui était la seule chose manquante pour lever le blocage. Aucune source de
données ne contredisait quoi que ce soit ; l'ambiguïté était documentaire, pas
factuelle.

**Q2 — direction de trade.** Préenregistrée avant tout PnL : le mécanisme de
`LIQ_CASCADE_REPEAT_V1` est « après épuisement du flux forcé, se positionner
**contre** ce flux ». Le symétrique sur `SHORT_SQUEEZE` (achats forcés) est donc
**SHORT** (`SSE_MEANREV`). Corollaire préenregistré : le chiffre publié du round 2
a été mesuré sur `fwd_4h` **brut**, donc **LONG**, donc **avec** le flux — ce
n'est pas le symétrique de l'alpha existant mais un mécanisme différent
(`SSE_CONT`, « continuation »). Les deux ont été testés.

### 1.2 Gate §2 complet — les deux directions, les deux seuils

`evidence/t1c_short_squeeze_gate.py` (+ `_thr2.json`, `_thr3.json`).
Note de spécification, documentée et non refit : le code figé
`repeat_variant.py:27` (`EXHAUSTION_MIN_PRIOR = 2`) utilise `n_events_sym_24h >= 2`, alors que le chiffre
publié du round 2 (+40,0 plein / +114,6 OOS) correspond à `>= 3` — reproduction
exacte des N (`SHORT_SQUEEZE` 1 140, `LONG_CASCADE` 1 988). **Les deux sont testés.**

| test | dir. | thr | n_raw | L1 | L2 | **L3** | net14 brut | **net14 L3** | net28 | t L3 | IC95 boot L3 | ex-best-year | ETA |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `SSE_CONT` (round 2) | LONG | ≥3 | 1 140 | 745 | 353 | **437** | **+40,0** | **−8,7** | +26,0 | **−0,71** | [−31,5 ; +16,4] | −10,6 (t −0,85) | 213 ans |
| `SSE_CONT` (code figé) | LONG | ≥2 | 2 719 | 1 823 | 739 | **1 004** | +15,5 | **−30,5** | +1,5 | **−4,52** | [−43,6 ; −17,9] | −33,0 (t −4,38) | 4,7 ans (sur edge négatif) |
| `SSE_MEANREV` (symétrique) | SHORT | ≥3 | 1 140 | 745 | 353 | 437 | −68,0 | −19,4 | −82,0 | −1,58 | [−44,4 ; +3,5] | −30,7 | 42,7 ans |
| `SSE_MEANREV` | SHORT | ≥2 | 2 719 | 1 823 | 739 | 1 004 | −43,5 | +2,5 | −57,5 | +0,37 | [−10,1 ; +15,6] | −3,2 | 712 ans |
| **contrôle** `LC` exhaustion (alpha shadow) | LONG | ≥3 | 1 988 | 1 169 | 555 | 789 | +27,1 | **+25,4** | +13,1 | **+2,75** | [+7,9 ; +43,4] | +22,0 (t +2,37) | 11,0 ans |
| **contrôle** `LC` exhaustion | LONG | ≥2 | 5 457 | 3 157 | 1 034 | 1 645 | +16,2 | **+22,5** | +2,2 | **+4,11** | [+12,0 ; +33,5] | +20,7 (t +3,77) | 5,4 ans |

Contrastes (règle §1.3 du briefing — bras contre bras, jamais contre zéro) :

| contraste | thr=3 | thr=2 |
|---|---|---|
| `SS` exhaustion − onset (LONG) | +26,0 bps, t=**2,01**, IC [+1,5 ; +52,2] | +4,2 bps, t=**0,53**, IC [−10,6 ; +19,9] |
| `LC` exhaustion − onset (LONG), contrôle | +10,8, t=1,10 | +7,9, t=1,25 |
| `SS` exh − `LC` exh (cross-kind) | **−34,1**, t=−2,23 | **−53,0**, t=−6,10 |

Le seul contraste favorable (`SS` exh − onset à thr=3, t=2,01) **disparaît au seuil
du code figé** (t=0,53) : il n'est pas robuste au choix de seuil. Et il est de toute
façon un effet *relatif* — le bras exhaustion lui-même vaut +5,4 bps bruts
déclusterisés, **sous le coût de 14 bps**. Il n'est pas tradable.

### 1.3 Le déclustering n'est pas un artefact de méthode (contrôle obligatoire)

`evidence/t1d_decluster_diagnostics.py`. Si L3 fabriquait quelques méga-épisodes,
la moyenne équipondérée serait dominée par des épisodes minuscules et le verdict
serait un artefact. Ce n'est pas le cas :

- unités L3 de `SS` exhaustion : **62,4 % de taille 1**, taille médiane 1, durée
  médiane 0 h, p95 6,7 h, max 29,7 h — des épisodes, pas des blocs ;
- le signe négatif **survit à tous les choix de gap** : 1 h → −20,2 (t −2,70) ;
  4 h → −30,5 (t −4,52) ; 12 h → −17,8 (t −2,42) ;
- `corr(log taille d'épisode, perf de l'épisode) = +0,114` → **les gros épisodes
  performent mieux**, ce qui est exactement pourquoi la pondération par taille
  (= la moyenne brute) gonfle le chiffre ;
- contrôle de cohérence : `size_weighted_check` redonne à la décimale la moyenne
  brute pour les trois niveaux → le calcul est correct.

### 1.4 Le verdict ne dépend pas de la convention de pondération (test ajouté)

Objection légitime : la moyenne brute (size-weighted) **est** le PnL par trade
réellement réalisable ; l'équipondération par épisode répond à une autre question.
`evidence/t1e_weighting_robustness.py` teste donc directement la **quantité
tradable** : block-bootstrap par épisode L3 (on rééchantillonne des **épisodes
entiers avec leurs poids**, 8 000 tirages), et on recalcule la moyenne
size-weighted à chaque tirage.

| population | thr | net14 size-weighted | **IC95 block-bootstrap** | p(≤0) | IC95 « naïf iid » | PnL porté par le top-1 épisode | net14 **hors top-5 épisodes** |
|---|---|---|---|---|---|---|---|
| `SS` exhaustion (SSE_CONT) | 3 | **+40,0** | **[−9,6 ; +91,7]** | 0,063 | [+18,3 ; +61,7] | **39,1 %** | **−13,9** |
| `SS` exhaustion | 2 | +15,5 | **[−18,2 ; +51,1]** | 0,201 | [+1,7 ; +29,2] | **67,9 %** | **−17,2** |
| **contrôle** `LC` exhaustion | 3 | +27,1 | **[+4,1 ; +51,1]** | **0,010** | [+10,8 ; +43,3] | 21,4 % | **+10,8** |
| **contrôle** `LC` exhaustion | 2 | +16,2 | **[+1,6 ; +31,5]** | **0,015** | [+6,3 ; +26,2] | 24,5 % | **+6,3** |

Lecture :

1. Le `+40,0 bps` publié **n'est pas significatif** une fois le clustering pris en
   compte, même en gardant la pondération qui le favorise. L'IC naïf iid
   ([+18,3 ; +61,7]) est ~2× trop étroit : c'est là qu'est née l'illusion.
2. **Les 3 meilleurs épisodes portent 108 % du PnL total ; les 5 meilleurs, 130 %.**
   Retirer 5 épisodes sur 437 fait passer l'edge de +40,0 à **−13,9**.
3. Décomposition temporelle : avant 2025 → +7,0 bps, IC [−40,5 ; +59,9] (rien) ;
   2025+ → +114,6 bps, IC [+3,5 ; +195,4]. Le « **+114,6 bps OOS** » publié est
   **reproduit exactement** — et c'est un effet d'une seule année, porté par une
   poignée d'épisodes, dont l'IC touche presque zéro.
4. **Calibration** : le même test appliqué au contrôle `LONG_CASCADE` exhaustion
   (l'alpha déjà en shadow) **passe** (p=0,010/0,015, survit au retrait du top-5,
   équipondéré et size-weighted d'accord de signe). La méthode ne tue donc pas
   tout ce qu'elle touche : elle sépare.

**Robustesse du test décisif** (`evidence/t1f_bootstrap_robustness.py`, 4 gaps
d'épisode × 3 graines) : l'IC95 de `SS` exhaustion **contient zéro dans les 24
configurations** — gap 1 h : [−20,3 ; +51,7] (thr2) / [−15,0 ; +93,3] (thr3) ;
gap 24 h : [−16,5 ; +49,9] / [−6,6 ; +89,1] ; p(≤0) de 0,050 à 0,216. Le contrôle
`LC` exhaustion exclut zéro dans 9 configurations sur 12 (p 0,006–0,018 aux gaps
4 h/12 h/24 h, marginal 0,036 au gap 1 h). Le verdict ne tient à aucun réglage.

### 1.5 Verdict

> ### `LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION` → **REJECTED**
> Définitif, dans **les deux directions**, aux **deux seuils**, sous **les deux
> conventions de pondération**, avec un contrôle positif qui passe le même test.

Ce qu'il faut retenir, et qui est la vraie réponse au blocage :

- Le statut `BLOCKED` reposait sur l'idée qu'un choix de signe débloquerait
  +40,0/+114,6 bps. **Ce chiffre n'existe pas.** C'est un artefact de clustering
  et de concentration (5 épisodes sur 437), concentré sur 2025.
- La question de signe, elle, était **factuelle et tranchable en une heure** sur
  les données brutes (§1.1). Elle n'a jamais été le vrai obstacle. La leçon de
  méthode : quand un candidat est bloqué sur une « ambiguïté », vérifier d'abord
  que la valeur qu'on croit débloquer résiste au déclustering — ici elle n'y
  résistait pas, et 4 mois de blocage portaient sur un chiffre vide.
- `repeat_variant.py:53` exclut `SHORT_SQUEEZE` du trading depuis le début.
  **Cette exclusion était correcte** et doit le rester ; elle n'a jamais coûté
  d'edge au projet.

### 1.6 Note d'exécution (sans objet ici, mais consignée)

Si ce candidat avait été validé, il aurait **hérité de la contrainte de latence**
documentée dans `reports/live_alpha_lab/DECISION_LATENCY_AUDIT_2026-09-05.md` :
le détecteur figé lit `data/derivatives_backfill/binance_vision_metrics/` — un
backfill d'archives quotidiennes, pas un flux live — d'où un retard médian de
45,5 h pour un horizon de 4 h et **100 % de décisions périmées** pour toute la
famille cascade. Aucun déploiement de cette famille n'est possible sans résoudre
d'abord la latence. Le verdict `REJECTED` rend le point théorique pour ce
candidat, mais il reste actif pour les 4 alphas figés qui en dépendent.

---

## 2. CIBLE 2 — `LIQ_REPEAT_VOL_GATE` : **UNCONFIRMABLE_IN_HORIZON** (définitif)

Question préenregistrée : *existe-t-il une formulation du même mécanisme
économique dont le taux d'épisodes indépendants soit nettement plus élevé que
l'état macro lent « vol BTC 24 h élevée » (268 épisodes → ETA 28–38,5 ans) ?*

Trois reformulations, décidées avant tout résultat, seuil top 30 % préenregistré,
percentile **causal trailing** (rang parmi les 200 events précédents du même
symbole, min 30, sans le point courant). Population de base : `LONG_CASCADE`
exhaustion (`n_events_sym_24h ≥ 2`), la spec du code figé, n=5 457.
Unité L4 ajoutée = **épisode de régime du gate** (run maximal d'events consécutifs
de même état, coupé à 24 h) — l'unité même qui avait détruit la preuve d'origine.

`evidence/t2_vol_gate_reformulations.py` → `_thr2.json` :

| gate | conditionneur | n ON | delta ON−OFF (L4) | t (L4) | ép. ON L4 (hist.) | taux ép. ON/sem. | **ETA (L4)** | années delta>0 | bras ON à −28 bps |
|---|---|---|---|---|---|---|---|---|---|
| `B0` (reproduction) | vol BTC 24 h **macro** | 1 583 | +30,4 | 2,13 | 121 | 0,735 | **29,0 ans** | 4/5 | +20,4 |
| `R1` | vol 24 h **du symbole** | 1 013 | **+113,7** | **5,00** | **590** | 2,13 | **5,83 ans** | 4/5 | +92,5 |
| `R2` | vol rapide `|px_ret_1h|` du symbole | 979 | +66,1 | 3,41 | 828 | 4,29 | **8,77 ans** | **5/5** | +51,1 |
| `R3` | intensité de l'event `|oi_drop_z|` | 1 221 | +39,7 | 2,75 | 1 046 | 6,15 | **12,73 ans** | 4/5 | +32,5 |

Premier résultat : `B0` **reproduit le diagnostic d'origine** (29,0 ans vs 28–38,5
publiés) — le pipeline de mesure est calibré.

Second résultat : les reformulations **fonctionnent économiquement et améliorent
massivement l'ETA** — `R1` divise l'ETA par 5 (29,0 → 5,8 ans) et multiplie par
4,9 le nombre d'épisodes indépendants (121 → 590). Le mécanisme « n'activer le
repeat-cascade que sous stress » est **plus fort en local qu'en macro** : le
stress qui compte est celui du symbole, pas celui de BTC. C'est un vrai résultat
économique.

**Mais aucune ne descend sous les 3 ans**, et le contrôle d'indépendance montre
que même 5,83 ans est l'estimation la plus optimiste possible.

### 2.1 Les épisodes gagnés sont-ils vraiment indépendants ?

C'est le piège redécouvert 4 fois dans ce projet : « 49 symboles × états locaux »
ne fait pas 49 fois plus d'information si les symboles bougent ensemble.
`evidence/t2b_gate_independence_check.py` :

| | recouvrement avec le macro `B0` (Jaccard) | lift P(local ON \| macro ON)/P(… \| macro OFF) | part d'état partagée intra-jour (obs.) | attendu si indépendant | **ETA sur l'unité JOUR (L2)** | t (L2) |
|---|---|---|---|---|---|---|
| `B0` | — | — | — | — | 62,5 ans | 1,45 |
| `R1` | 0,270 | **2,93** | **0,900** | 0,814 | **53,9 ans** | **1,26** |
| `R2` | 0,188 | 1,73 | 0,860 | 0,821 | **39,3 ans** | **1,50** |
| `R3` | 0,164 | 1,17 | 0,810 | 0,776 | **68,9 ans** | **1,14** |

Deux faits décisifs :

1. **Concordance cross-symbole en excès.** Les états « locaux » sont partagés à
   90 % au sein d'une même journée pour `R1` (attendu 81,4 % sous indépendance),
   et `R1` a un lift de 2,93 avec le macro. Les 590 « épisodes indépendants » de
   `R1` sont donc **partiellement le même état macro porté par 49 symboles** —
   exactement le mode de défaillance qui avait tué la version d'origine.
2. **Sur l'unité JOUR CALENDAIRE** — l'un des trois niveaux de déclustering
   *imposés* par le briefing, et le plus conservateur ici — le delta n'est
   **significatif pour aucune reformulation** (t = 1,14 à 1,50) et l'ETA est de
   **39 à 69 ans**.

L'écart 5,8 ans (unité L4, par symbole) vs 53,9 ans (unité jour) **est** la mesure
de la dépendance résiduelle. La vérité est entre les deux, et les deux bornes sont
au-dessus de 3 ans.

### 2.2 Diagnostic supplémentaire — et si on regardait le bras ON tout seul ?

Le critère préenregistré porte sur le **delta ON−OFF**, parce que le candidat au
registre est un **gate** (« ajoute-t-il de la valeur ? »). Le lecteur demandera
néanmoins ce que vaut le bras ON pris comme alpha. Réponse, gate §2 standard
appliqué au bras ON (`evidence/t2c_on_arm_supplementary.py`) — **ceci n'est pas
le critère préenregistré et ne change aucun verdict** :

| population | n | L3 | net14 | net28 | net14 L3 | t L3 | ex-best-year | **ETA** |
|---|---|---|---|---|---|---|---|---|
| base **non gatée** | 5 457 | 1 645 | +16,2 | +2,2 | +22,5 | 4,11 | +20,7 | **5,44 ans** |
| bras ON `B0` (macro) | 1 583 | 314 | +47,4 | +33,4 | +28,1 | 2,41 | +20,2 | **12,61 ans** |
| bras ON `R1` | 1 013 | 342 | **+95,4** | **+81,4** | +61,9 | 2,94 | +41,1 | **8,39 ans** |
| bras ON `R2` | 979 | 392 | +67,7 | +53,7 | +49,4 | 2,86 | +31,9 | **8,49 ans** |
| bras ON `R3` | 1 221 | 618 | +42,5 | +28,5 | +36,0 | 3,27 | +27,9 | **6,32 ans** |

Le résultat est net et il **renforce** le verdict au lieu de le nuancer : les bras
ON sont économiquement solides (tous survivent au stress 28 bps, tous positifs
hors meilleure année, t déclusterisés 2,4–3,3), mais **leur ETA est systématiquement
PIRE que celui de la base non gatée** (5,44 ans → 6,3 à 12,6 ans).

> **Le gate améliore l'edge mais dégrade la confirmabilité** : il retire plus
> d'échantillon indépendant qu'il n'ajoute de signal/bruit. Il n'existe donc
> aucune lecture — gate ou alpha — sous laquelle cette famille passe sous 3 ans.

### 2.3 Verdict

> ### `LIQ_REPEAT_VOL_GATE` → **UNCONFIRMABLE_IN_HORIZON**, dossier clos
> Règle de décision préenregistrée appliquée telle quelle : aucune des trois
> reformulations n'atteint ETA < 3 ans. Meilleur cas `R1` = **5,83 ans** sur
> l'unité la plus favorable, **53,9 ans** et non significatif sur l'unité jour.

Ce qui est acquis et mérite d'être consigné même si le candidat est clos :

- Le mécanisme économique est **corroboré et affiné** : le conditionnement de
  stress marche mieux **par symbole** (`R1`, delta +113,7, t=5,00, 4/5 années)
  que **macro** (`B0`, +30,4, t=2,13). Si la famille cascade est un jour ravivée
  (après résolution de la latence), c'est la formulation locale qu'il faut
  retenir, pas la macro.
- `R2` (vol rapide) est la seule à être **positive sur 5/5 années** — le meilleur
  profil de stabilité du lot, avec un ETA de 8,77 ans.
- Je n'ai testé **que** R1/R2/R3, préenregistrées. Toute quatrième idée serait un
  refit et n'est pas proposée comme candidate.

---

## 3. CIBLE 3 — second regard sur les 5 REJETÉS : **5/5, le rejet tient**

Audit documentaire + vérifications ciblées, **aucun re-backtest**. Grille de
décision préenregistrée : `MECHANISM_DEAD` si au moins un de (a) signe opposé
mesuré avec un t déclusterisé décisif, (b) non-indépendance vis-à-vis d'un alpha
existant, (c) donnée mono-régime non extensible, (d) ETA structurellement > 3 ans
par la fréquence intrinsèque du phénomène. `EXPRESSION_DEAD_MECHANISM_OPEN`
**uniquement** si je peux nommer, avant de lancer quoi que ce soit, la raison
économique pour laquelle la première expression était mal choisie. En cas de
doute, le rejet tient.

### 3.1 Un écran uniforme pour trancher (d) sans re-backtester

`evidence/t3_eta_screen.py`. Le `n_required` du §2 ne dépend que de deux nombres
que **toute découverte publie déjà** — le N indépendant et le t :

```
n_required = (z_α + z_β)² / haircut² × N_indep / t²  =  24,74 × N_indep / t²
```

(α=5 % unilatéral, puissance 80 %, haircut 50 % — les conventions du briefing).
Calibration de l'écran sur les deux alphas que le projet a acceptés : il redonne
**18,4 ans** pour `AMIHUD_ILLIQUIDITY_PREMIUM_V1` (ETA publié ~17,0) et **11,0 ans**
pour `LIQ_CASCADE_REPEAT` exhaustion (ETA que je mesure en §1.2 : 11,0). L'écran
est juste.

| candidat / expression | source des chiffres | N_indep | t | n_required | **ETA** |
|---|---|---|---|---|---|
| `POSITIONING_TAKER_FLOW`, grille 7D | **la découverte elle-même** (+51,9 bps) | 224 | 2,16 | 1 187 | **31,8 ans** |
| `GLOBAL_ACCOUNT_LSR_FADE`, grille 7D | **la découverte elle-même** (+51,5 bps) | 239 | 2,05 | 1 406 | **35,3 ans** |
| `BTC_ETH_CURVE_STEEPNESS` | **la découverte elle-même** (+77,8 bps) | 157 | 1,94 | 1 032 | **49,4 ans** |
| `CROSS_SECTIONAL_MOMENTUM_CVD`, côté tradable | validation (+4,5 bps) | 333 | 0,22 | 170 152 | 3 272 ans |
| `CROSS_SECTIONAL_MOMENTUM_CVD`, côté filtre | validation (−61,4 bps) | 330 | −1,58 | 3 269 | **62,9 ans** |
| `BTC_ETH_CURVE_STEEPNESS`, PRIMARY_SPEC | validation (+17,1 bps) | 57 | 0,28 | 17 980 | 860 ans |
| *piste* « ETH single-asset » signalée par la validation | validation (+259,6 bps, 4/4 ans) | 61 | 2,35 | 273 | **13,1 ans** |
| *piste* miroir momentum du LSR, signalée non adoptée | validation (+19,6 bps) | 63 | 0,64 | 3 804 | **8,0 ans** |
| *[référence]* `AMIHUD_ILLIQUIDITY_PREMIUM_V1` — FROZEN | registre | 332 | 2,92 | 963 | 18,4 ans |
| *[référence]* `LIQ_CASCADE_REPEAT` exhaustion — shadow | §1.2 de ce rapport | 789 | 2,75 | 2 580 | 11,0 ans |

**Aucune ligne ne passe sous 3 ans — y compris les deux « pistes » que les
rapports de validation avaient laissées ouvertes.** Le critère (d) est donc
rempli pour l'ensemble, et les pistes ouvertes sont refermées par le calcul,
pas par un avis.

### 3.2 Verdict par candidat

#### `CROSS_SECTIONAL_MOMENTUM_CVD` → **MECHANISM_DEAD**, critère (d)

Le rebalancement est **hebdomadaire** : ~52 épisodes indépendants par an, plafond
structurel du mécanisme et non choix de paramètre. Le côté tradable (CONFIRMED)
est non significatif (t=0,22), fragile (le retrait d'une seule date sur 333
inverse le signe : +4,5 → −1,5 bps ; 4 perturbations sur 9 changent de signe) et
demande 3 272 ans. Le côté robuste (DIVERGENT, 0 inversion sur 9 perturbations)
est **un filtre, pas une position**, et demande tout de même 63 ans.
Pour atteindre 3 ans il faudrait `n_required < 156`, soit un t d'environ **7** sur
les 333 épisodes disponibles, contre 0,22 mesuré. Ce n'est pas une question
d'expression : aucune expression *hebdomadaire* de ce mécanisme n'est
confirmable. **Le rejet tient, dossier clos.**

#### `BTC_ETH_CURVE_STEEPNESS` → **MECHANISM_DEAD**, critères (d) et (c)

- **(d)** ~21 épisodes/an (franchissements `|z| ≥ 1,5` d'un spread de pente
  trimestrielle). ETA 140–218 ans selon la validation, 860 ans par l'écran
  uniforme. Il faudrait `n_required < 63`, soit un t d'environ **4,7** sur les
  57 épisodes disponibles, contre 0,28 mesuré.
- **(c)** l'histoire **ne peut pas être étendue vers l'arrière** : avant le
  2023-08-18, Binance ne cotait quasiment jamais deux trimestriels simultanément
  (26 jours sur 332 en 2021, 21 sur 365 en 2022). Le spread near−far *n'existe pas*
  avant cette date. L'échantillon ne peut croître qu'à ~21 épisodes/an.
- La piste laissée ouverte par la validation — le momentum **ETH single-asset**
  (+259,6 bps, t=2,35, 4/4 années) — **n'est pas ce mécanisme** : c'est
  précisément ce contre quoi le candidat était défini (« les variantes
  single-asset sont DEAD, seul le cross-asset marche »), et la validation a montré
  que cette thèse centrale s'**inverse**. Refermée par l'écran : **13,1 ans**.
  Elle ne doit pas être ressuscitée ici ; si quelqu'un y tient, il lui faut son
  propre préenregistrement, et elle échouera au gate ETA à l'arrivée.
- **Le rejet tient, dossier clos.**

#### `POSITIONING_TAKER_FLOW` → **MECHANISM_DEAD**, critère (d)

C'est le cas où j'ai le plus hésité, et il mérite d'être explicité.

Ce que la validation a réellement testé n'est **pas** la revendication d'origine :
la source imposée (`data/positioning`, archive fapi live) ne contient que
**48 jours** — je mesure aujourd'hui **51 jours, 2026-07-16 → 2026-09-05**, un seul
régime, sans profondeur historique possible (rétention API Binance = 30 jours).
La grille 7 jours d'origine n'y donnerait que ~6 fenêtres non chevauchantes, donc
la validation a dû préenregistrer un **horizon de 24 h**. C'est une raison
économique nommable, et pas « les paramètres étaient mauvais » : à 24 h on mesure
un déséquilibre de flux intraday, à 7 jours une accumulation de positionnement —
**ce ne sont pas le même mécanisme**. La validation le dit elle-même et refuse
explicitement de conclure sur la revendication multi-annuelle d'origine.

Le mécanisme à 7 jours est donc **non testé**, pas réfuté. Mais il n'a pas besoin
d'être testé pour être clos : **sur les chiffres publiés par la découverte
elle-même** (N_indep=224, t=2,16, ~37 épisodes indépendants/an), l'écran donne
`n_required` = 1 187 et **ETA = 31,8 ans**. Le critère (d) est rempli sans
qu'aucune donnée nouvelle ne soit nécessaire.

Ce qui est mesuré sur l'expression 24 h reste par ailleurs sans appel : **aucun
edge brut dans les deux directions** (gross −7,3 à +7,8 bps), 0 perturbation sur 7
qui repasse positive, et un N indépendant réel de **49 clusters systémiques** et
non 978 (surestimation ×20). **Le rejet tient, dossier clos.**

#### `GLOBAL_ACCOUNT_LSR_FADE` → **MECHANISM_DEAD**, critères (d) et (b)

- **(d)** sur les chiffres de la découverte (N_indep=239, t=2,05, ~40 épisodes/an) :
  `n_required` = 1 406, **ETA = 35,3 ans**.
- Le signe mesuré est en outre **opposé et constant** : les 7 perturbations
  testant la direction fade revendiquée sont négatives, et **la plupart le sont
  avant coûts** (gross −12,4 à −42,1 bps). N indépendant réel : 63 clusters
  systémiques, pas 685 (×11).
- **(b)** J'avais une reformulation économiquement défendable à proposer, et je la
  nomme telle qu'elle m'est venue avant toute vérification : le `global_account`
  LSR est un ratio **de comptes**, pas de **notionnel** — une foule de petits
  comptes peut être longue à 80 % en headcount tout en portant 5 % du risque, si
  bien que le « crowding » mesuré n'est pas le crowding qui compte. La version
  notionnelle serait `top_position`. Vérification faite : **c'est exactement ce
  qu'utilise déjà l'alpha live `WHALE_LSR_SCREEN_V1`.** La reformulation que
  j'aurais proposée est donc déjà déployée — critère (b), non-indépendance.
- La piste « miroir momentum » signalée (non adoptée) par la validation est
  refermée par l'écran : **8,0 ans**.
- **Le rejet tient, dossier clos.**

#### `OI_CVD_MEMORY_OVERLAP` → **MECHANISM_DEAD**, critères (b) et (a) — le plus net des cinq

- **Moitié OI (b)** : **79 % des events `LONG_CASCADE` du détecteur de production
  se retrouvent** dans un détecteur d'OI-down construit **indépendamment** (autre
  source, autre pipeline, autre seuil). Mécaniquement attendu — le déclencheur de
  `LIQ_CASCADE` *est* un z-score de chute d'OI — mais désormais **établi
  empiriquement** et non par assertion. C'est une **corroboration** de
  `LIQ_CASCADE_REPEAT_V1`, pas un alpha indépendant. Aucun nouvel `alpha_id`.
- **Moitié CVD (a)** : sur le résidu **non chevauchant** (N=5 024, **3 530 épisodes
  indépendants**, 63 jours — tous les planchers de preuve franchis), la forme
  revendiquée « la répétition renforce » est **absente et inversée** : onset
  +7,51 bps bruts (le *meilleur* bucket), exhaustion −2,47 ; différence
  exhaustion−onset **−9,98 bps, t=−1,93** ; bootstrap par blocs sur l'edge
  exhaustion : **IC 90 % [−23,0 ; −9,4], P(moyenne>0) = 0,000**. Ce n'est pas un
  manque de puissance, c'est un résultat négatif propre.
- Et il vient avec sa **raison économique**, ce qui est la meilleure façon de
  clore un mécanisme : la vente **forcée** s'épuise à la 3ᵉ répétition parce que le
  stock de longs sur-leviérisés est **fini** ; la vente **agressive organique**
  (flux informé, tendance, news) n'a aucune dynamique de stock fini et ne doit pas
  présenter la même forme. Le faible recouvrement *en nombre* masquait un fort
  recouvrement *économique*.
- **Le rejet tient, dossier clos.**

### 3.3 Ce que cet audit révèle sur le processus (livrable transverse)

`n_required = 24,74 × N_indep / t²` **ne dépend que de deux nombres que chaque
rapport de découverte publie déjà**. L'écran ETA est donc applicable **au moment
de la découverte**, pour un coût nul.

Appliqué rétroactivement, il aurait classé `POSITIONING_TAKER_FLOW` (31,8 ans),
`GLOBAL_ACCOUNT_LSR_FADE` (35,3 ans) et `BTC_ETH_CURVE_STEEPNESS` (49,4 ans)
`UNCONFIRMABLE_IN_HORIZON` **sur leurs propres chiffres de découverte** — c'est-à-dire
avant de dépenser trois workers de validation complets. Les rapports de validation
ont produit des résultats justes et utiles, mais leur conclusion était déjà lisible
dans le résumé de la découverte.

**Recommandation de processus** : faire de `24,74 × N_indep / t²` une colonne
obligatoire du scoreboard de découverte, au même titre que `net_bps`. Un candidat
dont l'ETA dépasse 3 ans dès la découverte ne devrait pas entrer en file de
validation — il devrait être versé directement en `UNCONFIRMABLE_IN_HORIZON` avec
son chiffre.

**Observation structurelle, à dire franchement** : appliqué aux deux alphas que le
projet a *acceptés*, l'écran donne **18,4 ans** (`AMIHUD`, FROZEN) et **11,0 ans**
(`LIQ_CASCADE_REPEAT`, shadow). **Aucun alpha du projet ne passe actuellement la
barre des 3 ans.** Le goulot d'étranglement n'est donc pas le taux de rejet en
validation : c'est que **tout le portefeuille est construit sur des mécanismes à
faible fréquence d'épisodes indépendants**. C'est exactement le point que le §2 du
briefing désigne comme « le vrai critère manquant dans ce projet », et cet audit le
confirme quantitativement. Chercher des mécanismes à **haute fréquence d'épisodes
indépendants** vaut mieux que chercher des mécanismes à haut bps : à t constant,
diviser l'ETA par 10 demande de multiplier le taux d'épisodes par 10, ce qu'aucun
gain de bps ne peut compenser.

---

## 4. CIBLE 4 — `MICROSTRUCTURE_ALL_ROUND3` : **DATA_LIMITED**, et **pas sur la trajectoire** de devenir jugeable

`evidence/t4_microstructure_readiness.py` (ré-exécutable, scanne
`data/microstructure_reduced/raw/`).

Convention préenregistrée : l'unité indépendante est le **jour calendaire** ; un
jour partiel ne compte pas ; seuil de jugeabilité **≥ 60 jours complets ET ≥ 2
régimes de vol distincts**. Un jour est complet si les 24 heures sont présentes
pour 3 venues × 3 symboles **sur les deux flux** (`bbo` et `trades`) = 216
fichiers horaires par flux.

### 4.1 État au 2026-09-05 06:27 UTC

| date | heures présentes | complet ? |
|---|---|---|
| 2026-08-31 | 1 / 24 (23 h seulement — démarrage du collecteur) | non |
| 2026-09-01 | 24 / 24 | **oui** |
| 2026-09-02 | 24 / 24 | **oui** |
| 2026-09-03 | 24 / 24 | **oui** |
| 2026-09-04 | 9 / 24 (trou 07:00 → 21:59 — incident disque du 2026-09-04) | non |
| 2026-09-05 | 7 / 24 (en cours) | non |

> **3 jours indépendants complets sur disque.** Il en faut 60.

Empreinte : **4,04 GiB** ; **1,205 GiB par jour complet** (moyenne des 3 jours
complets — cohérent avec les ~0,89 Go/j annoncés au lancement, le volume a
augmenté). Le collecteur tourne (`futur-microstructure-reduced.service`, unité
utilisateur, relancée à 00:07 aujourd'hui). Rendement observé : **0,75 jour
complet par jour calendaire écoulé** (1 jour détruit sur 4 par l'incident).

### 4.2 Date cible — et pourquoi elle n'est pas atteignable en l'état

| scénario | date à laquelle la famille devient jugeable |
|---|---|
| 100 % de disponibilité à partir de maintenant | **2026-11-01** (57 jours de plus) |
| au rendement observé (0,75 j/j) | **2026-11-20** |
| **au budget disque actuel du collecteur** | **jamais** — arrêt vers le **2026-09-11**, à ~9 jours complets |

**Le facteur limitant n'est pas le temps, c'est le disque.** Le service tourne
avec `--disk-budget-gb 12` (plafond dur sur sa propre empreinte, vérifié dans
`scripts/collect_microstructure_reduced.py::disk_budget_status` — le collecteur
**s'arrête** quand il est atteint, il n'écrase rien) et `--min-free-disk-gb 15`.

- marge restante sous le plafond : **7,96 GiB → 6,6 jours** → arrêt estimé au
  **2026-09-11**, à environ **9 jours complets** sur les 60 requis ;
- volume nécessaire pour atteindre 60 jours complets : **68,7 GiB**, soit
  **au-delà du plafond collecteur (12 GiB) ET au-delà des 57,1 GiB libres sur la
  machine**.

### 4.3 Verdict et ce qu'il faudrait

> ### `MICROSTRUCTURE_ALL_ROUND3` → **DATA_LIMITED** (3/60 jours), **non jugeable
> avant le 2026-11-20 dans le meilleur des cas, et actuellement bloqué au
> 2026-09-11 par le budget disque.**

Il ne s'agit pas d'attendre : sans décision explicite de l'utilisateur, la
collecte s'arrête dans ~6 jours et la famille restera à ~9 jours, c'est-à-dire
définitivement mono-régime — la leçon `market_physics_v3` répétée à l'identique.
Trois leviers possibles (aucun n'est appliqué ici, je ne touche pas au service) :

1. **Réduire le débit** — c'est le levier le moins coûteux : agréger le BBO en
   barres (le flux `bbo` pèse 90 % du volume : 1 132 Mo/j contre 111 Mo/j pour
   `trades`). Un BBO échantillonné à 100 ms ou agrégé en barres 1 s ferait tenir
   60 jours dans quelques GiB, en préservant la quasi-totalité des mécanismes de
   round 3 (OFI, microprice, déséquilibre BBO, absorption, lead-lag cross-venue —
   aucun n'a besoin de chaque tick de top-of-book).
2. **Réduire la portée** — 1 symbole au lieu de 3 divise par ~3, mais détruit le
   lead-lag cross-symbole.
3. **Relever le plafond** — nécessiterait ~69 GiB alors que la machine en a 57
   libres : **non viable sans le levier 1**.

Deuxième critère préenregistré, à ne pas oublier : **≥ 2 régimes de vol
distincts**. Il n'est pas garanti par le calendrier — 60 jours consécutifs dans
un marché calme resteraient `REGIME_DEPENDENT`. Il devra être vérifié à
l'échéance, pas supposé.

---

## 5. Ce que j'ai tué, et pourquoi

| ce qui meurt | pourquoi, en une phrase |
|---|---|
| `LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION`, direction LONG (`SSE_CONT`) | le +40,0/+114,6 bps est un artefact : 5 épisodes sur 437 portent 130 % du PnL, l'IC bootstrap par blocs contient zéro dans 24 configurations sur 24, l'effet est entièrement 2025 |
| `LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION`, direction SHORT (`SSE_MEANREV`) | l'hypothèse symétrique préenregistrée ne paie pas non plus : −68,0 bps bruts, +2,5 bps déclusterisés (t=0,37), ETA 712 ans |
| l'idée que le blocage venait d'une ambiguïté de signe | trois sources indépendantes tranchent la sémantique en une heure (|t| = 30 à 65) ; la docstring du détecteur était déjà exacte ; le blocage portait sur un chiffre vide |
| `LIQ_REPEAT_VOL_GATE` sous toutes ses formes (macro, vol locale, vol rapide, intensité d'event) | le mécanisme est réel et plus fort en local qu'en macro, mais l'ETA reste 5,8 ans dans le meilleur cas et 39–69 ans sur l'unité jour ; **et le gate dégrade la confirmabilité** (5,44 ans non gaté → 6,3–12,6 ans gaté) |
| `CROSS_SECTIONAL_MOMENTUM_CVD` | plafond structurel : ~52 épisodes indépendants/an, il en faudrait un t de 7 pour tenir en 3 ans |
| `BTC_ETH_CURVE_STEEPNESS` | ~21 épisodes/an, et le spread near−far **n'existe pas** avant 2023-08-18 — l'histoire ne peut pas être étendue vers l'arrière |
| `POSITIONING_TAKER_FLOW` | 31,8 ans sur les chiffres de la découverte elle-même ; l'expression 24 h testée n'a aucun edge brut dans les deux directions |
| `GLOBAL_ACCOUNT_LSR_FADE` | 35,3 ans sur les chiffres de la découverte ; signe opposé avant coûts sur 7 perturbations/7 ; et la reformulation notionnelle que j'aurais proposée **est déjà l'alpha live `WHALE_LSR_SCREEN_V1`** |
| `OI_CVD_MEMORY_OVERLAP` | 79 % de recouvrement avec l'alpha existant côté OI ; forme inversée avec P(moyenne>0)=0,000 côté CVD, avec sa raison économique (stock fini de longs leviérisés vs vente organique) |
| la piste « ETH single-asset » laissée ouverte par la validation de la courbe | 13,1 ans à l'écran ETA — refermée par le calcul, pas par un avis |
| la piste « miroir momentum » laissée ouverte par la validation du LSR | 8,0 ans à l'écran ETA — idem |

**Zéro résurrection sur 5 rejets réexaminés.** C'est ce que le préenregistrement
annonçait (« je m'attends à clore la majorité ») et c'est le résultat honnête :
dans les cinq cas, la raison du rejet porte sur le **mécanisme** ou sur sa
**fréquence intrinsèque**, pas sur une expression malheureuse.

## 6. Limites de ce travail, énoncées franchement

1. **Une réimplémentation historique n'est jamais une confirmation forward.**
   Rien de ce rapport ne confirme quoi que ce soit en avant. Tous les verdicts
   positifs éventuels resteraient à confirmer en forward — et c'est précisément
   ce que les ETA disent impossible dans l'horizon du projet.
2. **La cible 1 est tranchée sur `data/events/liq_cascade_dataset.parquet`**, qui
   s'arrête au 2026-07-04. La sémantique du signe (§1.1) est en revanche établie
   sur des fenêtres plus récentes et sur une archive externe 2023–2024.
3. **L'écran ETA de la cible 3 est une arithmétique de puissance, pas un
   re-backtest.** Il suppose que le `t` et le `N_indep` publiés sont ceux d'unités
   réellement indépendantes. Quand ils ne le sont pas (cas fréquent : ×11 à ×20 de
   surestimation constatés sur le LSR et le taker flow), **le vrai ETA est encore
   pire** que celui que je reporte. L'écran est donc conservateur dans le bon sens :
   il ne peut pas faire passer pour mort un candidat vivant.
4. **La cible 4 est une mesure et une projection, pas une décision.** Je n'ai
   touché ni au service ni à sa configuration. Les trois leviers proposés sont des
   options à soumettre à l'utilisateur.
5. **Je n'ai pas modifié `configs/validation_registry.yaml`.** Les verdicts
   ci-dessus sont une proposition d'intégration, à appliquer par qui de droit.
6. La cible 2 n'a testé **que** R1/R2/R3, préenregistrées. Aucune quatrième
   variante n'a été essayée après coup ; il n'y a donc aucun refit dans ce rapport.

## 7. Reproduire

```bash
cd /home/qbee/futur
V=.venv/bin/python
E=reports/edge_discovery/alpha_hunt_2026-09-03_round4/w10_validation_push/evidence
$V $E/t1a_sign_semantics.py            # sémantique du signe, 3 sources
$V $E/t1b_kind_vs_liqflow.py           # label du détecteur vs flux réel
$V $E/t1c_short_squeeze_gate.py 3      # gate §2, seuil du round 2
$V $E/t1c_short_squeeze_gate.py 2      # gate §2, seuil du code figé
$V $E/t1d_decluster_diagnostics.py     # le déclustering est-il un artefact ?
$V $E/t1e_weighting_robustness.py      # bootstrap par blocs, moyenne size-weighted
$V $E/t1f_bootstrap_robustness.py      # 4 gaps x 3 graines
$V $E/t2_vol_gate_reformulations.py 2  # reformulations R1/R2/R3 du gate de vol
$V $E/t2b_gate_independence_check.py   # les épisodes locaux sont-ils indépendants ?
$V $E/t2c_on_arm_supplementary.py      # diagnostic bras ON (hors critère)
$V $E/t3_eta_screen.py                 # écran ETA uniforme des 5 rejetés
$V $E/t4_microstructure_readiness.py   # jours complets et date cible microstructure
```

Tous en **lecture seule** sur `src/`, `configs/`, `data/`,
`reports/live_alpha_lab/` ; écriture uniquement dans `evidence/`.
