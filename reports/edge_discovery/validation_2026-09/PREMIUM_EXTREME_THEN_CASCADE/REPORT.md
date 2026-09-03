# PREMIUM_EXTREME_THEN_CASCADE — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** 12.1 bps net (source : liste de mission / rapport de découverte).
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
| gross / **net** / net stress | 33.69 / **19.69** / 5.69 bps |
| profit factor | 1.217 |
| **t cluster-robuste (L3)** | **1.455** |
| bootstrap CI95 | [-6.12, 46.43] |
| bootstrap 5e centile | -1.87 |
| années positives | 4/5 |
| hors meilleure année | 7.86 bps |
| pire épisode | -1230.53 bps |
| drawdown cumulé max | -4213.69 bps |

Année par année (net) : 2022 **-36.4** · 2023 **42.0** · 2024 **118.4** · 2025 **11.8** · 2026 **5.5**

## 4. Déclustering

**L1** = même symbole, chaîne < 24 h · **L2** = jour calendaire UTC · **L3 (inférence)** = **épisode cross-symbole chaîné, gap < 4 h**. Une cascade market-wide touche des dizaines d'alts dans les mêmes minutes : les compter séparément surestime N d'un ordre de grandeur. t cluster-robuste sur L3, block bootstrap par épisode.

| Niveau | N |
|---|---|
| brut | 1760 |
| L1 | 1640 |
| L2 | 523 |
| **L3 (inférence)** | **533** |

## 5. Capacité

Mécanisme événementiel sur perps majeurs ; la contrainte est le nombre d'événements, pas la profondeur de carnet. Non chiffré plus finement (noté comme tel).

## 6. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| taux historique (2 ans) | 3.059/week |
| taux récent (6 mois) | 4.538/week |
| taux conservateur | 3.059/week |
| `n_required_statistical` | 6110.1 |
| `minimum_calendar_days` | 60 |
| `eta_p50` | 13982 days (~38.3 years) |
| **`eta_conservative`** | **13982 days (~38.3 years)** |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 7. Verdict

# `NEEDS_MORE_RESEARCH`

Tags secondaires : `THIN_EPISODE_EVIDENCE`
`sign_correction_required` : **False**

Le seul candidat de la vague avec un contraste A-B positif dans les deux conventions (épisode +21.41 ; événement +32.99... t=3.08) MAIS un t d'épisode de 1.455 sous le seuil 1.645. Population inconditionnelle PREM_CAPITULATION = 0 (épisode +0.12) : c'est bien la QUEUE extrême qui porte l'effet, et le contrôle de direction est propre (queue haute nulle, -1.43). Grand écart épisode (+19.69) / événement (+101.85) = quelques gros épisodes dominent -> preuve concentrée. À reprendre avec une définition d'épisode préenregistrée et un N plus grand avant tout freeze.

**`recommended_next_step` : `MORE_RESEARCH`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
