# OI_COLLAPSE_BOUNCE — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** 247.0 bps net (source : liste de mission / rapport de découverte).
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
| gross / **net** / net stress | 32.31 / **18.31** / 4.31 bps |
| profit factor | 1.311 |
| **t cluster-robuste (L3)** | **2.744** |
| bootstrap CI95 | [5.49, 31.27] |
| bootstrap 5e centile | 7.45 |
| années positives | 4/5 |
| hors meilleure année | 9.13 bps |
| pire épisode | -580.61 bps |
| drawdown cumulé max | -3597.21 bps |

Année par année (net) : 2022 **8.0** · 2023 **53.4** · 2024 **18.4** · 2025 **-10.4** · 2026 **22.2**

## 4. Déclustering

**L1** = même symbole, chaîne < 24 h · **L2** = jour calendaire UTC · **L3 (inférence)** = **épisode cross-symbole chaîné, gap < 4 h**. Une cascade market-wide touche des dizaines d'alts dans les mêmes minutes : les compter séparément surestime N d'un ordre de grandeur. t cluster-robuste sur L3, block bootstrap par épisode.

| Niveau | N |
|---|---|
| brut | 2411 |
| L1 | 1769 |
| L2 | 771 |
| **L3 (inférence)** | **833** |

## 5. Capacité

Mécanisme événementiel sur perps majeurs ; la contrainte est le nombre d'événements, pas la profondeur de carnet. Non chiffré plus finement (noté comme tel).

## 6. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| taux historique (2 ans) | 3.701/week |
| taux récent (6 mois) | 4.692/week |
| taux conservateur | 3.701/week |
| `n_required_statistical` | 2698.5 |
| `minimum_calendar_days` | 60 |
| `eta_p50` | 5103 days (~14.0 years) |
| **`eta_conservative`** | **5103 days (~14.0 years)** |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 7. Verdict

# `NEEDS_MORE_RESEARCH`

Tags secondaires : `UNCONFIRMABLE_IN_HORIZON`, `CONDITIONING_ADDS_NOTHING`
`sign_correction_required` : **False**

Le bras seul est positif et significatif dans les deux conventions (épisode +18.31/t=2.74 ; événement +27.61/t_cluster=2.47). MAIS le test obligatoire bras A - bras B au niveau ÉPISODE donne -0.39 bps (Welch -0.05) : conditionner sur l'effondrement d'OI n'apporte RIEN par rapport au fade inconditionnel de cascade, qui porte déjà cet edge. Au niveau événement le contraste est significatif (+32.99, t=2.88) uniquement parce que la référence événement est négative. Le +247 bps réclamé ne se reproduit à aucune convention (max +50.79 sur la queue q05). ETA 14-28 ans.

**`recommended_next_step` : `MORE_RESEARCH`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
