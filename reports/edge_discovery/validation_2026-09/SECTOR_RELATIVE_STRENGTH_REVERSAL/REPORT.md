# SECTOR_RELATIVE_STRENGTH_REVERSAL — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** 46.4 bps net (source : liste de mission / rapport de découverte).
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
| gross / **net** / net stress | -7.72 / **-21.72** / -35.72 bps |
| profit factor | 0.806 |
| **t cluster-robuste (L3)** | **-1.327** |
| bootstrap CI95 | [-54.18, 11.01] |
| bootstrap 5e centile | -49.07 |
| années positives | 2/7 |
| hors meilleure année | -33.26 bps |
| pire épisode | -1210.83 bps |
| drawdown cumulé max | -7938.91 bps |

Année par année (net) : 2020 **-172.8** · 2021 **-29.4** · 2022 **18.2** · 2023 **37.3** · 2024 **-29.4** · 2025 **-22.8** · 2026 **-30.1**

## 4. Déclustering

**L1** = position nom × rebalancement · **L2** = période de rebalancement · **L3 (inférence)** = mois calendaire. t cluster-robuste sur L3, block bootstrap mensuel (10 000 tirages).

| Niveau | N |
|---|---|
| brut | 9350 |
| L1 | 9350 |
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
| `n_required_statistical` | None |
| `minimum_calendar_days` | 182 |
| `eta_p50` | unbounded |
| **`eta_conservative`** | **unbounded** |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 7. Verdict

# `REJECTED`

Tags secondaires : `COST_FRAGILE`, `REGIME_DEPENDENT`, `UNCONFIRMABLE_IN_HORIZON`
`sign_correction_required` : **True**

Excess -21.72 net14 (t=-1.33) : NÉGATIF là où la découverte réclamait +46.4. Le signal de reversal intra-secteur a une corrélation de rang de 0.86 avec le momentum 7j — ce n'est pas un facteur sectoriel, c'est du momentum inversé, et le parier à l'envers perd. P1 (carte grossière) aggrave (-35.88, t=-2.03), donc le signe négatif n'est pas un artefact de carte.

**`recommended_next_step` : `REJECT`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
