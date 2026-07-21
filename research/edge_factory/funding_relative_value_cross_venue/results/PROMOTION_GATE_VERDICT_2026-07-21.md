# Verdict promotion_gate — funding_relative_value_cross_venue_v1 (Binance↔Bybit)

Étape 6→8 partielle : les résultats de
[EVENT_LEVEL_NET_EDGE_2026-07-21.json](EVENT_LEVEL_NET_EDGE_2026-07-21.json)
sont passés directement dans `src.alpha20.validation.promotion_gate.gate_sleeve()`
et `gate_research()` via `MultiLegBacktestResult.run_sleeve_gate()` /
`.run_research_gate()` — aucun gate recalculé à la main.

## gate_sleeve (étape 6)

| Gate | Valeur | Seuil | Verdict |
|---|---|---|---|
| pf_min | 0,67 | ≥ 1,30 | ❌ FAIL |
| costs_x2_positive | −0,152 | > 0 | ❌ FAIL |
| top10_events_removed_positive | −0,043 | > 0 | ❌ FAIL |
| no_destructive_recent_year | −0,0078 | > −0,01 | ✅ PASS (marginal) |

## gate_research (étape 10, indicatif à ce stade)

| Gate | Valeur | Seuil | Verdict |
|---|---|---|---|
| dsr_min | 6,4×10⁻¹² | ≥ 0,95 | ❌ FAIL |
| max_corr_with_kept_sleeves | 0,0 (non calculé, pas de portefeuille combiné) | ≤ 0,25 | ✅ PASS (non informatif) |
| min_capacity | 0 € (non estimé) | ≥ 200 000 € | ❌ FAIL |

Le PBO n'a pas été calculé : aucune grille de paramètres n'a été testée sur
cette paire (`n_trials=1`, seuils repris tels quels de FUNDING_XVENUE_V0) —
`gate_research()` saute ce gate quand `trials_matrix=None`, ce qui est le
comportement correct ici, pas une omission.

## Interprétation

3 des 4 gates `gate_sleeve` échouent nettement (PF < 1, coûts ×2 négatifs,
même en retirant les 10 plus gros événements le résultat reste négatif). Le
DSR est essentiellement nul — la série a une moyenne négative, ce qui rend
le test caduc par construction (un Sharpe négatif ne peut pas passer un
gate de Sharpe déflaté positif). Le seul gate qui passe
(`no_destructive_recent_year`) le fait de justesse et n'a aucune valeur
probante isolément : il ne fait que constater que 2026 n'a pas été pire que
les années précédentes, toutes déjà négatives.

Ce résultat est cohérent avec l'avertissement déjà documenté dans
`reports/FUNDING_XVENUE_PROTOCOL.md` : la paire Binance↔Bybit y était
reléguée "secondaire non-gating" sur la base d'un spread médian déjà
quasi nul dans un rapport antérieur. Ce test — le premier réellement gaté
sur cette paire précise, avec prix réels des deux côtés — confirme
directement ce signal préalable plutôt que de le contredire.

## Verdict

**NO_EDGE — Binance↔Bybit funding relative value.** Le spread brut est
trop petit pour survivre même à des coûts modestes (turnover déjà faible,
60-90 changements de direction sur ~4 ans). Aucune retouche des seuils
d'hystérésis n'est proposée ici (ce n'est pas l'objet de ce test) ; rouvrir
cette paire spécifique nécessiterait une thèse nouvelle et précise, pas un
retuning des mêmes paramètres.

## Ce qu'il reste de la piste

Hyperliquid (paire primaire, déjà testée et fermée NO_EDGE dans
FUNDING_XVENUE_V0) et Bybit (ce document) sont maintenant tous les deux
NO_EDGE contre Binance. Sans un quatrième venue candidat ou un changement
matériel de mécanisme, `funding_relative_value_cross_venue_v1` n'a plus de
paire testable non fermée à ce stade.
