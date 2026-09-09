# Balayage v4 — le harnais réparé, et ce qui en sort

*2026-09-07*

## Pourquoi v3 ne pouvait pas trouver la famille qui compte

Trois défauts de **source**, pas de méthode. Ils expliquent à eux seuls l'écart
entre le classement de v3 et celui-ci.

| | v3 | v4 |
|---|---|---|
| prix | `data/enriched/` — **50 symboles** | `um_klines_1d` — **696 symboles**, 2020-01 → 2026-06 |
| univers | `--top-n 50` sur 50 noms = **aucun crible** | top-120 par ADV 20j + contrainte de capacité |
| open interest | `open_interest_hist` = **30 jours glissants d'API**, aucun chevauchement avec la fenêtre | `binance_vision_metrics` — 5 min, 200 symboles, depuis 2021-12 |
| ratios long/short | **absents** | 4 séries (foule/compte, top-trader/compte, top-trader/position, taker) |
| flux preneur | `taker_buy_*` = placeholder | **réel** (2 373 valeurs distinctes sur BTC, corr 0,556 avec le rendement du jour) |

La famille dérivés n'avait jamais été testée sur ce projet. Pas parce qu'elle est
absente : parce que le loader regardait à 500 lignes de là.

## Les défauts d'inférence corrigés

1. **`effective_tests` prenait `list(pnls)[:200]`** — l'ordre d'insertion, soit
   4 signaux sur 58. Corrigé : toutes les configurations, alignées sur l'index
   complet (jour non traité = PnL 0, ce qui est exact). Meff passe de 10,4 à 12,3.
   *L'échantillonnage n'était pas le problème : 2 898 configurations ne sont que
   ~12 paris indépendants.*
2. **La formule n'est pas Cheverud/Nyholt, c'est le ratio de participation.**
   Il sature : sur 1 facteur dominant + K directions indépendantes il rend
   3,81 / 3,92 / 3,96 / 3,98 pour K = 20 / 50 / 100 / 200. Sur la grille v4 il
   donnait 35,8 quand Li-Ji donnait 841. **Aucun estimateur n'est fiable à p ≫ n.**
3. **Seuil unilatéral sur un côté choisi a posteriori.** Corrigé : bilatéral.
4. **Le `t` du côté INV n'était pas rejoué** — reconstruit avec la SE de la série
   non retournée. Corrigé : `net_INV = −gross − cost` terme à terme (exact, car
   inverser le score négate les poids et laisse |dW| inchangé).
5. **Newey-West à `lag = max(horizon, 2)`** alors que le portefeuille détenu est
   la moyenne de `horizon × hold` cibles plus un lissage `smooth`. Corrigé.
6. **Exécution sur la barre du signal.** `--exec-lag 1` : score en t, exécution à
   `close(t+1)`. *Mesuré : coûte au plus 0,83 de `t` sur toute la grille.*
7. **Égalités tranchées par ordre alphabétique.** `rank(method="first")` départage
   les ex æquo par ordre de colonne. `jump_share_20` a **85,8 %** de part modale
   et 100 % de jours à >25 % d'égalités : sa jambe courte *est* la liste
   alphabétique. Garde-fou ajouté, 13 signaux sur 110 écartés.
8. **`passes` testait `|t| > seuil`.** Après choix du meilleur côté, un `t` négatif
   signifie que même le meilleur côté perd. Corrigé : `t > seuil`.

## Le seuil qui ne suppose rien

20 tirages, symboles permutés jour par jour (le lien signal→rendement est détruit,
la distribution transversale et le rendement de marché sont conservés), grille
entière rejouée, `max(t)` retenu.

```
max(t) sous le nul : médiane 2,81   p95 3,88   p99 4,32
tirages : 2,32 2,33 2,45 2,50 2,54 2,65 2,68 2,72 2,77 2,77
          2,84 3,07 3,08 3,08 3,22 3,35 3,42 3,58 3,85 4,43
```

Bonferroni + Li-Ji donnait 4,01 — cohérent. Le ratio de participation donnait
3,29 — c'est lui qui était faux, et c'est lui que v3 utilisait.

## Ce qui meurt

