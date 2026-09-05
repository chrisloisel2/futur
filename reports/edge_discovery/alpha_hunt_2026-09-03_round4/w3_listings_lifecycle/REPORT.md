# W3_LISTINGS_LIFECYCLE — RAPPORT

**Alpha Hunt Round 4** (`alpha_hunt_2026-09-03_round4`) — axe : cycle de vie des instruments
(cotations, radiations, âge, maturation, vagues de cotation).
Tests exécutés le 2026-09-03, **session interrompue avant rédaction** ; reprise, complétion et
rédaction le 2026-09-05. Préenregistrement : `PREREGISTRATION.md` (écrit avant tout test, fait foi).
Résultats machine : `RESULTS.json` (59 mécanismes). Scripts ré-exécutables : `evidence/`.

---

## 0. Verdict d'axe — à lire en premier

> **L'axe entier est `UNCONFIRMABLE_IN_HORIZON`.** Aucun mécanisme n'est `VALIDATED_FOR_FORWARD`,
> aucun n'est `PROMISING_NEEDS_VALIDATION`. Ce n'est pas un accident de mesure : c'est une
> propriété structurelle du gisement, démontrée au §3 et vraie *avant* de regarder le moindre
> rendement.

| verdict | n | mécanismes |
|---|---|---|
| `VALIDATED_FOR_FORWARD` | **0** | — |
| `PROMISING_NEEDS_VALIDATION` | **0** | — |
| `UNCONFIRMABLE_IN_HORIZON` | 8 | A1 (d4h/168h), A1b ×4, A3, C2, C2c_RAW |
| `REGIME_DEPENDENT` | 2 | E1b_btc30, E2b |
| `WEAK` | 12 | reste de la grille A1, A4b_h72h, A5 (7 j), E1b_bask7 |
| `DEAD` | 23 | tout l'axe B, C2b, C2c winsorisé, A2, E1c, F1, F2, horizons 28 j de A1/A5 |
| `DATA_LIMITED` | 11 | axe D entier, E1/E2 en déclustering mensuel (effondré), A4 apparié |
| `DESCRIPTIVE` | 3 | C1, C3, audit de survie S0 |

**Les trois résultats utiles de ce worker sont des kills :**

1. **Le « fade de listing » ne survit pas au funding.** Neutralisé par la coupe transversale
   contemporaine, le drift post-listing reste favorable à un short (+386 bps à 7 j, A1), mais
   **le funding cumulé des 30 premiers jours d'un perp vaut −310 bps**, contre +61 bps pour les
   contrats matures aux mêmes semaines (A3 : différentiel −321,5 bps, **t = −5,74**, l'estimation
   la plus significative de tout mon axe) : la jambe short *paie*.
   Net du funding des deux jambes, le spread tombe à **+231 bps à 7 j (t = 1,09)** et à
   **−425 bps à 28 j** (A5). Le trade n'existe pas.
2. **L'âge de l'instrument n'est pas un facteur transversal.** Sur univers filtré en liquidité,
   le livre « vieux − jeune » quotidien rapporte **+1,4 bps/jour brut, IC95 [−11,3 ; +13,8]**,
   t = 0,21 sur 323 semaines indépendantes (B1_1D). Le coefficient d'âge **univarié**, avant tout
   contrôle, vaut +0,2 bps par sigma (t = 0,09). C'était le seul candidat de l'axe à ETA
   potentiellement court ; il est nul, pas faible.
3. **Le « signal de régime par intensité de cotation » était un artefact de chevauchement.**
   Avec des fenêtres forward 7/30 j qui débordent des plages de régime (E1b), BTC donnait
   +1172 bps/30 j, t = 2,01. Sans chevauchement inter-plages (E1c), le même signal donne
   **−288 bps, t = −0,39**. La significativité venait entièrement du débordement.

---

## 1. Données, univers, et ce qui est PIT

| bloc | source | univers | fenêtre |
|---|---|---|---|
| Event-time cotations | `data/listings_backfill/binance/{listings_calendar,klines_1h,funding}` | **518 symboles** ayant des klines (720 barres 1 h = 30 j chacun) | 2023-01-15 → 2026-07-03 |
| Panel calendaire | `/home/qbee/futur-data-v2/data_v2/normalized/{perp_ohlcv,event_feature_panel}` (venue=binance, 5m → daily) | **312 symboles** (311 après éligibilité), 343 229 lignes symbole-jour éligibles | 2020-01-01 → 2026-07-31 |
| Funding réglé | `event_feature_panel.funding_is_settlement` → `funding_daily.parquet` | 312 symboles | 2020-01-01 → 2026-07-31 |
| Cascades (F2) | `data/events/liq_cascade_dataset.parquet` | 49 symboles seulement | — |

**PIT.** `onboard_ts` vient d'`exchangeInfo` et est annoncé à l'avance : aucun lookahead. L'âge à
`t` est `t − onboard_ts`, causal par construction. Toute feature roulante est en fenêtre fermée à
gauche (`ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING`). Le signal de vague (axe E) utilise les
cotations des **30 jours précédents** (`shift(1)`) converties en percentile **expanding**
(`min_periods=250`) — jamais un percentile plein-échantillon. Éligibilité : médiane roulante
causale 30 j du quote-volume ≥ 1 M$ ; **aucun plancher d'âge** n'est imposé au panel, c'est
l'objet même de l'axe B.

**Le contrefactuel, et ce qu'il change vraiment.** L'apport méthodologique annoncé au
préenregistrement était de comparer chaque cotation au **panier equal-weight des noms éligibles
sur la même fenêtre horaire** (`build_benchmark.py`) plutôt qu'à zéro, comme le faisait l'étude
interne de juillet 2026 (`reports/LISTING_EVENT_STUDY.md`). Mesuré, l'effet du contrefactuel est
**plus faible que prévu à court horizon et significatif à long horizon** :

| fenêtre | nouveau listing (médiane / moyenne) | panier éligible (médiane / moyenne) | part expliquée par le marché |
|---|---|---|---|
| d1h → 168 h (7 j) | −1395 / −319 bps | −53 / −39 bps | ~4 % de la médiane |
| d24h → 168 h | −1062 / −307 bps | −102 / −42 bps | ~10 % |
| d24h → 672 h (28 j) | −1914 / **+1379** bps | −496 / −206 bps | ~26 % de la médiane |

Donc **le drift post-listing est bien idiosyncratique, pas un simple « les alts baissent »** :
mon hypothèse préenregistrée A1 est *infirmée dans sa forme forte*, et la conclusion
opérationnelle de juillet (`ListingAgeGate`, `min_age_days=30`, aucun LONG sur un perp jeune)
**en sort renforcée**. Les deux corrections qui comptent réellement sont ailleurs : (a) l'écart
**médiane vs moyenne** (−1395 vs −319 bps à 7 j : la moyenne, seule quantité qui compose, vaut
un quart de la médiane) et (b) le **funding**, que l'étude de juillet n'incluait pas (§6.1, A5).

