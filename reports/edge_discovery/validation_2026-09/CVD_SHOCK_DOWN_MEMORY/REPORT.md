# CVD_SHOCK_DOWN_MEMORY — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** 15.5 bps net (source : liste de mission / rapport de découverte).
**Discipline d'indépendance :** aucun script ni dossier `evidence/` de découverte n'a été ouvert.
Réimplémentation entière depuis la définition économique, harnais commun `../_lib/validation_lib.py`.

---

## 1. Méthodologie

Population d'événements depuis `data/events/*_dataset.parquet`, filtrée sur `label_full == True`, horizon `fwd_4h`, à partir de 2022-01-01 UTC. Tout conditionnement utilise une règle de centile **causale** sur une fenêtre glissante de 365 j (≥ 200 événements antérieurs exigés, sinon l'événement est écarté et compté) — jamais un centile in-sample. Chaque test est un contraste **bras A − bras B sur la même population**, jamais un « A > 0 ».

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
| gross / **net** / net stress | 13.98 / **-0.02** / -14.02 bps |
| profit factor | 1.000 |
| **t cluster-robuste (L3)** | **-0.003** |
| bootstrap CI95 | [-13.01, 13.17] |
| bootstrap 5e centile | -10.99 |
| années positives | 2/5 |
| hors meilleure année | -6.07 bps |
| pire épisode | -1592.68 bps |
| drawdown cumulé max | -6453.77 bps |

Année par année (net) : 2022 **-33.3** · 2023 **19.7** · 2024 **14.0** · 2025 **-6.7** · 2026 **-26.6**

## 4. Déclustering

**L1** = même symbole, chaîne < 24 h · **L2** = jour calendaire UTC · **L3 (inférence)** = **épisode cross-symbole chaîné, gap < 4 h**. Une cascade market-wide touche des dizaines d'alts dans les mêmes minutes : les compter séparément surestime N d'un ordre de grandeur. t cluster-robuste sur L3, block bootstrap par épisode.

| Niveau | N |
|---|---|
| brut | 2305 |
| L1 | 2040 |
| L2 | 970 |
| **L3 (inférence)** | **1133** |

## 5. Capacité

Mécanisme événementiel sur perps majeurs ; la contrainte est le nombre d'événements, pas la profondeur de carnet. Non chiffré plus finement (noté comme tel).

## 6. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| taux historique (2 ans) | 4.977/week |
| taux récent (6 mois) | 4.538/week |
| taux conservateur | 4.538/week |
| `n_required_statistical` | None |
| `minimum_calendar_days` | 60 |
| `eta_p50` | unbounded |
| **`eta_conservative`** | **unbounded** |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 7. Verdict

# `REJECTED`

Tags secondaires : —
`sign_correction_required` : **True**

Nul à négatif dans toutes les lectures : bras seul épisode -0.02 (t=-0.003), événement -5.31 (t=-0.72). Le contraste A-B est NÉGATIF et significatif (-19.4 épisode, Welch -2.66). La variante taker_delta_1h est significativement négative (-11.88, t_cluster=-2.19). Le 'gros N' invoqué par la découverte est un N de jambes corrélées : 2305 événements pour 1133 épisodes seulement.

**`recommended_next_step` : `REJECT`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
