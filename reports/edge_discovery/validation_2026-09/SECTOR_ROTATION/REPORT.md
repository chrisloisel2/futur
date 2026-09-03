# SECTOR_ROTATION — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** 103.0 bps net (source : liste de mission / rapport de découverte).
**Discipline d'indépendance :** aucun script ni dossier `evidence/` de découverte n'a été ouvert.
Réimplémentation entière depuis la définition économique, harnais commun `../_lib/validation_lib.py`.

---

## 1. Méthodologie

Même panel PIT que la famille cross-sectionnelle (voir `XSEC_MOMENTUM_HORIZON_EXTENSION`). La carte sectorielle est construite par le validateur (`_lib/sector_map_v5.py`, 10 secteurs + `OTHER`), jamais lue depuis un `sector_map.py` de découverte ; sa sensibilité est une perturbation obligatoire. BTC et ETH sont exclus du classement sectoriel (ce sont des paniers à eux seuls). Statistique de verdict = l'EXCESS sur l'univers éligible équipondéré.

## 2. Checklist de vérification

| Contrôle | Résultat |
|---|---|
| Causalité des features | Toute fenêtre se termine à `d` inclus ou avant ; aucun close postérieur n'entre dans un signal. Le panel n'est jamais rempli par interpolation (`min_periods` = fenêtre pleine). |
| Croissance d'univers / âge de listing | PIT strict : `d >= onboard_ts + 30 j`, éligibilité recalculée à chaque date. n_eligible passe de 21 (2020) à 258 (2025-26). |
| Délistages / renommages | Sortie forcée au dernier close disponible dans la fenêtre de détention — un nom qui disparaît est réalisé, pas supprimé. |
| Unités | Rendements en décimal → bps (×1e4). Dollar-volume en USDT bruts. |
| Bras A − bras B | Appliqué : le verdict porte sur l'excess vs l'univers éligible équipondéré, pas sur le rendement brut. |
| Déclustering | Appliqué aux 3 niveaux, voir §4. |
| Coûts | 14 bps (une jambe) / 28 bps (long-short), + stress à coût doublé et perturbation à +50 %. |

## 3. Résultat primaire

| Grandeur | Valeur |
|---|---|
| gross / **net** / net stress | 27.29 / **13.29** / -0.71 bps |
| profit factor | 1.165 |
| **t cluster-robuste (L3)** | **0.807** |
| bootstrap CI95 | [-17.66, 46.36] |
| bootstrap 5e centile | -12.71 |
| années positives | 5/7 |
| hors meilleure année | 2.69 bps |
| pire épisode | -999.87 bps |
| drawdown cumulé max | -3536.64 bps |

Année par année (net) : 2020 **-52.6** · 2021 **67.5** · 2022 **1.9** · 2023 **25.9** · 2024 **-7.6** · 2025 **0.0** · 2026 **36.2**

## 4. Déclustering

**L1** = position nom × rebalancement · **L2** = période de rebalancement · **L3 (inférence)** = mois calendaire. t cluster-robuste sur L3, block bootstrap mensuel (10 000 tirages).

| Niveau | N |
|---|---|
| brut | 15471 |
| L1 | 15471 |
| L2 | 318 |
| **L3 (inférence)** | **74** |

## 5. Capacité

Perps Binance liquides sous plancher de dollar-volume causal ≥ $1 M ; un book de $300k équipondéré reste très en deçà de toute participation problématique (mesuré à 0,19 % de l'ADV au 5e centile pour la famille cross-sectionnelle).

## 6. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| taux historique (2 ans) | 0.230/week |
| taux récent (6 mois) | 0.231/week |
| taux conservateur | 0.230/week |
| `n_required_statistical` | 2738.6 |
| `minimum_calendar_days` | 182 |
| `eta_p50` | 83299 days (~228.1 years) |
| **`eta_conservative`** | **83299 days (~228.1 years)** |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 7. Verdict

# `REJECTED`

Tags secondaires : `COST_FRAGILE`, `UNCONFIRMABLE_IN_HORIZON`
`sign_correction_required` : **False**

Excess +13.29 net14, t_L3=0.81, net28=-0.71 (négatif) -> échoue sur significativité ET sur le coût de stress. Le raw vs zéro (+89.80) approche la réclamation (+103.0) : encore une fois le chiffre publié capture la dérive du panier, pas la rotation. Effondrement sur les perturbations structurelles : P2 (>=5 membres/secteur) -> -1.63 ; P3 (sans le panier OTHER) -> +3.94 ; P4 (hors 2021) -> +2.69, c'est-à-dire que l'essentiel de l'effet est concentré sur 2021. La carte grossière (P1) garde le signe (+15.73), donc le résultat n'est pas un artefact de MA carte — il est simplement trop faible.

**`recommended_next_step` : `REJECT`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
