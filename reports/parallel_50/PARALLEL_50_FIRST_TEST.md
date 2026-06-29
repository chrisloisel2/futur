# PARALLEL_50 — premier test sur 100K (données déjà récoltées)

> Question : *« combien je gagne en tradant ces 50 cryptos ? »*
> Réponse honnête : **naïvement, tu PERDS −39,6 % (100K → 60,4K).** L'élargissement
> sans discipline de sélection est destructeur. Ce n'est pas un bug, c'est le coût du churn.

## Setup

- Fenêtre : 2022-11-03 → 2026-06-28 (~3,5 ans), capital 100 000 $.
- Même config dans les deux runs : asset_regime_gate + regime_flip_exit + intra_governor
  + carry BTC/ETH (fraction 0,50) + hedge. Données : enriched backfillé (49 PASS / 1 WARN).
- Seule variable : l'univers de PULLBACK_LONG (4 actifs vs 49). TRM (BTC/ETH) et
  LIQUIDATION (data-gated) identiques. **Le ranker top-3-7 N'EST PAS câblé** → tous les
  signaux alts sont exécutés.

## Résultat

| 100 000 $ | BASELINE_9 | PARALLEL_50 (naïf) |
|---|---:|---:|
| Capital final | **118 186 $** | **60 418 $** |
| Gain | **+18 186 $ (+18,2 %)** | **−39 582 $ (−39,6 %)** |
| PF | 1,03 | 0,92 |
| maxDD | −1,7 % | **−43,7 %** |

### Décomposition PnL (la preuve)

| | directionnel | carry | hedge | frais | NET |
|---|---:|---:|---:|---:|---:|
| 9 cryptos | +6 098 | +27 751 | +85 | −15 719 | **+18 186** |
| 50 cryptos | +8 848 | +7 693 | +701 | **−56 814** | **−39 582** |

- Le directionnel brut est **plus élevé** sur 50 (+8 848 > +6 098) → les signaux alts
  ne sont pas faux.
- **Frais ×3,6** (−56 814 vs −15 719) : le churn des 49 alts mange tout l'edge.
- Carry s'effondre (27 751 → 7 693) car dimensionné sur une equity qui s'écroule.

## Cause racine

Le backtester exécute **tous** les signaux des 49 alts (max_open_longs=3 mais rotation
~3,5 entrées/jour vs ~1/jour). Le **ranker** (`opportunity_ranker.py` : top 3-7, max 2/bucket,
1 meme, no BLOCK) — cœur du design PARALLEL_50 — **n'est pas branché dans le backtester
multi-jambes**. Sans sélection, élargir l'univers = augmenter le churn = ruine.

→ **4ᵉ occurrence de la règle structurelle** : *un signal valide localement est destructeur
en exécution portefeuille s'il n'est pas filtré.* (exit engine, forced-exit, carry-gate v2, et
maintenant univers élargi non-rankés.)

## Verdict

`PARALLEL_50_NAIVE : REJECTED (−39,6 %, churn-fees)`. L'élargissement d'univers ne crée de
la valeur **que** si la discipline de sélection top-3-7 est imposée. Prochaine étape : câbler
le ranker dans le backtester multi-jambes et re-tester — **seul** moyen de savoir si 50 > 9
*avec* sélection. Aucune promotion tant que ce test ranké n'est pas positif net de frais.
