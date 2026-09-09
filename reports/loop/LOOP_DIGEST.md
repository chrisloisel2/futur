# Boucle de recherche — digest

*régénéré à l'itération 1 · 2026-09-09*

## Budget

**0 test disponible.** Solde brut **−2** (crédité 14, consommé 16).

| source | jours | crédit |
|---|---|---|
| `um_klines_1d` | 2 330 | +5 *(plafond)* |
| `binance_vision_metrics` | 1 673 | +4 |
| `funding_mark` | 2 330 | +5 *(plafond)* |
| `sweep_v4` confirm | — | −9 |
| `sweep_v5` confirm | — | −7 |

Pas de donnée nouvelle → pas de test. La boucle travaille, elle ne teste pas.

## Seuil courant

**`threshold_t(16) = 2,7344`** — dérivé, jamais saisi
(`preregistration.py::threshold_t`, Bonferroni unilatéral à α = 0,05).

## Défauts d'instrument

**Ouverts (2)**

| id | défaut | bloque |
|---|---|---|
| I3 | plafond de capacité adossé à l'ADV, pas à la profondeur | famille illiquidité |
| I7 | le coût de 14 bps est une hypothèse, pas une mesure | tout signal à fort churn |

**Fermés (7)** — I1, I2, I4, I5 (v4, vérifiés) · I6, I8, I9 (itération 1)

## Sources

**Ingérées** — `um_klines_1d` (696 symboles), `binance_vision_metrics` (310),
`funding_mark` (322). Manifeste SHA-256 dans `LOOP_STATE.json`.

**Restantes** — spot apparié · liquidations historiques · univers élargi ·
historique avant 2020 · collecte forward *(tourne déjà, ~0,89 Go/j)*

## Hypothèses, finalistes, sleeves

- hypothèses écrites : **0**
- finalistes en attente : **0**
- **sleeves validées : 0**

## Contrôles

- **contrôle positif** — pente **1,026**, r² 0,9775, du 2026-09-06 (3 jours).
  Dans [0,9 ; 1,1] et moins de 7 jours : **invariant satisfait**.
- **placebo** — 20 tirages, médiane 2,807, p95 3,878, p99 4,320.

## Violations enregistrées

1. **2026-09-07 — relecture du même candidat.** `sweep_v5` a rouvert la fenêtre
   scellée sur deux candidats numériquement identiques à des candidats `sweep_v4`
   déjà jugés. Cause (dédoublonnage par nom) fermée par I8.

## Escalade ouverte

**Le candidat « qui survit » ne franchit pas le seuil de sa propre famille** :
`t = 2,399` hors échantillon contre un seuil à 2,734 pour 16 essais, et
`passes=False` sur les 19 lignes des CSV `confirm`. Le `RAPPORT.md` de `sweep_v4`
conclut l'inverse en se plaçant à `n = 1`. Décision demandée — détail dans
[itérations/0001.md](iterations/0001.md).
