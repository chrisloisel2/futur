# CROWD_WASHOUT_NO_CASCADE — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** 10.6 bps net (source : liste de mission / rapport de découverte).
**Discipline d'indépendance :** aucun script ni dossier `evidence/` de découverte n'a été ouvert.
Réimplémentation entière depuis la définition économique, harnais commun `../_lib/validation_lib.py`.

---

## 1. Méthodologie

Population d'événements `crowding_dataset.parquet` (`CROWD_WASHOUT`), horizon `fwd_4h`, à partir de 2022-01-01 UTC, avec conditionnement par centile causal 365 j. Contraste bras A − bras B sur la même population.

## 2. Checklist de vérification

| Contrôle | Résultat |
|---|---|
| Causalité de la règle de seuil | Centile calculé sur `[t − 365 j, t)` strictement antérieur ; les événements sans ≥ 200 antécédents sont écartés, jamais imputés. |
| Causalité des features | Toutes les features de conditionnement sont des mesures antérieures à l'événement (as-of backward dans le dataset source). |
| Unités | `fwd_4h` en décimal → bps (×1e4) ; vérifié sur la moyenne de population. |
| Bras A − bras B | Appliqué systématiquement sur la même population, avec Welch sur les moyennes d'épisode ET régression cluster-robuste au niveau événement. |
| Déclustering | 3 niveaux, voir §4. C'est le point critique de cette famille. |
| Double lecture | Chaque résultat est produit en pondération épisode ET en pondération événement avec SE cluster-robuste — un candidat n'est retenu que si les deux tiennent. |

## 3. Résultat primaire

| Grandeur | Valeur |
|---|---|
| gross / **net** / net stress | 7.65 / **-6.35** / -20.35 bps |
| profit factor | 0.911 |
| **t cluster-robuste (L3)** | **-1.050** |
| bootstrap CI95 | [-18.08, 5.37] |
| bootstrap 5e centile | -16.14 |
| années positives | 1/5 |
| hors meilleure année | -9.97 bps |
| pire épisode | -1051.73 bps |
| drawdown cumulé max | -8069.21 bps |

Année par année (net) : 2022 **-78.0** · 2023 **-5.8** · 2024 **4.0** · 2025 **-4.8** · 2026 **-11.6**

## 4. Déclustering

**L1** = même symbole, chaîne < 24 h · **L2** = jour calendaire UTC · **L3 (inférence)** = **épisode cross-symbole chaîné, gap < 4 h**. Une cascade market-wide touche des dizaines d'alts dans les mêmes minutes : les compter séparément surestime N d'un ordre de grandeur. t cluster-robuste sur L3, block bootstrap par épisode.

| Niveau | N |
|---|---|
| brut | 2200 |
| L1 | 1979 |
| L2 | 746 |
| **L3 (inférence)** | **1050** |

## 5. Capacité

Mécanisme événementiel sur perps majeurs ; la contrainte est le nombre d'événements, pas la profondeur de carnet. Non chiffré plus finement (noté comme tel).

## 6. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| taux historique (2 ans) | 4.795/week |
| taux récent (6 mois) | 4.231/week |
| taux conservateur | 4.231/week |
| `n_required_statistical` | None |
| `minimum_calendar_days` | 60 |
| `eta_p50` | unbounded |
| **`eta_conservative`** | **unbounded** |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 7. Verdict

# `REJECTED`

Tags secondaires : `DATA_LIMITED`
`sign_correction_required` : **False**

La réclamation ('+10.6 bps, 6/7 années stable') ne se reproduit pas : population inconditionnelle CROWD_WASHOUT à -6.35 net14 (t=-1.05) au niveau épisode, avec seulement 1/5 années positives. La lecture événement est positive (+33.08) mais non significative (t_cluster=1.60) et contredit la lecture épisode. La queue extrême n'a que 215 événements / 101 épisodes -> trop mince pour trancher (event +257.73 mais t=1.62). Dataset de 2200 événements sur 2022-2026 : c'est la contrainte dure.

**`recommended_next_step` : `REJECT`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