**Coûts.** Convention briefing (`−14` / stress `−28`) surchargée par le préenregistrement, plus
conservatrice et prioritaire pour le verdict : livre long/short 4 jambes → `−28` / stress `−56` ;
entrée sur perp de moins de 24 h → stress supplémentaire `−60` (reporté en `net_bps_thin60` dans
`RESULTS.json`). La colonne `net` du tableau §5 applique la convention *propre au mécanisme*, et
`net_stress` le stress correspondant. Pour les flux (A3, C2b) et les coefficients de régression
(B2), le coût est mis à 0 et c'est dit dans l'entrée JSON.

---

## 2. Biais de survie — vérification explicite (obligatoire sur cet axe)

Statuts morts = `SETTLING` ∪ `DELISTED` ∪ `DELISTED_NO_DATA`.

| univers | total | TRADING | SETTLING | DELISTED | DELISTED_NO_DATA | **morts** |
|---|---|---|---|---|---|---|
| panel calendaire | 312 | 259 | 38 | 14 | 1 | **53 (17,0 %)** |
| univers cotations (klines) | 518 | 419 | 95 | 4 | 0 | **99 (19,1 %)** |
| calendrier complet (référence) | 683 | 530 | 122 | 27 | 2 | 151 (22,1 %) |

**Les radiés sont donc inclus dans les deux univers testés.** Aucun test de ce rapport n'est
stampé `SURVIVORSHIP_BIASED`. Sur le panel, seulement 15 des 53 noms morts ont leur fin de vie
*à l'intérieur* de la fenêtre de données (les autres sont `SETTLING` en cours au 2026-07-31) —
c'est ce qui rend l'axe D exsangue (§6.4).

**Contrefactuel chiffré** (`S0_SURVIVORSHIP_AUDIT`) — le fade recalculé sans les noms morts :

| variante | % de morts | tous les noms (L3) | survivants seuls (L3) | écart | fade brut des morts | fade brut des vivants |
|---|---|---|---|---|---|---|
| d24h / h168h | 17,8 % | **+386,0 bps** | +109,9 bps | **−276,1** | +1171,1 bps | +67,8 bps |
| d24h / h672h | 18,0 % | −148,5 bps | −561,7 bps | **−413,3** | +2484,1 bps | −2479,2 bps |
| d1h / h168h | 17,8 % | +422,9 bps | +164,8 bps | **−258,1** | +1318,1 bps | +55,5 bps |

Le biais joue **dans le sens inverse de l'intuition** : ce sont les futurs radiés qui fadent, et
un univers de survivants *sous-estimerait* l'effet de 260 à 410 bps. A1 n'est donc pas un artefact
de survie. Symétriquement, cela dit que ce que capture l'axe A est en bonne partie « les perps qui
vont mourir baissent » — information non disponible à `t0`, ce qui plafonne l'exploitabilité.

**Limite déclarée et non contournable** : les perps radiés **avant 2023** et absents de
`fapi/exchangeInfo` ne sont pas récupérables (`listings_backfill_store.yaml`
`_meta.missing_delisted`). L'axe A n'est propre que sur 2023+.

---

## 3. Le budget de puissance de l'axe — pourquoi il est structurellement inconfirmable

C'est le résultat central. `n_required = 7,849 / (0,5·d)²` où `d` = Sharpe par épisode L3
(puissance 80 %, alpha 5 % bilatéral, **haircut 50 % obligatoire**). `event_rate` = épisodes
L3/semaine sur les 6 derniers mois de données (2026-02-01 → 2026-07-31).
`ETA = n_required / event_rate`.

**Taux d'épisodes indépendants mesurés sur cet axe :**

| unité macro L3 | épisodes / semaine (6 derniers mois) | commentaire |
|---|---|---|
| vague de cotation = semaine ISO | **0,696** | 30 cotations en 6 mois, réparties sur 18 semaines |
| vague de cotation = règle gap ≥ 7 j | **0,232** | règle préenregistrée, dégénérée : 35 vagues au total, la plus grosse en contient 128 |
| plage (spell) de régime de vague | **0,155** | 39 plages HI/LO ≥ 5 j sur 2124 jours, durée médiane 21 j |
| semaine calendaire (livre quotidien) | 1,006 | plafond absolu d'un livre panel déclusterisé à la semaine |
| radiation | **0,000** | zéro radiation datable dans les 6 derniers mois |

J'ajoute à chaque mécanisme le diagnostic **`t_stat_needed_for_eta_3y`** : le t de découverte
qu'il aurait fallu obtenir, à N et à taux d'épisodes constants, pour que l'ETA passe sous 3 ans.

| famille | N épisodes L3 | **t requis pour ETA < 3 ans** | meilleur \|t\| observé |
|---|---|---|---|
| cotations (A1 / A5) | 138-143 | **6,3 – 6,4** | 2,03 |
| conditionnement taille de vague (A4b) | 45 | **3,6** | −1,53 |
| régime de vague (E1c / E2b) | 17 | **4,7** | −2,29 |
| facteur âge quotidien (B1_1D) | 323 | **8,0** | 0,21 |
| carry d'âge (C2) | 305 | **8,0** | −2,31 |

Aucune famille n'approche son seuil, et le seuil n'est pas déplaçable : il ne dépend que du
nombre d'épisodes indépendants que le marché produit. Binance cote 100 (2023), 129 (2024),
245 (2025), 44 (2026 au 3 juillet) perps par an, groupés (médiane 3 cotations par semaine active,
une vague unique de 128) ⇒ **~36 épisodes indépendants par an au mieux**. Pour confirmer un edge
de Sharpe-par-épisode réaliste (0,15-0,20 avant haircut), il faut ~1000-2000 épisodes, soit
**30 à 60 ans de forward**.

> **Prédiction préenregistrée §4.4 : confirmée.** Le préenregistrement annonçait « je m'attends à
> ce que l'axe A soit `UNCONFIRMABLE_IN_HORIZON` et que le seul espoir d'ETA court soit B1 en
> version 1 jour ». C'est exactement ce qui s'est produit — sauf que B1_1D n'est pas faible, il
> est nul.

---

## 4. Déclustering — le mapping réellement appliqué

| | event-time (A, D) | livres panel (B, C, F) | signaux de régime (E) |
|---|---|---|---|
| **L1** | 1 événement / (symbole, 24 h) | 1 jour de rebalancement | 1 jour dans une plage |
| **L2** | 1 jour calendaire | 1 période de détention non chevauchante | 1 bloc de 30 j |
| **L3** | **1 vague de cotation** (semaine ISO) | semaine (détention ≤ 1 j) / mois (détention 7 j) | **1 plage (spell) de régime** |

`t_stat_declustered` et `bootstrap_ci95` sont **toujours** calculés au niveau L3 ; le t L2 est
reporté à titre indicatif (`t_stat_L2`) et n'a jamais servi de verdict. Block-bootstrap
5000 tirages, blocs = épisodes L3. Une vague de cotation compte pour **1** épisode, quel que soit
le nombre de perps qu'elle contient.

