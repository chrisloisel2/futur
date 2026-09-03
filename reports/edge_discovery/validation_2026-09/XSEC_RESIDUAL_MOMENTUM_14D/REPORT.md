# XSEC_RESIDUAL_MOMENTUM_14D — Rapport de validation indépendante

**Validateur :** Alpha Validation Factory wave 2, worker unique (session futur-49), 2026-09-03
**Réclamation testée :** 64.8 bps net (source : liste de mission / rapport de découverte).
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
| gross / **net** / net stress | 45.50 / **31.50** / 17.50 bps |
| profit factor | 1.178 |
| **t cluster-robuste (L3)** | **0.626** |
| bootstrap CI95 | [-68.4, 127.3] |
| bootstrap 5e centile | -52.10 |
| années positives | 5/7 |
| hors meilleure année | 12.93 bps |
| pire épisode | -4012.57 bps |
| drawdown cumulé max | -5199.96 bps |

Année par année (net) : 2020 **167.1** · 2021 **82.1** · 2022 **-133.6** · 2023 **6.7** · 2024 **-17.7** · 2025 **97.5** · 2026 **74.0**

## 4. Déclustering

**L1** = position nom × rebalancement · **L2** = période de rebalancement non chevauchante · **L3 (inférence)** = mois calendaire. t cluster-robuste (Liang-Zeger) sur L3, block bootstrap à blocs mensuels (10 000 tirages).

| Niveau | N |
|---|---|
| brut | 4784 |
| L1 | 4784 |
| L2 | 166 |
| **L3 (inférence)** | **77** |

## 5. Capacité

Perps Binance liquides sous plancher de dollar-volume causal ≥ $1 M ; un book de $300k équipondéré reste très en deçà de toute participation problématique (mesuré à 0,19 % de l'ADV au 5e centile pour la famille cross-sectionnelle).

## 6. Fréquence, N_required, ETA

| Champ | Valeur |
|---|---|
| taux historique (2 ans) | 0.230/week |
| taux récent (6 mois) | 0.231/week |
| taux conservateur | 0.230/week |
| `n_required_statistical` | 4768.7 |
| `minimum_calendar_days` | 182 |
| `eta_p50` | 145047 days (~397.1 years) |
| **`eta_conservative`** | **145047 days (~397.1 years)** |
| `confirmable_in_horizon` (< 3 ans) | **False** |

## 7. Verdict

# `REJECTED`

Tags secondaires : `UNCONFIRMABLE_IN_HORIZON`
`sign_correction_required` : **False**

Excess sur l'univers éligible +31.50 net14 mais t_L3=0.63 et bootstrap p05=-52.10 : non significatif. Surtout, CE N'EST PAS UN FACTEUR DISTINCT : corrélation de rang avec le momentum 14j brut = 0.951, corrélation des rendements de portefeuille = 0.928, recouvrement Jaccard des jambes longues = 0.82. Le test apparié (resid - brut) sur les mêmes dates donne -21.51 bps (t=-1.00) : le strip de beta n'AMÉLIORE PAS le momentum brut, il le dégrade légèrement. Le +64.8 réclamé était mesuré contre zéro, pas contre l'univers.

**`recommended_next_step` : `REJECT`**

---

*Chiffres bruts complets, perturbations et contrôles de chevauchement : `RESULTS.json`.
Scripts ré-exécutables : `../_lib/`.*
