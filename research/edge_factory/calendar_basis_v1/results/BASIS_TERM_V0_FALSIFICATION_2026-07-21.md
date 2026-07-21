# Falsification indépendante de basis_term_v0 (entry5_size50)

Mandat (2026-07-21, humain) : falsifier le résultat existant, figé, sous les
gates edge_factory complets — **aucune nouvelle variante recherchée**, aucun
paramètre retouché. Ce document est un verdict de référee, pas une recherche
d'alpha.

## Contexte

`basis_term_v0` (`configs/alpha20_runners.yaml`) tourne en paper live dans le
tournoi ALPHA_20. Son backtest (`scripts/backtest_basis_term.py`, commit
`04e345e`, 2026-07-13) a testé 6 variantes (seuil d'entrée 3/5/8 × taille
25/50) et retenu `entry5_size50` comme primaire *après* avoir vu les 6
résultats : 38 trades, ROI +51,39 % (+8,39 %/an), maxDD −4,32 %,
2021-2026. `configs/alpha20.yaml` marque ce runner `status: a_qualifier`.

## Méthode

Reproduction indépendante depuis les données brutes (48 parquet
`binance_vision_quarterly` + `contracts.json`, proxy spot = close perp
enrichi), script jetable hors dépôt (`~/basis_term_falsification/` sur
qbee) qui ne touche jamais `data/alpha20/tournament/` ni aucun fichier
suivi. Résultat identique au rapport commité (38 trades, +51,39 %,
−4,32 % maxDD) — la reproduction est fidèle.

## Résultat par gate

| Gate | Valeur | Seuil | Verdict |
|---|---|---|---|
| DSR (n_trials=6) | 0,9975 | ≥ 0,95 | ✅ PASS* |
| **PBO (CSCV, 6 variantes)** | **0,47** | **≤ 0,10** | **❌ FAIL** |
| Coûts ×2 (46 bps AR) | +7,47 %/an, maxDD −4,53 %, toutes années positives | net positif | ✅ PASS |
| Leave-one-year | +5,28 %/an (pire cas, retrait 2024) à +8,75 %/an | pas d'inversion de signe | ✅ PASS |
| Leave-one-asset | BTC seul +4,14 %/an, ETH seul +4,34 %/an, ~45/45 % | pas de concentration | ✅ PASS |
| Holdout 2025-2026 (params gelés depuis 2021-2024) | +6,92 % sur 7 trades (1,24 an) | positif | ⚠️ PASS directionnel, sous-alimenté |

\* DSR calculé sur la série quotidienne de MTM (T=1701 jours), fortement
autocorrélée à l'intérieur de chaque position tenue (décroissance lisse de la
base) — le PASS est littéral, pas une confiance non réservée : l'hypothèse
IID du DSR est mise à mal par seulement 38 trades réels derrière 1701 jours
de PnL.

Le holdout n'est pas non plus un vrai OOS aveugle : `entry5_size50` a été
choisi *après* avoir vu l'échantillon complet 2021-2026, qui inclut déjà la
période de test — à lire comme un contrôle de cohérence, pas une preuve
d'indépendance.

## Verdict global

**FAILS disciplined falsification.** Le seul échec — PBO = 0,47, proche de
la valeur ~0,50 attendue sous pur bruit — suffit à lui seul à rejeter
`entry5_size50` comme candidat validé sous les règles de ce programme,
indépendamment des 5 autres gates qui passent. La sélection a posteriori du
primaire parmi 6 variantes ne montre aucune compétence de sélection
out-of-sample démontrée.

## Ce que ce document ne fait PAS

Ne propose aucune variante alternative, aucun paramètre retuné. La décision
sur le sort de `basis_term_v0` en tant que runner paper live (pause,
reclassification, statut inchangé) appartient à l'humain — ce document
fournit le verdict de falsification, pas la décision opérationnelle.

Détail machine-readable : [BASIS_TERM_V0_FALSIFICATION_2026-07-21.json](BASIS_TERM_V0_FALSIFICATION_2026-07-21.json).