Deux dégénérescences rencontrées, et leur traitement :

- **Règle « vague = gap ≥ 7 j sans cotation » (préenregistrée) : dégénérée.** Elle agrège 128
  cotations dans une seule vague. Repli sur la semaine ISO, **prévu à l'avance**
  (PREREGISTRATION §5c). L'ETA sous la règle gap est reporté en parallèle (`eta_L3_alt`) et il est
  **pire**, pas meilleur (65,6 ans contre 78,6 sur A1_d1h_h24h) : le repli ne flatte rien.
- **E1/E2 appariés par mois : effondrement à L3 = 4 et L3 = 3.** C'est le mode d'échec qui a
  interrompu la session. Corrigé au §6.5.

**Règle post-hoc déclarée** (appliquée uniformément, et qui ne peut que *dégrader* un verdict) :
tout mécanisme avec **L3 < 10** est classé `DATA_LIMITED`, quel que soit son t. Elle frappe
E1 mensuel (L3 = 4 et 9), E2 (L3 = 3), D1 (L3 = 9) et A4 apparié (L3 = 0) — c'est-à-dire
exactement les cellules où un t de −2,39 ou −2,57 aurait pu être présenté comme un résultat.

---

## 5. Table complète des mécanismes (colonnes du gate §2)

Table générée automatiquement depuis `RESULTS.json` par `evidence/make_table.py` — aucun chiffre
saisi à la main. `net` / `net_stress` : convention de coût propre au mécanisme (§1). `t_L3` : t
sur épisodes indépendants L3. `ép./sem.` : épisodes L3 par semaine sur les 6 derniers mois.
`t_req 3a` : t de découverte qu'il aurait fallu pour un ETA < 3 ans.