| signal | verdict |
|---|---|
| `close_location_20` | **INV, t = 1,73** sur 696 symboles — le signe s'inverse. Le DIR t=3,28 de v3 était une propriété du panel de 50, pas du marché. Non causé par la barre partagée (t=1,96 même en `exec-lag 0`). |
| `amihud_*` | **est** le modèle de coût : corrélation de rang 0,964 avec `symbol_cost_bps`. `t` 3,03 → 0,43 sous neutralisation par la taille. |
| `roll_spread_*` | 26 % de part modale, jambe courte dégénérée un jour sur deux. La « contradiction » avec `amihud` n'existe pas. |
| `jump_share_20` | 85,8 % de part modale. |
| `taker_imb_z*` INV | `gross ≈ −1 bps`, `cost ≈ 25 bps` : le `t` ne mesure que la constante de coût. |
| étage 2 de v3 | sous nul à états tournés, le meilleur `t` conditionné (3,283, **ligne n°1** du rapport v3) tombe *sous* la médiane du nul (3,347), **p = 0,575**. Un masque aléatoire bat le meilleur étage 1 dans 70 % des répétitions. |

## Ce qui survit

**`lsr_globacct_x | mkt | h3 k8 hd1 sm0 DIR`** — acheter, en transversal et
neutralisé du bêta marché, les noms que la foule des comptes Binance est le moins
longue ; vendre ceux qu'elle est le plus longue.

Unique hypothèse **primaire** pré-enregistrée avant d'ouvrir la fenêtre 2024-07 → 2026-06.

| | en échantillon (853 j) | hors échantillon (640 j) |
|---|---|---|
| Sharpe | 3,07 | **1,95** |
| mensuel médian (1× brut) | +3,48 % | **+3,08 %** |
| mois positifs | 21/28 | 15/22 |
| pire mois | −2,56 % | −6,61 % |
| perte maximale | −10,2 % | −14,7 % |
| net | 30,98 bps/j | 24,31 bps/j |
| coût / brut | 12 % | 11 % |
| t Newey-West | +4,06 | **+2,40** |

Un test pré-enregistré, direction pré-spécifiée : p ≈ 0,008 unilatéral, 0,016
bilatéral. **78 % du point estimé conservé** hors échantillon.

### Robustesse

- **Coût** : point mort à ~124 bps aller-retour, **9× l'hypothèse**. À 56 bps il
  reste 2,28 %/mois, Sharpe 1,04. (Le coût de 14 bps *est* une hypothèse : aucune
  source de profondeur ou de spread n'existe avant juillet 2026.)
- **Capacité** : plat jusqu'à 2 M$, tient à 5 M$, casse à 10 M$ (univers réduit à
  50 noms, t = 1,20). Vingt-cinq fois l'encours actuel.
- **Paramètres** : surface plate entre top_n 90 et 150 avec panier 8 (21 à 29 bps).
  Panier 12 systématiquement moins bon.
- **Pas de décroissance** : 32,8 / 22,4 / 27,7 / 22,3 / 22,2 bps/j pour
  2022 / 2023 / 2024 / 2025 / 2026-S1.
- **Mais volatil à six mois** : les 12 derniers mois ne rendent que 10,95 bps/j
  (1,68 %/mois), le S2-2025 ayant été quasi plat.

### Ce que ce n'est pas

- **Ce n'est pas « plusieurs » alphas.** `lsr_ttacct_x` est corrélé à **0,971**
  avec `lsr_globacct_x` : c'est la même série, pas un second signal.
- **La moitié de l'edge est du momentum.** `corr(xrank(L/S), xrank(rendement 60j))
  = −0,47`. Sous `allf` (bêta + taille + vol + rendements 5/20/60 j retirés), le
  `t` passe de 4,06 à 3,00 et le net de 31 à 15 bps/j. La moitié résiduelle n'est
  pas du momentum — c'est elle, l'alpha.
- **Ce n'est pas « ultra solide ».** En échantillon, `t = 4,06` contre un plancher
  de bruit à 3,88 (p95) : un tirage nul sur vingt a fait mieux. La confirmation
  hors échantillon est ce qui porte la conclusion, pas la recherche.

## Fichiers

- `tools/build_daily_cache.py` — agrège les trois sources en `data/_cache/panel_daily.npz`
- `tools/alpha_sweep_v4.py` — le harnais (`search` / `placebo` / `confirm`)
- `tools/placebo_mp.py` — seuil empirique parallélisé
- `tools/candidate_report.py` — comptabilité mensuelle
- `tools/deriv_signals.py` — bibliothèque étendue de dérivés (itération 2)
- `reports/edge_discovery/sweep_v4/prereg.json` — pré-enregistrement scellé
