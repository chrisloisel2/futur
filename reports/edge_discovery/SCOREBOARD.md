# Edge discovery scoreboard

Cible : 5-10 edges indépendants, simples, net-of-costs, en dehors du tournoi ALPHA_20 déjà
en production. Pipeline suivi pour chaque piste :

```
DEV_DISCOVERY -> INDEPENDENT_CONFIRMATION -> EXECUTION_ECONOMICS -> PAPER_LIVE -> PORTFOLIO_ADMISSION
```

Règle de gel (voir `docs/PHASE4F_TRUTH_ACCEPTANCE.md` pour l'esprit) : un IC/edge annoncé
"confirmé" doit porter un manifeste de provenance (hash de config + SHA-256 des données + seed).
Sans ça, il reste `UNFROZEN` — discovery, pas confirmé.

| # | mécanisme | statut | IC (discovery) | notes |
|---|---|---|---|---|
| 1 | A2 — venue dislocation convergence | `DEV_DISCOVERY` ✅ (8/8 horizons, budget famille 8/8 consommé, p=0.004975 = plancher à 200 permutations) | 0.195 @ 2s (0.127 @ 100ms → 0.104 @ 30s) | Figé sur `dev_6h`. Prochaine étape : `INDEPENDENT_CONFIRMATION` sur données jamais vues, fenêtre ≥24h (contrainte V5 par défaut). Ne pas retoucher le modèle en regardant `dev_6h` d'ici là — data snooping. |

## Comment ajouter une piste

1. Une ligne par **famille économique**, pas par variante testée (ex : A2 = 1 ligne malgré
   8 horizons — ce sont 8 expressions temporelles du même mécanisme, pas 8 edges).
2. Statut avancé seulement avec preuve : nombre de folds OOS positifs, p/q-value, et si le
   test a frappé le plancher de résolution (`1/(n_permutations+1)`) — le signaler explicitement,
   ça ne veut pas dire "plus significatif que ça".
3. `EXECUTION_ECONOMICS` doit chiffrer fees + spread + slippage + latence avant `PAPER_LIVE` —
   le plus gros IC brut n'est pas nécessairement le meilleur net alpha (cf. A2 : 2s vs 5s).