| mécanisme | verdict | n_raw | L1 | L2 | **L3** | net | net_stress | **t_L3** | IC95 bootstrap | ans même signe | ex_best_year | n_req | ép./sem. | **ETA (ans)** | t_req 3a |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `A1_DRIFT_XSNEUTRAL_d4h_h168h` | `UNCONFIRMABLE_IN_HORIZON` | 479 | 479 | 335 | **138** | +444.5 | +416.5 | **+2.03** | [6 ; 898] | 3/4 | 2023 → +335 | 1 048 | 0.696 | **28.85** | 6.31 |
| `A1b_FADE_SIGN_TEST_d1h_h168h` | `UNCONFIRMABLE_IN_HORIZON` | 478 | — | — | **138** | — | — | **+7.17** | — | — | — | 84 | 0.696 | **2.32 (taux)** | 6.31 |
| `A1b_FADE_SIGN_TEST_d24h_h168h` | `UNCONFIRMABLE_IN_HORIZON` | 488 | — | — | **143** | — | — | **+8.68** | — | — | — | 60 | 0.696 | **1.64 (taux)** | 6.42 |
| `A1b_FADE_SIGN_TEST_d24h_h72h` | `UNCONFIRMABLE_IN_HORIZON` | 488 | — | — | **143** | — | — | **+5.66** | — | — | — | 140 | 0.696 | **3.86 (taux)** | 6.42 |
| `A1b_FADE_SIGN_TEST_d4h_h168h` | `UNCONFIRMABLE_IN_HORIZON` | 479 | — | — | **138** | — | — | **+7.91** | — | — | — | 69 | 0.696 | **1.91 (taux)** | 6.31 |
| `A3_FUNDING_CARRY_YOUNG` | `UNCONFIRMABLE_IN_HORIZON` | 230185 | — | — | **152** | -321.5 | -321.5 | **-5.74** | [-438 ; -213] | — | — | 145 | 0.696 | **3.99** | 6.62 |
| `C2_FUNDING_CARRY_YOUNG_VS_MATURE` | `UNCONFIRMABLE_IN_HORIZON` | 201978 | — | — | **305** | -230.4 | -258.4 | **-2.31** | [-383 ; -39] | — | — | 1 797 | 0.967 | **35.61** | 7.95 |
| `C2c_CARRY_RAW` | `UNCONFIRMABLE_IN_HORIZON` | 265485 | — | — | **305** | -230.4 | -258.4 | **-2.31** | [-383 ; -39] | — | — | 1 797 | 0.967 | **35.61** | 7.95 |
| `E1b_RISK_REGIME_SPELL_btc30` | `REGIME_DEPENDENT` | 2124 | — | — | **17** | +1157.9 | +1143.9 | **+2.01** | [53 ; 2308] | 2/6 | 2024 → -29 | 133 | 0.116 | **21.90** | 5.42 |
| `E2b_COND_MOM_SPELL` | `REGIME_DEPENDENT` | 2124 | 1466 | 49 | **17** | -1225.0 | -1253.0 | **-2.29** | [-2217 ; -247] | 4/6 | 2020 → +77 | 102 | 0.155 | **12.58** | 4.69 |
| `A1_DRIFT_XSNEUTRAL_d1h_h168h` | `WEAK` | 478 | 478 | 334 | **138** | +394.9 | +366.9 | **+1.72** | [-68 ; 888] | 3/4 | 2023 → +278 | 1 468 | 0.696 | **40.40** | 6.31 |
| `A1_DRIFT_XSNEUTRAL_d1h_h24h` | `WEAK` | 478 | 478 | 334 | **138** | +136.8 | +108.8 | **+1.23** | [-106 ; 417] | 4/4 | 2023 → +124 | 2 854 | 0.696 | **78.57** | 6.31 |
| `A1_DRIFT_XSNEUTRAL_d1h_h72h` | `WEAK` | 478 | 478 | 334 | **138** | +219.1 | +191.1 | **+1.37** | [-110 ; 594] | 4/4 | 2025 → +150 | 2 313 | 0.696 | **63.68** | 6.31 |
| `A1_DRIFT_XSNEUTRAL_d24h_h168h` | `WEAK` | 488 | 488 | 343 | **143** | +358.0 | +330.0 | **+1.85** | [-43 ; 767] | 3/4 | 2023 → +268 | 1 308 | 0.696 | **36.01** | 6.42 |
| `A1_DRIFT_XSNEUTRAL_d24h_h24h` | `WEAK` | 488 | 488 | 343 | **143** | +132.9 | +104.9 | **+1.70** | [-30 ; 347] | 3/4 | 2025 → +33 | 1 559 | 0.696 | **42.91** | 6.42 |
| `A1_DRIFT_XSNEUTRAL_d24h_h72h` | `WEAK` | 488 | 488 | 343 | **143** | +176.0 | +148.0 | **+1.66** | [-40 ; 440] | 4/4 | 2025 → +127 | 1 629 | 0.696 | **44.84** | 6.42 |
| `A1_DRIFT_XSNEUTRAL_d4h_h24h` | `WEAK` | 479 | 479 | 335 | **138** | +190.0 | +162.0 | **+1.87** | [-16 ; 438] | 4/4 | 2025 → +104 | 1 235 | 0.696 | **34.00** | 6.31 |
| `A1_DRIFT_XSNEUTRAL_d4h_h72h` | `WEAK` | 479 | 479 | 335 | **138** | +247.2 | +219.2 | **+1.70** | [-50 ; 581] | 4/4 | 2025 → +151 | 1 508 | 0.696 | **41.52** | 6.31 |
| `A4b_SIZE_COND_h72h` | `WEAK` | 488 | — | — | **45** | -450.3 | -478.3 | **-1.53** | [-995 ; 91] | — | — | 607 | 0.696 | **16.71** | 3.60 |
| `A5_FADE_NET_OF_FUNDING_d24h_h168h` | `WEAK` | 488 | 488 | 343 | **143** | +202.9 | +174.9 | **+1.09** | [-201 ; 625] | 3/4 | 2023 → +128 | 3 772 | 0.696 | **104** | 6.42 |
| `A5_FADE_NET_OF_FUNDING_d4h_h168h` | `WEAK` | 479 | 479 | 335 | **138** | +249.3 | +221.3 | **+1.17** | [-199 ; 718] | 3/4 | 2023 → +161 | 3 178 | 0.696 | **87.49** | 6.31 |
| `E1b_RISK_REGIME_SPELL_bask7` | `WEAK` | 2124 | — | — | **17** | -210.3 | -224.3 | **-1.12** | [-540 ; 135] | 4/6 | 2020 → -21 | 429 | 0.155 | **53.18** | 4.69 |
| `A1_DRIFT_XSNEUTRAL_d1h_h695h` | `DEAD` | 473 | 473 | 329 | **136** | +255.7 | +227.7 | **+0.45** | [-1136 ; 1304] | 3/4 | 2023 → -13 | 21 474 | 0.619 | **665** | 6.64 |
| `A1_DRIFT_XSNEUTRAL_d24h_h672h` | `DEAD` | 483 | 483 | 338 | **141** | -176.5 | -204.5 | **-0.16** | [-2272 ; 1174] | 1/4 | 2025 → +744 | 178 625 | 0.619 | **5 532** | 6.76 |
| `A1_DRIFT_XSNEUTRAL_d4h_h692h` | `DEAD` | 474 | 474 | 330 | **136** | +176.9 | +148.9 | **+0.26** | [-1515 ; 1340] | 3/4 | 2023 → -98 | 60 932 | 0.619 | **1 887** | 6.64 |
| `A2_D0_COND_SPREAD_h168h` | `DEAD` | 372 | — | — | **39** | -400.6 | -428.6 | **-0.52** | [-1800 ; 958] | — | — | 4 491 | 0.077 | **1 113** | 10.08 |
| `A2_D0_COND_SPREAD_h24h` | `DEAD` | 372 | — | — | **39** | +151.4 | +123.4 | **+0.42** | [-664 ; 989] | — | — | 7 010 | 0.077 | **1 737** | 10.08 |
| `A2_D0_COND_SPREAD_h72h` | `DEAD` | 372 | — | — | **39** | +185.9 | +157.9 | **+0.44** | [-752 ; 1134] | — | — | 6 218 | 0.077 | **1 541** | 10.08 |
| `A4b_SIZE_COND_h168h` | `DEAD` | 488 | — | — | **45** | -202.4 | -230.4 | **-0.44** | [-939 ; 617] | — | — | 7 391 | 0.696 | **203** | 3.60 |
| `A5_FADE_NET_OF_FUNDING_d1h_h168h` | `DEAD` | 478 | 478 | 334 | **138** | +189.1 | +161.1 | **+0.86** | [-282 ; 699] | 3/4 | 2023 → +93 | 5 813 | 0.696 | **160** | 6.31 |
| `A5_FADE_NET_OF_FUNDING_d24h_h672h` | `DEAD` | 483 | 483 | 338 | **141** | -453.4 | -481.4 | **-0.45** | [-2570 ; 906] | 2/4 | 2025 → +514 | 22 192 | 0.619 | **687** | 6.76 |
| `B1_FACTOR_1D` | `DEAD` | 2255 | 2255 | 2255 | **323** | -26.6 | -54.6 | **+0.21** | [-11 ; 14] | 4/7 | 2022 → -2 | 221 063 | 1.006 | **4 213** | 8.02 |
| `B1_FACTOR_7D` | `DEAD` | 322 | 322 | 322 | **75** | -62.7 | -90.7 | **-0.60** | [-146 ; 75] | 4/7 | 2021 → +6 | 6 476 | 0.232 | **535** | 8.05 |
| `B2_RESID_LIQ_1D` | `DEAD` | 2220 | 2220 | 2220 | **318** | +0.9 | +0.9 | **+0.47** | [-3 ; 5] | 4/7 | 2022 → +0 | 45 171 | 1.006 | **861** | 7.96 |
| `B2_RESID_LIQ_7D` | `DEAD` | 317 | 317 | 317 | **73** | -6.0 | -6.0 | **-0.31** | [-46 ; 30] | 2/7 | 2021 → +7 | 23 426 | 0.232 | **1 935** | 7.94 |
| `B3_BUCKET_MONOTONICITY_1D` | `DEAD` | 342918 | — | — | **231** | -19.1 | -47.1 | **+0.62** | [-20 ; 37] | — | — | 18 716 | 0.851 | **422** | 7.38 |
| `B3_BUCKET_MONOTONICITY_7D` | `DEAD` | 341052 | — | — | **231** | -22.5 | -50.5 | **+0.07** | [-164 ; 169] | — | — | 1 670 039 | 0.851 | **37 618** | 7.38 |
| `C2b_FUNDING_LEVEL_DIFFERENTIAL` | `DEAD` | 201978 | — | — | **305** | +2.5 | +2.5 | **+0.77** | [-4 ; 8] | — | — | 16 006 | 0.967 | **317** | 7.95 |
| `C2c_CARRY_WINSORIZED` | `DEAD` | 265485 | — | — | **305** | -71.0 | -99.0 | **-0.76** | [-159 ; 68] | — | — | 16 566 | 0.967 | **328** | 7.95 |
| `E1b_RISK_REGIME_SPELL_bask30` | `DEAD` | 2124 | — | — | **17** | +538.9 | +524.9 | **+0.75** | [-894 ; 1913] | 3/6 | 2024 → -1037 | 937 | 0.116 | **155** | 5.42 |
| `E1b_RISK_REGIME_SPELL_btc7` | `DEAD` | 2124 | — | — | **17** | +34.0 | +20.0 | **+0.31** | [-242 ; 346] | 3/6 | 2024 → -13 | 5 620 | 0.155 | **696** | 4.69 |
| `E1c_REGIME_SPELL_bask` | `DEAD` | 2124 | 1466 | 49 | **17** | -122.8 | -136.8 | **-0.10** | [-2198 ; 2067] | 4/6 | 2022 → -548 | 54 894 | 0.155 | **6 801** | 4.69 |
| `E1c_REGIME_SPELL_btc` | `DEAD` | 2124 | 1466 | 49 | **17** | -301.9 | -315.9 | **-0.39** | [-1742 ; 1082] | 3/6 | 2022 → -135 | 3 549 | 0.155 | **440** | 4.69 |
| `F1_X_MOM_7D` | `DEAD` | 316 | — | — | **73** | +19.1 | -8.9 | **+0.84** | [-58 ; 162] | — | — | 3 284 | 0.232 | **271** | 7.94 |
| `F2_X_LIQ_CASCADE_REPEAT` | `DEAD` | 1988 | — | — | **120** | -30.3 | -44.3 | **-0.65** | [-63 ; 33] | — | — | 8 959 | 0.425 | **404** | 7.53 |
| `A4_SIZE_COND_h168h` | `DATA_LIMITED` | 488 | — | — | **0** | — | — | **—** | — | — | — | — | — | **—** | — |
| `A4_SIZE_COND_h72h` | `DATA_LIMITED` | 488 | — | — | **0** | — | — | **—** | — | — | — | — | — | **—** | — |
| `D1_PRE_DRIFT_30d` | `DATA_LIMITED` | 14 | 14 | 9 | **9** | -343.5 | -371.5 | **-0.45** | [-1616 ; 952] | — | — | 1 410 | 0.000 | **∞** | — |
| `D1_PRE_DRIFT_7d` | `DATA_LIMITED` | 14 | 14 | 9 | **9** | -837.7 | -865.7 | **-1.09** | [-2252 ; 453] | — | — | 236 | 0.000 | **∞** | — |
| `D1_PRE_DRIFT_90d` | `DATA_LIMITED` | 14 | 14 | 9 | **9** | +240.6 | +212.6 | **+0.34** | [-1205 ; 1668] | — | — | 2 404 | 0.000 | **∞** | — |
| `D2_FUNDING_BASIS_DISLOCATION` | `DATA_LIMITED` | 13 | — | — | **13** | — | — | **-1.92** | — | — | — | — | — | **—** | — |
| `E1_RISK_REGIME_bask30` | `DATA_LIMITED` | 2094 | — | — | **9** | -1747.4 | -1761.4 | **-1.31** | [-4198 ; 701] | 3/6 | — | 165 | 0.000 | **∞** | — |
| `E1_RISK_REGIME_bask7` | `DATA_LIMITED` | 2117 | — | — | **4** | -1092.6 | -1106.6 | **-2.39** | [-1912 ; -374] | 4/6 | — | 22 | 0.000 | **∞** | — |
| `E1_RISK_REGIME_btc30` | `DATA_LIMITED` | 2094 | — | — | **9** | -625.8 | -639.8 | **-1.71** | [-1254 ; 89] | 4/6 | — | 97 | 0.000 | **∞** | — |
| `E1_RISK_REGIME_btc7` | `DATA_LIMITED` | 2117 | — | — | **4** | -499.1 | -513.1 | **-2.57** | [-800 ; -170] | 3/6 | — | 19 | 0.000 | **∞** | — |
| `E2_COND_MOM_7D` | `DATA_LIMITED` | 303 | — | — | **3** | +64.0 | +36.0 | **+1.12** | [-19 ; 252] | — | — | 75 | 0.000 | **∞** | — |
| `C1_VOL_MATURATION` | `DESCRIPTIVE` | 343229 | — | — | **—** | — | — | **—** | — | — | — | — | — | **—** | — |
| `C3_LIQUIDITY_MATURATION` | `DESCRIPTIVE` | 343229 | — | — | **—** | — | — | **—** | — | — | — | — | — | **—** | — |
| `S0_SURVIVORSHIP_AUDIT` | `DESCRIPTIVE` | — | — | — | **—** | — | — | **—** | — | — | — | — | — | **—** | — |

