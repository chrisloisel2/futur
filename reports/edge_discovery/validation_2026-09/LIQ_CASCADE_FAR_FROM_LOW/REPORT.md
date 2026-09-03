# LIQ_CASCADE_FAR_FROM_LOW — Rapport de validation indépendante

**Validateur :** worker V2, Alpha Validation Factory wave 2, 2026-09-03
**Réclamation :** round 2 W2 (`alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md`, rang 4) —
« far from local low » bat « at the low » sur les cascades LONG, `fwd_4h`, **+15,5 → +73,3 bps OOS**.
**Alpha live concerné :** `LIQ_CASCADE_FAR_FROM_LOW_V1`, en `SIGNAL_SHADOW`, `scientific_status:
RECONSTRUCTED` — seuil `dist_low_24h ≥ 0,05` **reconstruit** (75e centile mesuré au moment du
freeze), le script d'origine étant perdu. C'est précisément ce qu'on vient tester.
**Scripts :** `../_lib/exp_v2_cascade.py`, `exp_v2_ffl_compare.py`, `exp_v2_ffl_gap_sensitivity.py`,
`exp_event_weighted_cluster.py`.

---

## 1. Ce qui a été trouvé, en une phrase

La réimplémentation **reproduit exactement** le chiffre publié sous la convention de comptage de
la découverte, mais **cette significativité disparaît dès qu'on corrige la corrélation entre
jambes d'une même cascade market-wide** — et le signe de l'effet s'inverse selon l'unité de
pondération retenue. Le split far/near n'est donc **pas établi**, dans un sens ni dans l'autre.

## 2. Reproduction de la réclamation — l'implémentation est d'accord

Au seuil EXACT de la spec live (`dist_low_24h ≥ 0,05`), niveau événement, coût 14 bps :

| Source | net14 plein | n | net14 OOS 2025+ | n OOS |
|---|---|---|---|---|
| `freeze_spec.json` (reproduction au freeze) | +6,7 | 6 804 | +19,84 | 2 409 |
| **Cette validation (indépendante)** | **+6,84** | 6 731 | **+20,21** | 2 395 |

L'écart est du bruit de fenêtre de données. **Il n'y a pas de désaccord d'implémentation** :
la divergence de verdict porte uniquement sur l'inférence statistique.

## 3. Le problème : une SE sous-estimée d'un facteur ~2

Une cascade de liquidation est un événement **market-wide** — des dizaines d'alts cascadent dans
les mêmes minutes. Compter chaque jambe comme une observation indépendante gonfle le t.
Statistique de référence (moyenne pondérée **par événement** = l'estimateur du P&L réel par trade,
avec erreur-type **cluster-robuste** sur les épisodes L3) :

| Bras | n | N_L3 | net14 | t naïf | **t cluster-robuste** | inflation SE | boot p05 |
|---|---|---|---|---|---|---|---|
| `far` (seuil live 0,05) | 6 650 | 1 637 | +6,86 | 1,68 | **0,90** | ×1,87 | −5,73 |
| `near` | 20 100 | 2 529 | −5,91 | −2,98 | −1,13 | ×2,63 | −14,74 |
| référence inconditionnelle | 26 750 | 2 926 | −2,74 | −1,52 | −0,66 | ×2,30 | −9,68 |
| **`far − near`** | — | — | **+12,76** | — | **1,30** | — | — |
| **`far − baseline`** | — | — | **+9,59** | — | **1,30** | — | — |

Le contraste va bien dans le sens réclamé (far > near), mais **t = 1,30 < 1,645** : le split n'est
pas significatif une fois la corrélation intra-cascade prise en compte.

## 4. Le signe dépend de l'unité de pondération

Moyenne par **épisode** (chaque cascade market-wide compte pour UNE observation), en balayant le
gap de chaînage — c'est une sensibilité sur l'unité d'inférence, aucun paramètre du signal ne bouge :

| gap d'épisode | far net14 | t | near net14 | t | référence | **far − référence** |
|---|---|---|---|---|---|---|
| 30 min | +0,67 | 0,14 | −4,71 | −1,98 | −1,44 | **+2,11** |
| 1 h | +2,57 | 0,53 | +3,27 | 1,33 | +6,39 | **−3,82** |
| 2 h | +2,69 | 0,51 | +14,77 | 5,78 | +15,42 | **−12,73** |
| **4 h (préenregistré)** | **−7,54** | −1,30 | **+27,58** | 9,83 | +20,15 | **−27,69** |
| 6 h | −23,88 | −3,99 | +28,10 | 8,83 | +13,63 | −37,51 |
| 12 h | −30,89 | −4,45 | +22,67 | 4,58 | +2,34 | −33,23 |
| 24 h | −32,51 | −3,79 | +5,21 | 0,49 | −18,57 | −13,94 |
| *niveau événement* | *+6,86* | *1,68* | *−5,91* | *−2,98* | *−2,74* | *+9,59* |

Mécaniquement : le bras `far` compte 3,4 événements par épisode contre 7,0 pour `near`. Le
rendement positif de `far` en pondération événement provient d'une minorité de gros épisodes
multi-noms ; dès qu'on donne le même poids à chaque épisode, il s'annule puis devient négatif.

