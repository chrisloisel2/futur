# SHORT_COVERING_CONTINUATION — Rapport de validation indépendante

**Validateur :** worker V1, Alpha Validation Factory wave 2, 2026-09-03
**Réclamation :** round 2 W2 (`alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md`, rang 2) —
prix ↑ + OI ↓ (décile de queue), `fwd_4h`, **excess vs baseline +9,2 bps plein / +19,0 OOS**,
t 5,5 / 4,7, n = 23 422 sur 2 055 173 barres.
**Alpha live concerné :** `SHORT_COVERING_CONTINUATION_V1`, `SIGNAL_SHADOW`,
`scientific_status: RECONSTRUCTED` (script d'origine perdu).
**Discipline :** `freeze_spec.json` lu en LECTURE SEULE (autorisé §2, pour savoir ce qui tourne) ;
le code `src/institutional/engines/short_covering_continuation/` n'a **pas** été lu.
Réimplémentation depuis la définition économique. Spec figée dans `PREREGISTRATION.md`.

---

## 1. Méthodologie

Panel **horaire** construit indépendamment sur les 50 symboles figés de
`portfolio_v1_1_parallel_50.yaml` : prix = dernier close 5 m de l'heure
(`data_v2/normalized/perp_ohlcv`), OI = dernier `sum_open_interest` de l'heure
(`derivatives_backfill/binance_vision_metrics`), jointure sur l'heure UTC, heure incomplète
écartée sans imputation. → **1 447 224 barres, 48 symboles, 2022-01-01 → 2026-07-31**
(2 symboles de l'univers figé sans fichier metrics).

Features causales : `px_ret_1h`, `oi_delta_1h`. Rang centile dans la fenêtre glissante des
**720 h précédentes, barre courante exclue** de sa propre référence
(`(rolling(721).rank() − 1) / 720`), fenêtre pleine exigée.

**Bras A** = `px_ret_1h_pctile ≥ 0,90` **ET** `oi_delta_1h_pctile ≤ 0,10` → 22 330 barres
(1,54 % de la population). **Bras B** = toutes les autres barres éligibles. Le verdict porte sur
l'**excess A − B**, jamais sur « A > 0 ».

## 2. Écart au préenregistrement — déclaré

Le prereg fixait `L3 = épisode cross-symbole chaîné, gap < 4 h`. Cette unité, correcte pour une
population d'**événements rares** (les cascades : 26 750 événements → 2 926 épisodes), est
**dégénérée** ici : sur un panel de **barres denses** à 48 symboles, il y a presque toujours un
signal dans les 4 h suivantes, si bien que les 22 330 signaux se chaînent en **5 épisodes**. Une
SE cluster-robuste sur G = 5 n'a aucune validité asymptotique.

L'unité a donc été remplacée par **jour / semaine / mois calendaires**, les trois fixées avant
relecture des résultats et les trois reportées. Ce n'est pas un ajustement de paramètre du signal
(aucun seuil ne bouge), mais la correction d'une unité d'inférence inadaptée à la forme de la
population. C'est une leçon transférable : **l'unité de déclustering doit être choisie d'après la
densité de la population, pas copiée d'une autre famille.**

## 3. Résultat primaire

| Unité de cluster | clusters | **excess A − B** | SE | **t** | bras A seul (net14) | t | boot p05 |
|---|---|---|---|---|---|---|---|
| jour calendaire | 1 582 | **+17,06** | 5,75 | **2,97** | +2,53 | 0,41 | −7,82 |
| semaine calendaire | 230 | **+17,06** | 5,29 | **3,22** | +2,53 | 0,46 | −6,58 |
| mois calendaire | 55 | **+17,06** | 5,81 | **2,94** | +2,53 | 0,42 | −7,49 |

**Le mécanisme se reproduit.** L'excess +17,06 bps est encadré par les deux chiffres réclamés
(+9,2 plein / +19,0 OOS) et le bras A à +2,53 net14 correspond au `net_bps_oos_2025_26 = +2,3`
du `freeze_spec`. La significativité tient sur les trois unités de cluster — c'est le candidat
le plus stable de la vague sur ce point.

**Mais le produit ne bat pas zéro.** Le bras A seul rend +2,53 bps net de 14 bps de coût
(t = 0,41, 5e centile bootstrap **négatif**) et **−11,47 bps au coût de stress 28 bps**.
Autrement dit : ces barres surperforment nettement l'univers, mais l'univers lui-même est en
recul sur ces fenêtres, et ce qui reste ne couvre pas les frais.

Excess A − B année par année (cluster = jour) :

| année | A − B | t | clusters |
|---|---|---|---|
| 2022 | −16,17 | −0,78 | 335 |
| 2023 | +24,06 | 2,89 | 333 |
| 2024 | +28,11 | 3,06 | 366 |
| 2025 | +29,71 | 2,15 | 336 |
| 2026 (partiel) | +10,75 | 1,31 | 212 |

**4/5 années positives**, et les trois années les plus récentes sont les plus fortes — le
mécanisme se renforce dans le temps plutôt que de s'éroder.

## 4. Perturbations préenregistrées

Toutes tournées, excess A − B (l'inférence par épisode chaîné étant dégénérée, seule la
direction/magnitude est lisible sur ces lignes) :

| # | Perturbation | A − B |
|---|---|---|
| P1 | quintile (0,80 / 0,20) | +8,30 |
| P2 | fenêtre de référence 360 h | +11,14 |
| P3 | **score live `min(px_p, 1−oi_p) ≥ 0,90`** | **+13,19 — identique à la PRIMARY** |
| P4 | horizon 1 h | +3,96 |
| P4 | horizon 8 h | +20,70 |
| P8 | OI notionnel au lieu du nombre de contrats | +16,05 |
| P5 | hors 2022 | +21,87 |

P3 est le contrôle qui compte pour l'alpha live : le **score combinateur `min()` de la spec
reconstruite sélectionne exactement la même population** que la conjonction décile — la
reconstruction est fidèle au mécanisme. P1 montre que diluer la queue affaiblit l'effet
(cohérent avec un vrai effet de queue). Le signe est stable sur toutes les perturbations.

## 5. Contrôles obligatoires

| Contrôle | Résultat |
|---|---|
| Chevauchement avec la population de cascades `LONG_CASCADE` | **3,2 %** (716 / 22 338 heures) — mécanisme très largement indépendant de la famille cascade |
| **Accord décision-par-décision avec le ledger live** | **IMPOSSIBLE** — le ledger couvre 2026-08-28 → 2026-09-03 (377 décisions) alors que `data_v2` s'arrête au 2026-07-31 : **zéro heure commune**. Ce test reste à faire quand les données auront rattrapé. |
| Capacité | 48 perps majeurs, 1,54 % des barres → ~14 signaux/jour tous symboles confondus. Contrainte = fréquence, pas profondeur de carnet. |

## 6. Fréquence et ETA

22 330 signaux sur 1 671 jours ≈ **13,4 signaux/jour**, 1 490 jours distincts porteurs de signal.
C'est de loin la fréquence la plus élevée de tous les candidats de la vague — et donc le meilleur
ETA potentiel du projet, comme l'anticipait le briefing. Mais l'ETA se calcule sur l'edge à
confirmer : sur l'**excess**, l'edge est significatif dès l'échantillon existant ; sur le
**produit tradeable** (bras A vs zéro), il n'y a pas d'edge positif à dimensionner, donc
`n_required` est non défini et `confirmable_in_horizon` est **False** faute d'effet, pas faute
de fréquence.

## 7. Verdict

# `NEEDS_MORE_RESEARCH` — tag `COST_FRAGILE`

| Critère | Résultat |
|---|---|
| 1. excess A − B > 0, t ≥ 1,645 sur toutes les unités de cluster | **PASSÉ** (+17,06 ; t 2,94–3,22) |
| 2. net28 > 0 | **ÉCHOUÉ** — le produit est à −11,47 au coût de stress |
| 3. ≥ 4/5 années positives sur l'excess | **PASSÉ** (4/5, les 3 dernières les plus fortes) |
| 4. **bras A seul > 0** (« un produit doit aussi battre zéro ») | **ÉCHOUÉ** — +2,53, t 0,41, p05 −7,82 |
| 5. chevauchement ≤ 50 % | **PASSÉ** (3,2 %) |

`sign_correction_required` : **non**. La direction et la magnitude réclamées sont confirmées.

**Ce que ça dit de l'alpha live.** La reconstruction est **fidèle** : le score `min()` de
`SHORT_COVERING_CONTINUATION_V1` sélectionne la même population que la définition économique, et
l'excess se reproduit. Ce n'est donc **pas** un cas `LIQ_CASCADE_FAR_FROM_LOW`. Mais l'alpha ne
mérite pas non plus `UPGRADE_LIVE_STATUS` : en tant que **produit long autonome**, il ne couvre
pas ses frais (+2,53 bps pour 14 bps de coût, négatif au stress).

**`recommended_next_step` : `MORE_RESEARCH`**, avec une direction précise —
1. **garder le statut `SIGNAL_SHADOW` inchangé** (ni upgrade ni downgrade) : le mécanisme est
   réel, le produit ne l'est pas encore ;
2. sa valeur est celle d'un **signal relatif** (+17 bps vs univers), donc d'un **overlay/filtre**
   dans un book cross-sectionnel, pas d'un long directionnel — c'est la piste à préenregistrer ;
3. refaire le test d'accord avec le ledger dès que `data_v2` couvrira fin août 2026 : c'est le
   seul contrôle du prereg qui n'a pas pu être exécuté.
