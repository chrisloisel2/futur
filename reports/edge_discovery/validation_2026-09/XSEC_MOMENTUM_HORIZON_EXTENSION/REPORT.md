# XSEC_MOMENTUM_HORIZON_EXTENSION — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** 199.3 bps net (source : liste de mission / rapport de découverte).
**Discipline d'indépendance :** aucun script ni dossier `evidence/` de découverte n'a été ouvert.
Réimplémentation entière depuis la définition économique, harnais commun `../_lib/validation_lib.py`.

---

## 1. Méthodologie

Panel quotidien reconstruit indépendamment depuis les barres 5 m `data_v2/normalized/perp_ohlcv` (close = dernière barre 5 m du jour UTC, dv = somme du quote_asset_volume) : **365 980 lignes, 312 symboles, 2020-01-01 → 2026-07-31**. Univers PIT à chaque rebalancement : âge de listing ≥ 30 j (`listings_calendar.parquet`, 1 seul symbole en fallback), médiane causale 30 j du dollar-volume ≥ $1 M (fenêtre pleine exigée), `n_eligible ≥ 20`. Médiane 129 noms éligibles, min 0, max 258, première date éligible 2020-03-14 — l'univers croît réellement, il n'est pas copié à rebours. Sortie au dernier close disponible dans la fenêtre (un délisté n'est jamais retiré : pas de biais du survivant). Winsorisation 1 %/99 % sur la cross-section éligible complète. **Statistique de verdict = l'EXCESS sur le bras B** (univers éligible équipondéré), jamais le rendement contre zéro.

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
| gross / **net** / net stress | 65.78 / **51.78** / 37.78 bps |
| profit factor | 1.275 |
| **t cluster-robuste (L3)** | **0.850** |
| bootstrap CI95 | [-69.97, 167.59] |
| bootstrap 5e centile | -48.76 |
| années positives | 6/7 |
| hors meilleure année | 18.56 bps |
| pire épisode | -4107.25 bps |
| drawdown cumulé max | -5823.26 bps |

Année par année (net) : 2020 **282.7** · 2021 **96.4** · 2022 **-157.7** · 2023 **19.9** · 2024 **12.8** · 2025 **90.7** · 2026 **83.7**

## 4. Déclustering

**L1** = position nom × rebalancement · **L2** = période de rebalancement non chevauchante · **L3 (inférence)** = mois calendaire. t cluster-robuste (Liang-Zeger) sur L3, block bootstrap à blocs mensuels (10 000 tirages).

| Niveau | N |
|---|---|
| brut | 4836 |
| L1 | 4836 |
| L2 | 167 |
| **L3 (inférence)** | **77** |

## 5. Capacité

Perps Binance liquides sous plancher de dollar-volume causal ≥ $1 M ; un book de $300k équipondéré reste très en deçà de toute participation problématique (mesuré à 0,19 % de l'ADV au 5e centile pour la famille cross-sectionnelle).

## 6. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| taux historique (2 ans) | 0.230/week |
| taux récent (6 mois) | 0.231/week |
| taux conservateur | 0.230/week |
| `n_required_statistical` | 2587.3 |
| `minimum_calendar_days` | 182 |
| `eta_p50` | 78698 days (~215.5 years) |
| **`eta_conservative`** | **78698 days (~215.5 years)** |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 7. Verdict

# `REJECTED`

Tags secondaires : `UNCONFIRMABLE_IN_HORIZON`
`sign_correction_required` : **False**

Excess +51.78 net14, t_L3=0.85, p05=-48.76 -> échec du critère 1. Le raw vs zéro (+254.53) dépasse même la réclamation (+199.3), ce qui confirme que le chiffre publié mesurait surtout la dérive inconditionnelle du panier alt, pas le mécanisme. P1 (30D_LO) est NÉGATIF (-66.73) alors que la découverte réclamait +462.8 : la variante 30 jours ne se reproduit pas du tout. Direction stable (14/14 ancrages positifs, pooled +60.07) mais magnitude non significative.

**`recommended_next_step` : `REJECT`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