**`far − référence` est négatif à tous les gaps ≥ 1 h.** Autrement dit, sous toute forme de
déclustering cross-symbole, conditionner sur « loin du plus bas » **ne fait pas mieux** que fader
les cascades sans condition — et fait moins bien.

## 5. Gate préenregistré (moyennes d'épisode, gap 4 h)

| Spec | net14 | net28 | t_L3 | N_L3 | N_raw | boot p05 | années + |
|---|---|---|---|---|---|---|---|
| PRIMARY `far` (q75 causal, ma règle) | −6,76 | −20,76 | −1,192 | 1 620 | 6 664 | −16,24 | 2/5 |
| Bras B `near` | +22,98 | +8,98 | 8,251 | 2 482 | 19 886 | +18,48 | 5/5 |
| T2 `far − near` | **−29,74** | — | Welch −4,71 | — | — | P(diff≤0)=1,00 | — |
| P1 seuil live 0,05 | −11,56 | −25,56 | −2,044 | 1 637 | 6 650 | −20,82 | 1/5 |
| P2 `dist_low_7d ≥ q75` | −1,55 | −15,55 | −0,261 | 1 524 | 6 688 | −11,24 | 2/5 |
| P3 hors meilleure année | −11,30 | −25,30 | −1,727 | 1 264 | 5 103 | −22,03 | 1/4 |

Année par année du bras `far` (niveau événement, seuil live) : 2022 **−19,1** · 2023 **+12,6** ·
2024 **−1,9** · 2025 **+20,5** · 2026 **+19,8** → **3/5 années positives**, pas 5/6.
Chevauchement avec le ledger `LIQ_CASCADE_REPEAT_V1` : 1 924 / 6 664 = **28,9 %**.

## 6. Fréquence, N_required, ETA

Taux d'épisodes L3 : historique 6,06/semaine, récent 7,00/semaine, conservateur 6,06/semaine —
**c'est un mécanisme à haute fréquence**, donc l'ETA n'est pas le problème ici. Mais `N_required`
est **non défini** : l'effet de la spec primaire est négatif, il n'y a pas d'edge positif à
dimensionner. `minimum_calendar_days` = 60. `confirmable_in_horizon` = **False** (faute d'effet
à confirmer, pas faute de fréquence).

## 7. Verdict

**`REJECTED`**, tag secondaire **`EVIDENCE_ARTEFACT`** (la significativité publiée provient du
comptage de jambes corrélées comme indépendantes).

| Critère | Résultat |
|---|---|
| net14 > 0 avec t_L3 ≥ 1,645 | **ÉCHOUÉ** — −6,76 / t −1,19 (épisode) ; +6,86 / t 0,90 (événement) |
| `far − near` > 0 significatif | **ÉCHOUÉ** — +12,76 / t 1,30 (événement) ; −29,74 (épisode) |
| net28 > 0 | **ÉCHOUÉ** dans les deux conventions |
| ≥ 4/5 années positives | **ÉCHOUÉ** — 3/5 au mieux |

`sign_correction_required` : **non tranché** — et c'est le résultat, pas une échappatoire. Le
signe de l'effet dépend de l'unité de pondération (positif par événement, négatif par épisode),
donc l'affirmation « far bat near » **n'est établie dans aucune direction**. Prétendre l'inverse
serait commettre la faute symétrique de celle de la découverte.

**Ce qui reste vrai et exploitable :** le fade **inconditionnel** des cascades LONG est solide au
niveau épisode (+20,15 net14 au gap 4 h, et le bras `near` à +27,58 / t 9,83). L'edge de la
famille cascade existe ; c'est le **conditionnement far/near** qui n'apporte rien.

**`recommended_next_step` : `DOWNGRADE_LIVE_STATUS`.**
`LIQ_CASCADE_FAR_FROM_LOW_V1` tourne en shadow sur une spec dont la preuve ne tient pas. À faire :
1. passer `scientific_status` de `RECONSTRUCTED` à `INVALIDATED_PENDING_RESPEC` — sans couper la
   collecte forward, qui reste informative ;
2. ne PAS le remplacer par le bras `near` : ce serait choisir la direction après avoir vu le
   résultat, exactement la faute qu'on reproche à la découverte. Un `near` doit être
   préenregistré et validé comme un candidat neuf ;
3. la question ouverte utile est **le fade inconditionnel de cascade** comme alpha à part
   entière, avec un déclustering correct dès le départ.

## 8. Note de méthode pour les prochaines vagues

Ce candidat est le cas d'école de la leçon wave 1 (3 rejets sur 4 venaient d'un déclustering
cross-symbole absent). La nouveauté ici est qu'il ne suffit pas de décluster pour l'inférence :
**la pondération elle-même change le signe**. La statistique à publier par défaut est donc
« moyenne pondérée par événement + SE cluster-robuste » — elle garde l'estimateur de P&L
qu'un trader reconnaît, tout en donnant une significativité honnête, et elle rend le désaccord
de convention visible au lieu de le cacher.