---

## 6. Résultats par axe

### 6.1 Axe A — l'effet de cotation

**A1.** La sous-performance relative post-listing existe, son signe est stable (3/4 ou 4/4 années
selon la cellule), mais elle n'atteint t = 2 que sur **une seule cellule sur douze**
(delay 4 h / horizon 168 h, t = 2,03, IC95 [+6 ; +898]) — soit ce qu'on attend du hasard sur une
grille de cette taille. Aux horizons longs (≈ 28 j) elle disparaît complètement (t entre −0,16 et
+0,45) : le fade est un phénomène de première semaine. Retirer 2023 (première année, la moins
arbitrée) — ou 2025 selon la cellule — réduit l'effet de 20 à 80 % : d4h/168h passe de +472 à
+335 bps (t 2,03 → 1,21), d24h/24h de +161 à +33 bps (t 1,70 → 0,25).

**A1b — le test de signe, et pourquoi il ne sauve pas l'axe.** Le fade réussit **69 à 72 % du
temps** (t déclusterisé jusqu'à **8,68** sur 143 vagues), et ce *taux de réussite* est confirmable
en **1,64 à 2,32 ans** — les seuls ETA de tout mon axe sous les 3 ans, et les seuls t observés
au-dessus du `t_req 3a`. Mais la distribution est violemment asymétrique à gauche :

| cellule | taux de réussite déclusterisé | médiane | **moyenne** | skew | pire événement | moyenne hors 5 pires |
|---|---|---|---|---|---|---|
| d24h / h168h | 72,1 % | +1028 bps | **+265 bps** | −2,97 | **−30 796 bps** | +506 bps |
| d4h / h168h | 70,9 % | +1264 bps | +327 bps | −2,75 | −31 771 bps | +575 bps |
| d1h / h168h | 69,6 % | +1292 bps | +280 bps | −2,79 | −31 079 bps | +565 bps |
| d24h / h72h | 65,3 % | +599 bps | +127 bps | −4,71 | −33 795 bps | +328 bps |

La quantité qui compose est la **moyenne**, et c'est elle qui est mesurée par A1/A5 : ETA 29 à
104 ans. Retirer les 5 pires événements double la moyenne — c'est la définition d'un payoff porté
par la queue. Verdict `UNCONFIRMABLE_IN_HORIZON`. Ce serait un candidat pour une structure à
**risque défini** (§9.5) ; il n'en existe pas sur des alts fraîchement cotés, et un stop
changerait le payoff donc constituerait un refit non testé. **Non promu.**

**A3 — le résultat qui tue l'axe.** Le funding cumulé des 30 premiers jours d'un perp vaut
**−310 bps** en moyenne sur les 518 cotations, contre **+61 bps** pour les contrats matures aux
mêmes semaines : différentiel **−321,5 bps/30 j, t = −5,74 sur 152 vagues**, IC95 [−438 ; −213].
C'est de loin l'estimation la plus significative de mon axe. Signe : funding **négatif** ⇒ les
**shorts paient les longs**. Détail microstructure (C1) : **51,8 %** des jours-symbole de moins de
30 j sont en funding 4 h contre **11,6 %** au-delà de 2 ans — Binance place les nouveaux perps sur
un régime de règlement accéléré, et le positionnement y est structurellement short.
Même ainsi, son ETA propre est de 3,99 ans : au-delà du seuil.

**A5 — la synthèse A1 × A3** (non préenregistrée, stampée `POST_HOC_COMBINATION`). Fade de prix
+ funding réellement réglé sur les deux jambes (short le nouveau, long le panier) :

| variante | prix seul | funding jambe short | funding jambe longue | **total** | t_L3 | ex_best_year |
|---|---|---|---|---|---|---|
| d24h / **168 h** (7 j) | +386,0 | −142,1 | −2,0 | **+230,9** | 1,09 | 2023 → +127,6 |
| d4h / 168 h | +472,5 | −183,5 | −1,9 | +277,3 | 1,17 | 2023 → +161,4 |
| d1h / 168 h | +422,9 | −195,8 | −1,9 | +217,1 | 0,86 | 2023 → +92,7 |
| d24h / **672 h** (28 j) | −148,5 | −232,9 | −5,5 | **−425,4** | −0,45 | — |

Le funding mange **37 à 46 %** du fade à 7 jours et **le retourne complètement à 28 jours**. Et
retirer 2023 divise encore par deux ce qui reste. **Le fade de listing n'est pas un trade.**

**A2** (conditionnement sur la réaction jour-0 : bras « pump > +20 % » moins bras « dump < 0 »)
et **A4/A4b** (taille de vague) : rien. L'appariement par semaine de A4 donne L3 = 0 par
construction — la taille de vague est constante à l'intérieur d'une semaine, donc les deux bras
ne peuvent jamais coexister dans une même unité L3 ; corrigé en bras disjoints (Welch) sous A4b.
A4b va d'ailleurs *dans le sens opposé* à l'hypothèse préenregistrée (les **grandes** vagues
fadent **moins** : −85 bps contre +337 bps, t = −1,53) ; non significatif, donc non retenu, mais
noté comme piste de signe inverse.

### 6.2 Axe B — l'âge comme facteur transversal : plat

C'était le seul candidat à ETA court de l'axe (rebalancement quotidien, 1 épisode indépendant par
semaine, 323 semaines disponibles). Il est **nul** :

- **B1_1D** : livre quintile vieux − quintile jeune, equal-weight, quotidien, univers éligible.
  **+1,4 bps/jour brut, IC95 [−11,3 ; +13,8], t = 0,21.** Bras vieux +8,2 bps, bras jeune
  +6,7 bps, univers +7,9 bps : les deux bras *sont* l'univers.
- **B1_7D** : −34,7 bps brut, t = −0,60, signe stable 4/7 années seulement.
- **B2 (Fama-MacBeth, contrôles log(qvol) et Amihud)** : coefficient d'âge +0,9 bps par sigma,
  t = 0,47 ; **coefficient univarié +0,2 bps, t = 0,09**. Il n'y a rien à résiduer : l'effet d'âge
  n'existe pas *avant même* les contrôles. La question préenregistrée « B1 est-il un repackaging
  d'Amihud (déjà validé, +105 bps) ? » est donc sans objet : il n'y a pas de B1 à expliquer.
- **B3** : la monotonie est absente — **rho de Spearman = 0,543** contre 0,80 exigé au préreg.
  Le profil par bucket est en **bosse**, pas monotone (rendement forward 1 j démeané
  transversalement) :

| bucket d'âge | `<30j` | `30-90j` | `90-180j` | `180-365j` | `1-2a` | `>2a` |
|---|---|---|---|---|---|---|
| rendement démeané (bps/j) | **−11,1** | −9,1 | +2,2 | **+5,7** | 0,0 | +1,1 |
| t (sur semaines ISO) | −0,95 | −1,43 | +0,55 | **+2,02** | 0,00 | +0,47 |
| n symboles | 308 | 310 | 309 | 297 | 255 | 186 |

Le seul bucket à t > 2 est `180-365j` — 1 sur 6, non corrigé pour tests multiples, et ce n'est
pas une extrémité, donc inexploitable comme facteur ordonné.

Le seul enseignement exploitable est **négatif et déjà en production** : les instruments de moins
de 30 jours sont les seuls à porter un signe franchement négatif (−11,1 bps/jour démeané), ce qui
corrobore le `ListingAgeGate` existant. **Aucun nouveau livre.**

### 6.3 Axe C — maturation de la microstructure

**C1 / C3 (descriptif)**, univers éligible ≥ 1 M$/j, médianes par bucket d'âge :

| bucket | vol réalisée 1 j (bps) | range H/L (bps) | quote-vol (M$) | nb trades | jours en funding 4 h | `\|basis_z_7d\|` |
|---|---|---|---|---|---|---|
| < 30 j | **531** | **1504** | **85,0** | 455 042 | **51,8 %** | 0,744 |
| 30-90 j | 409 | 1110 | 38,4 | 210 573 | 51,4 % | 0,776 |
| 90-180 j | 349 | 968 | 30,4 | 173 304 | 50,6 % | 0,789 |
| 180-365 j | 342 | 927 | 35,3 | 193 795 | 47,4 % | 0,788 |
| 1-2 a | 317 | 821 | 33,2 | 184 203 | 34,2 % | 0,794 |
| > 2 a | **244** | **640** | **23,1** | 148 905 | **11,6 %** | 0,801 |

La maturation est réelle et monotone sur la **volatilité** (×2,2 du plus jeune au plus vieux) et
sur le **régime de funding** (×4,5 de fréquence de règlement), mais **pas sur la liquidité** :
un perp de moins de 30 j fait 85 M$/jour de médiane, soit **3,7× plus** qu'un contrat de plus de
2 ans. Conséquence pour tout futur livre « jeunes » : la contrainte n'est pas la capacité, c'est
la volatilité — un livre âge devrait être vol-scalé, faute de quoi le bras jeune domine le risque.

**C2 — le carry d'âge n'est pas robuste.** Short-perp sur jeunes (< 90 j, funding > 0) moins
short-perp sur matures (≥ 1 an), horizon 7 j, funding inclus : **−202 bps, t = −2,31 en brut**,
mais **−43 bps, t = −0,76 après winsorisation transversale 1 %/99 %** (C2c). Tout le « résultat »
est dans les 2 % de queues. Et **C2b** montre que le différentiel de *funding seul* (sans le prix)
vaut +2,5 bps sur 7 j (t = 0,77) : sur le sous-échantillon funding > 0, le funding des jeunes
n'est **pas** plus riche. Le différentiel spectaculaire de A3 est un funding **négatif**
unilatéral des jeunes, pas une prime de carry harvestable.

### 6.4 Axe D — fin de vie : `DATA_LIMITED`, comme préenregistré

Le préenregistrement prédisait `UNCONFIRMABLE_IN_HORIZON` ou `DATA_LIMITED`. C'est `DATA_LIMITED`,
et pire que prévu : **14 radiations seulement** sont datables à l'intérieur de la fenêtre de
données (9 semaines ISO distinctes) et **zéro dans les 6 derniers mois** — donc `event_rate = 0`
et l'ETA est littéralement infini. Les points de mesure (D1 : −810 bps à 7 j, −316 bps à 30 j,
+269 bps à 90 j, \|t\| ≤ 1,09) sont du bruit ; les IC bootstrap font 2700 bps de large.

