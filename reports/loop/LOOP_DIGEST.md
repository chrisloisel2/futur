# Boucle de recherche — digest

*régénéré à l'itération 5 · 2026-09-09*

## Budget

**3 tests disponibles.** Solde brut **+3** (crédité 19, consommé 16).

| source | jours | crédit |
|---|---|---|
| `um_klines_1d` | 2 330 | +5 *(plafond)* |
| `binance_vision_metrics` | 1 673 | +4 |
| `funding_mark` | 2 330 | +5 *(plafond)* |
| **`spot_klines_1d_vision`** *(itération 2)* | 2 330 | **+5** *(plafond)* |
| `sweep_v4` confirm | — | −9 |
| `sweep_v5` confirm | — | −7 |

La boucle peut tester — mais seulement un finaliste ayant passé les huit
cribles, et il n'y en a **aucun**.

## Seuil courant

**`threshold_t(16) = 2,7344`** — dérivé, jamais saisi
(`preregistration.py::threshold_t`, Bonferroni unilatéral à α = 0,05).

## Défauts d'instrument

**Ouverts (2)**

| id | défaut | bloque |
|---|---|---|
| I3 | plafond de capacité adossé à l'ADV, pas à la profondeur | famille illiquidité |
| I7 | le coût de 14 bps est une hypothèse, pas une mesure | tout signal à fort churn |

**Fermés (9)**

| id | défaut | itération |
|---|---|---|
| I1 | `effective_tests` échantillonnait l'ordre d'insertion | v4 *(vérifié)* |
| I2 | l'open interest ne chargeait pas | v4 *(vérifié : 301 215 pts)* |
| I4 | `roll_spread` contredisait `amihud` | v4 *(vérifié)* |
| I5 | stabilité des lignes INVERSE inconnue | v4 *(vérifié)* |
| I6 | `squeeze_setup` et `long_capit` étaient le même signal | 1 |
| I8 | 24 % de la bibliothèque étaient des doublons de rang | 1 |
| I9 | `taker_imb_z{n}` utilisait une fenêtre `n*3` | 1 |
| I10 | le dédoublonnage de I8 était incomplet | 3 |
| I11 | l'unité des horodatages Vision déduite une fois par lot | 2 |
| I12 | les signaux nommés `basis_*` ne mesuraient pas le basis | 5 |

**Bibliothèque de signaux : 154 noms → 9 080 configurations, 4,55 paris indépendants.**

## Sources

**Ingérées (4)** — `um_klines_1d` (696 symboles) · `binance_vision_metrics` (310)
· `funding_mark` (322) · **`spot_klines_1d_vision` (409)**.
Manifeste SHA-256 dans `LOOP_STATE.json`.

**Restantes** — univers élargi · historique avant 2020 · collecte forward
*(tourne déjà, ~0,89 Go/j)*

**Impasse vérifiée** — liquidations historiques : 45 symboles *coin-margined*
seulement, contre un panel de 696 USDT-M. Aucun recouvrement utile.

## Hypothèses, finalistes, sleeves

- hypothèses écrites : **4** — `H-BASIS-1..4`, payeurs dans
  [hypotheses/H-BASIS.md](hypotheses/H-BASIS.md)
- finalistes en attente : **0**
- **sleeves validées : 0**

### Dernière exploration — `sweep_basis`, 2026-09-09

Famille basis mesurée pour la première fois (le spot venait d'être apparié) :
**aucun edge**. Meilleur `t` de la famille **+2,494** (`dis_basis_vs_crowd`),
sous la médiane du placebo (2,807), très loin du seuil (4,06). Corrobore le
prior « funding/basis épuisé par arbitrage 2025-26 ».

**Crible 8 mesuré** : les 14 signaux de tête ne font que **4,55 paris
indépendants**. `dis_basis_vs_crowd` est corrélé **+0,69** avec
`lsr_globacct_x` — ce n'est pas une seconde sleeve, c'est le positionnement
déguisé. Une seule direction forte existe dans l'exploration.

**Aucune promotion.** Le budget de 3 tests reste intact.

## Contrôles

- **contrôle positif** — pente **1,026**, r² 0,9775, du 2026-09-06 (3 jours).
  Dans [0,9 ; 1,1] et moins de 7 jours : **invariant satisfait**.
- **placebo** — 20 tirages, médiane 2,807, p95 3,878, p99 4,320.
- **basis vs funding** — corr de rang **+0,5345** : validation économique
  indépendante de l'appariement spot.

## Violations enregistrées

1. **2026-09-07 — relecture du même candidat.** `sweep_v5` a rouvert la fenêtre
   scellée sur deux candidats numériquement identiques à des candidats `sweep_v4`
   déjà jugés. Cause (dédoublonnage par nom) fermée par I8, **puis réellement**
   fermée par I10 — le correctif de I8 n'aurait pas empêché la récidive.

## Escalade ouverte

**Le candidat « qui survit » ne franchit pas le seuil de sa propre famille** :
`t = 2,399` hors échantillon contre 2,734 pour 16 essais, et `passes=False` sur
les 19 lignes des CSV `confirm`. Le `RAPPORT.md` de `sweep_v4` conclut l'inverse
en se plaçant à `n = 1`. Décision demandée — détail dans
[itérations/0001.md](iterations/0001.md).

## Prochaine itération

**Cas 1 — défaut d'instrument.** Les cribles **5** (tiers liquide *et*
illiquide), **6** (monotonie dans l'intensité) et **7** (±30 % sur chaque
paramètre) ne sont pas calculés par le harnais. Ils n'ont pas bloqué à
l'itération 5 — les cribles 1 et 8 suffisaient — mais **aucun candidat ne pourra
jamais être promu tant qu'ils manquent**. C'est le prochain défaut, et il passe
en tête de file.
