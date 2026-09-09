# Boucle de recherche — digest

*régénéré à l'itération 10 · 2026-09-09*

## Budget

**2 tests disponibles.** Solde brut +2 (crédité 19, consommé 17 — le 17ᵉ est `CME_SEGMENTATION_V1`,
enregistré au moment du regard, ledger seq 6).
Le backfill du 2026-09-09 a ajouté **62 jours** — `floor(62/400) = 0`, **aucun crédit**.

## Seuils

| | valeur | quand |
|---|---|---|
| **barre opérante** | **2,9552** | bilatéral à n=16 : les 16 essais déjà consommés l'ont été par un harnais qui re-choisissait le côté (I13) |
| prochain essai | **2,7543** | unilatéral à n=17, la direction étant désormais imposée |

Dérivés, jamais saisis (`preregistration.py::threshold_t`).

## Défauts d'instrument

**Ouverts (3)**

| id | défaut | bloque |
|---|---|---|
| I3 | plafond de capacité adossé à l'ADV, pas à la profondeur | famille illiquidité |
| I7 | le coût de 14 bps est une hypothèse, pas une mesure | tout signal à fort churn |
| I14 | `n_indep` ignore le lissage | le crible 3 sur les configs lissées |

**Fermés (15)** — I1, I2, I4, I5 *(v4, vérifiés)* · I6, I8, I9 *(it. 1)* · I11 *(it. 2)* ·
I10 *(it. 3)* · I12 *(it. 5)* · I13 *(it. 6)* · I16, I17 *(it. 7)* · I15, I18, I19 *(it. 8)*

Les quatre qui produisaient des **chiffres faux**, pas du désordre :

- **I6** — deux mécanismes qui n'en étaient qu'un (ρ 1,0000 → −0,0745)
- **I13** — le côté re-choisi sur les données scellées ; 2 lignes du confirm v4
  contredisaient leur propre pré-enregistrement ; barre trop basse de 0,22
- **I15** — le Sharpe était un CAGR composé (3,07 → **2,43** en échantillon,
  1,95 → **1,66** hors échantillon) ; toutes les dates de confirmabilité étaient optimistes
- **I19** — un trou **universel** faisait sauter l'index et recollait les bords :
  un rendement de 31 jours porté comme un rendement d'un jour

## Données

**Frontière** — klines `2026-08-31` *(+62 j, trou de juillet comblé)* · métriques `2026-09-06`
· spot `2026-08-31`. Goulot : l'archive mensuelle de septembre n'est pas publiée.

**Continuité** — 52/787 klines et 9/409 spot ont un trou interne (FTTUSDT 310 j,
CVCUSDT 153 j, LUNAUSDT 17 j). Ce sont de **vraies suspensions de cotation**,
inoffensives tant qu'elles restent propres à un symbole. Vérifié sur LUNA :
`ret1 = NaN` sur tout le trou.

**Jamais entré dans le panel** — `options_backfill` (586 Mo) ·
`microstructure_reduced` (7,1 Go) · `execution_probe` (243 Mo) · bybit / okx /
hyperliquid · `stablecoins`. **C'est le seul gisement d'hypothèses vérifiablement
extérieures aux 138 regards** : le regard y était physiquement impossible.

## Préenregistrements scellés (branches orphelines, fichier unique, poussées seules)

| id | branche | commit | statut |
|---|---|---|---|
| `SOURCE_SELECTION_RULE` | `prereg/source-selection-rule` | `e765081` | appliquée : Bitfinex refusée (cond. 3), CME testée |
| `FORWARD_CROWD_POSITIONING_V1` | `prereg/forward-crowd-positioning-v1` | `0de75a9` | **en attente** — un regard, pas avant 2028-12-06 (819 j) |
| `CME_SEGMENTATION_V1` | `prereg/cme-segmentation-v1` | `0ddeee4` | **échec** `t_net` 1,577 < 1,960 — fermée |

Ledger des regards : `reports/loop/LOOK_LEDGER.jsonl`, chaîne hachée, 6 entrées, valide.

## Hypothèses, finalistes, sleeves

- hypothèses écrites : **6** — `H-BASIS-1..4` (famille morte), `FORWARD_CROWD_POSITIONING_V1` (scellée, en attente), `CME_SEGMENTATION_V1` (scellée, testée, échec)
- finalistes en attente : **0**
- **sleeves validées : 0**

## Le candidat, tranché

`lsr_globacct_x | mkt | h3 k8 hd1 sm0 DIR` — **il n'y a jamais eu trois `t`.**
Deux configurations lues sur la fenêtre scellée, chacune deux fois (v4, puis v5
sous l'alias `pos_crowd_vs_univ`), relectures **identiques à la 6ᵉ décimale**.
Le premier `t` vaut **2,399433** et c'est un `t`, pas un maximum d'ordre.

Ce que la lecture a révélé d'autre :
- **aucun ledger** n'avait enregistré ces regards — zéro mention dans
  `MULTIPLICITY_LEDGER` et `PREREGISTRATION_LEDGER`
- `prereg.json` précède `confirm` de **dix secondes**, même pipeline : un
  artefact, pas une promesse

**Indécidable** — par la multiplicité du balayage, pas par des relectures.
Échoue à 2,9552 et même à 2,7729 (famille v4 seule).

## Contrôles

- **contrôle positif** — pente 1,026, r² 0,9775, du 2026-09-06. Invariant satisfait.
- **placebo** — 20 tirages, médiane **2,8069** → N implicite bilatéral **138**,
  exactement le compte de dédoublonnage. Le q95 (3,8783) est le 19ᵉ ordre de 20 :
  couverture réelle [0,784 ; 0,982], **il ne porte rien**.
- **cribles 5/6/7** — implémentés (`tools/cribles.py`). Sur `lsr_globacct_x` :
  les trois passent, tranche haute à **286,6 épisodes effectifs**.
- **continuité** — `tools/check_continuity.py`, code de sortie 1 si trou interne.

## Prochaine itération

**L'attente a commencé.** Les deux sources de la règle sont examinées, aucune ne passe, aucune
exception ne sera fabriquée. Le calendrier est celui du forward : `FORWARD_CROWD_POSITIONING_V1`,
un regard, pas avant le 2028-12-06. Pendant l'attente, les branches gratuites : **I14** en
premier (tant que `n_indep` ignore le lissage, toute date de confirmabilité est optimiste),
puis instrument, ingestion, hypothèses — aucune ne consomme de regard.