**D2** (comparaison intra-nom : 30 derniers jours vs reste de la vie du **même** symbole, donc non
polluée par la sélection) est le seul résultat informatif de l'axe D, et il est **négatif au sens
propre** :

| ratio fin-de-vie / reste-de-vie (médiane, n = 13) | `\|funding\|` | `\|basis_z_7d\|` | quote-volume | vol réalisée |
|---|---|---|---|---|
| ratio médian | **×1,00** | **×1,07** | ×0,35 | ×0,27 |

**Aucune dislocation.** Un contrat en fin de vie ne se disloque pas, il **s'éteint**.
L'hypothèse préenregistrée « les positions forcées de se déboucler créent une dislocation de
funding/basis » est fausse sur ce dataset. Ce qu'il faudrait pour trancher : les dates de
radiation **annoncées** (et non déduites de la dernière barre) plus les perps radiés avant 2023 —
une collecte d'annonces Binance, hors périmètre de ce round.

### 6.5 Axe E — vagues de cotation comme marqueur de régime : l'erreur de déclustering et sa correction

C'est ici que la session s'est interrompue, et c'est l'apport méthodologique du rattrapage.

**Étape 1 — ce qui a cassé.** Le préenregistrement fixait L3 = mois pour les mécanismes panel.
Pour un signal de **régime**, c'est incohérent : le régime est persistant (durée médiane d'une
plage : 21 jours), donc très peu de mois contiennent à la fois des jours HI et des jours LO.
L'appariement mensuel s'effondre à **L3 = 4** (E1 bask7/btc7) et **L3 = 3** (E2). Les t
« significatifs » de −2,39 et −2,57 qu'on lisait alors reposaient sur 4 paires. Reclassés
`DATA_LIMITED` par la règle L3 < 10 du §4.

**Étape 2 — première correction (E1b), insuffisante.** Déclustering par **plage** (run contigu de
même régime, ≥ 5 jours) : 17 plages HI, 22 plages LO. Mais la cible restait `fwd7`/`fwd30`,
c'est-à-dire des fenêtres forward qui **débordent de la plage** et chevauchent la suivante.
Résultat : BTC à 30 j donnait **+1172 bps, t = 2,01** — significatif, de signe **opposé** à
l'hypothèse préenregistrée, stable sur 2/6 années seulement, et dont `ex_best_year` inverse le
signe (+1172 → −29 en retirant 2024). Classé `REGIME_DEPENDENT`.

**Étape 3 — correction correcte (E1c).** Pour un signal persistant, la quantité harvestable est le
**rendement quotidien forward moyen pendant que le régime tient** : additif, contenu dans la
plage, sans chevauchement inter-plages. Exprimé en bps par 30 jours de détention continue :

| cible | régime HAUTE intensité | régime BASSE | **diff** | **t_L3** | L3 | ETA |
|---|---|---|---|---|---|---|
| panier equal-weight éligible | +135,2 | +244,0 | −108,8 | **−0,10** | 17 | 6801 ans |
| BTCUSDT | +341,7 | +629,7 | −287,9 | **−0,39** | 17 | 440 ans |

Le signe redevient celui de l'hypothèse (haute intensité de cotation ⇒ rendement plus faible),
mais l'effet est **indistinguable de zéro**. **La significativité de E1b venait entièrement du
débordement des fenêtres forward hors des plages de régime.** C'est le piège du déclustering sous
sa forme la moins visible : ce n'est pas le *nombre* d'épisodes qui était faux, c'est le fait que
la *cible* de chaque épisode couvrait les épisodes voisins.

**E2b** (momentum transversal 7 j conditionné au régime, même correction) : **−1197 bps/30 j,
t = −2,29** sur 17 plages, ETA 12,6 ans. Mais `ex_best_year` **inverse le signe** (−1197 → +76,9
en retirant 2020, année où le bras HI ne compte que 20 jours). Concentré sur une année ⇒
`REGIME_DEPENDENT`, et de toute façon ETA > 3 ans.

### 6.6 Axe F — interactions âge × alphas existants : rien

- **F1** (momentum 7 j sur la moitié jeune − sur la moitié vieille, âge médian PIT) : +47 bps
  brut, t = 0,84, meurt au stress 28 bps. `DEAD`.
- **F2** (cascade répétée 3e+ sur jeunes − sur vieux) : −16 bps, t = −0,65. **L'effet
  « repeat-cascade » acquis du projet n'est pas modulé par l'âge du contrat.** Limite honnête :
  le dataset cascade ne couvre que **49 symboles**, majoritairement matures, donc le bras
  « jeune » y est en réalité « moins vieux ». Un vrai test demanderait de recalculer les cascades
  sur les 312 symboles du panel ; cela ne changerait pas le verdict de non-promotion.
  **L'effet repeat lui-même n'a pas été re-testé** : il est acquis (briefing §4).

---

## 7. Ce que j'ai tué, et pourquoi

| tué | pourquoi, en une ligne |
|---|---|
| **Le fade de listing comme trade** (A1 / A5) | le funding réellement réglé sur un perp jeune (−310 bps/30 j) mange 40 % du fade à 7 j et le retourne à 28 j ; et le t ne dépasse 2 que sur 1 cellule de grille sur 12 |
| **Le fade de listing comme signal de signe** (A1b) | 69-72 % de réussite, confirmable en 1,6 an — mais skew −2,8 à −4,7 et pire événement −31 000 bps : la moyenne, seule quantité qui compose, n'est pas significative ; et le projet interdit le short directionnel standalone |
| **Le facteur âge transversal** (B1 / B2) | +1,4 bps/jour, IC95 [−11,3 ; +13,8], t = 0,21 ; le coefficient d'âge **univarié** vaut +0,2 bps (t = 0,09) : il n'y a rien avant même les contrôles de liquidité |
| **La monotonie du rendement en âge** (B3) | rho = 0,543 contre 0,80 préenregistré ; profil en bosse, le seul bucket significatif (`180-365j`) n'est pas une extrémité |
| **Le carry d'âge** (C2 / C2c) | t = −2,31 en brut mais **t = −0,76 après winsorisation 1/99** : le résultat est intégralement dans les queues |
| **La prime de funding des jeunes** (C2b) | à funding > 0, le différentiel jeune − mature vaut +2,5 bps/7 j (t = 0,77) : A3 est un funding *négatif* unilatéral, pas une prime harvestable |
| **La dislocation de fin de vie** (D2) | `\|funding\|` ×1,00 et `\|basis_z\|` ×1,07 dans les 30 derniers jours : un contrat mourant s'éteint (volume ×0,35, vol ×0,27), il ne se disloque pas |
| **Le drift pré-radiation** (D1) | 14 événements, 9 semaines, **0 dans les 6 derniers mois** ⇒ `event_rate = 0`, ETA infini par construction |
| **Le régime « intensité de cotation »** (E1 / E1b / E1c) | mesuré sans chevauchement inter-plages, t = −0,10 et −0,39 ; le t = 2,01 précédent était un artefact de fenêtres forward débordant des plages |
| **Le momentum conditionné au régime de vague** (E2 / E2b) | t = −2,29 mais le signe s'inverse en retirant 2020 ; 17 plages, ETA 12,6 ans |
| **L'interaction âge × cascade répétée** (F2) | t = −0,65 : l'effet repeat-cascade n'est pas modulé par l'âge du contrat |
| **Le conditionnement jour-0** (A2) et **la taille de vague** (A4 / A4b) | \|t\| ≤ 0,52 et ≤ 1,53 ; A4b va même dans le sens opposé à l'hypothèse (les grandes vagues fadent *moins*) |
| **La règle de vague « gap ≥ 7 j »** (méthodologique) | dégénérée : 128 cotations dans une seule vague ; repli semaine ISO prévu au préreg §5c, et il donne un ETA *pire*, pas meilleur |

---

## 8. Ce qui a changé après l'interruption, et son statut anti-refit

| élément | statut | effet sur le résultat |
|---|---|---|
| `E1c` / `E2b` — L3 = plage de régime, cible = fwd 1 j moyen dans la plage | `METHOD_AMENDED` (le préreg §5c prévoyait le repli d'unité L3 en cas de dégénérescence) | **dégrade** : t 2,01 → −0,39 |
| `A5` — fade net du funding des deux jambes | `POST_HOC_COMBINATION` (combine A1 et A3, tous deux préenregistrés ; aucun seuil modifié) | **dégrade** : +386 → +231 bps à 7 j ; −148 → −425 bps à 28 j |
| Règle « L3 < 10 ⇒ `DATA_LIMITED` » | règle post-hoc **déclarée**, uniforme, ne pouvant que dégrader | **dégrade** : retire 5 cellules dont deux à \|t\| > 2 |
| Repli « vague = semaine ISO » | prévu au préreg §5c, **avant** tout résultat | ETA *pire* que la règle originale |
| Repli « WEAK vs DEAD » sur \|t\| seul (et non sur \|net\|) | correction de cohérence : un net négatif *à cause des coûts* ne doit pas faire passer un t nul pour WEAK | **dégrade** : 11 mécanismes WEAK → DEAD |
| `evidence/build_funding_daily.py` | script de reproductibilité écrit après coup (§10) | aucun : formule vérifiée identique à l'artefact d'origine |

Aucun seuil du §3 du préenregistrement n'a été modifié. Aucune grille (delays, horizons, buckets,
quintiles) n'a été étendue après coup. **Tous** les ajustements post-interruption vont dans le
sens de la dégradation des résultats.

---

## 9. Limites et ce qu'il faudrait pour aller plus loin

1. **Perps radiés avant 2023** : irrécupérables sans une collecte d'annonces Binance historiques.
   Tant qu'ils manquent, l'axe D restera `DATA_LIMITED` quel que soit le raffinement statistique.
2. **Dates de radiation annoncées** (vs déduites de la dernière barre du panel) : sans elles,
   l'événement D1 est daté au jour près par défaut, ce qui étale et dilue l'effet.
3. **`taker_buy_quote_asset_volume`** est agrégé dans le panel mais n'a été utilisé par **aucun**
   mécanisme retenu — le piège des placeholders (`data_pitfalls_enriched_vision.md`) est donc
   sans effet sur ce rapport.
4. **F2** mériterait un recalcul des cascades sur les 312 symboles du panel plutôt que sur les 49
   du dataset enrichi. Cela ne changerait pas le verdict (t = −0,65 est loin du seuil).
5. **A1b** est la seule voie qui pourrait ressusciter l'axe A : le taux de réussite est
   confirmable en 1,6 an, c'est la queue gauche qui tue la moyenne. Il faudrait une structure à
   **risque défini** — options sur alts fraîchement cotés (n'existent pas) ou stop-loss (change le
   payoff, donc refit non testé, et demanderait les klines 5 m qui sont disponibles mais non
   exploitées ici). **Je ne le promeus pas.**
6. **Politique SHORT du projet respectée** : tout ce qui précède est « short-shaped » et n'est
   proposé ni comme short directionnel standalone ni comme candidat. La seule forme livrable
   aurait été un SCREEN/GATE — et il existe déjà (`ListingAgeGate`), que mes mesures corroborent
   sans le modifier.

---

## 10. Reproductibilité

Scratch de travail : `$W3_SCRATCH` (42 Mo, hors dépôt, jamais > 250 Mo — contrainte §8 du
briefing respectée). Aucune écriture hors du dossier de worker et du scratch ; aucune suppression.

```bash
export W3_SCRATCH=/chemin/vers/scratch/w3 && mkdir -p $W3_SCRATCH
V=/home/qbee/futur/.venv/bin/python
E=/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w3_listings_lifecycle/evidence
$V $E/build_panel.py                # daily_panel.parquet (37 Mo) + life.parquet
$V $E/build_funding_daily.py        # funding_daily.parquet — funding REGLE, agrege au jour
$V $E/build_benchmark.py            # bench_hourly.parquet — panier equal-weight eligible, horaire
$V $E/run_axis_A_listing_event.py   # A1, A2, A4        -> axisA_results.json (+ axisA_events.parquet)
$V $E/run_axis_B_age_factor.py      # B1, B2, B3        -> axisB_results.json
$V $E/run_axis_CD.py                # C1,C2,C2b,C3,A3,D1,D2 -> axisCD_results.json
$V $E/run_axis_EF.py                # E1, E2, F1, F2    -> axisEF_results.json
$V $E/run_fixups.py                 # E1b, A1b, A4b, C2c -> fixups_results.json
$V $E/run_final.py                  # S0, A5, E1c, E2b  -> final_results.json
$V $E/consolidate.py                # verdicts deterministes -> ../RESULTS.json
$V $E/make_table.py                 # table markdown du §5 depuis RESULTS.json
```

`evidence/gate.py` contient la machinerie commune (déclustering 3 niveaux, block-bootstrap 5000,
`n_required` avec haircut 50 %, ETA). `evidence/consolidate.py` **calcule** tous les verdicts à
partir des seuils préenregistrés : aucun verdict de ce rapport n'a été écrit à la main, et
`evidence/make_table.py` régénère la table du §5 sans saisie manuelle.

**Note de reproductibilité.** `build_funding_daily.py` a été écrit *après coup* : le builder
original n'avait pas été sauvegardé avant l'interruption de session. Sa formule
(`sum(funding_rate) FILTER (funding_is_settlement)`) a été vérifiée identique à l'artefact
d'origine sur BTCUSDT / SOLUSDT / 1000FLOKIUSDT — `max |Δ funding_paid_d| = 0,0` et `n_settle_d`
identique sur les 2404 / 2147 / 1183 jours respectifs. Seul `abs_funding_avg_d` diffère
marginalement sur SOLUSDT (max 1,7e-3) ; ce champ ne sert que dans la table descriptive C1 et
dans D2, tous deux `DESCRIPTIVE` / `DATA_LIMITED`.
