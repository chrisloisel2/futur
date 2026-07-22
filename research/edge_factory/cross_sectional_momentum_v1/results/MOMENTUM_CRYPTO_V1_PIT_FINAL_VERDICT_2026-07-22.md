# Verdict final — MOMENTUM_CRYPTO_V1 (post-audit, univers PIT)

Rerun unique, formule inchangée depuis le début (0,4×mom7j + 0,4×mom30j +
0,2×mom90j − pénalité illiquidité − coût funding, résiduel bêta BTC),
**moteur et univers corrigés** suite à l'audit du 2026-07-21
(`QUARANTINE_2026-07-21.md`) :

- exécution open-to-open, délai réel de 2 jours (pas close-to-close avec
  décalage d'1 jour) ;
- pondération water-filling (cap 15 % jamais dépassé, neutralité dollar
  exacte quand faisable) ;
- univers point-in-time réel : 311 symboles crypto classés (delistés
  compris), reconstruit par `build_pit_universe.py` — plus le snapshot
  figé de 32 noms du 2026-06-30 ;
- funding réel backfillé pour les 311 symboles (contre 33 avant) ;
- invariants quotidiens vérifiés : **`invariant_violations: {}`** sur les
  2254 jours (exposition brute, cap par nom, neutralité dollar, bêta
  portefeuille, aucun rendement ≤ −100 %).

Détail brut : [MOMENTUM_CRYPTO_V1_PIT_FINAL_2026-07-22.json](MOMENTUM_CRYPTO_V1_PIT_FINAL_2026-07-22.json).

## Résultat

| Gate | Valeur | Seuil | Verdict |
|---|---|---|---|
| CAGR net ×1 | −20,4 %/an | > 12 % | ❌ FAIL |
| Sharpe ×1 | **−0,04** | > 1,2 | ❌ FAIL |
| maxDD | −87,8 % | < 15 % | ❌ FAIL |
| Coûts ×2 | −27,6 %/an | net positif | ❌ FAIL |
| Leave-one-year | toutes années négatives (−5 % à −34 %) | positif | ❌ FAIL |
| Concentration max par actif | 3,3 % | ≤ 15 % | ✅ PASS |
| DSR | 0,46 | ≥ 0,95 (programme) / > 0 (littéral) | ⚠️ PASS littéral, FAIL réel |

Par année, le PnL brut oscille violemment : +10 % (2020), −14 % (2021),
−60 % (2022), −35 % (2023), **+113 % (2024)**, +44 % (2025), −89 % (2026).
Ce n'est pas un déclin régulier ni un effet de reversal cohérent — c'est
une série bruitée, dominée par des années individuelles extrêmes.

## Ce qui a changé par rapport aux runs précédents (et pourquoi c'est important)

| | CRYPTO_32 (biaisé, 2026-07-21) | Univers PIT (2026-07-22) |
|---|---|---|
| Univers | 31 noms fixes, snapshot du 2026-06-30 | 311 noms, réellement actifs à chaque date |
| Sharpe ×1 | −1,22 | **−0,04** |
| DSR | 0,0006–0,0013 | 0,46 |
| Concentration max | 12,7–18,7 % | 3,3 % |
| Invariants | violations trouvées et corrigées en cours de route | 0 violation |

Le Sharpe est passé de −1,22 (un signal fortement et significativement
négatif) à **−0,04 (statistiquement indiscernable de zéro)**. Le verdict du
2026-07-21 ("c'est un reversal, pas un momentum", `t = −3,18` sur l'IC
quotidien) était lui-même construit sur ce même univers survivant biaisé
et cette même exécution mal alignée — **ce verdict est retiré**, pas
seulement le NO_EDGE : l'affirmation qu'il existe un effet de reversal
significatif à cette fréquence n'est plus soutenue une fois le moteur et
l'univers corrigés. Il n'a jamais été retesté proprement sur l'univers
PIT ; ne pas le citer comme un résultat établi.

## Verdict

**NO_EDGE — mais un NO_EDGE propre, pas un signal inversé.** Sur le
moteur et l'univers corrigés, la formule ne montre ni momentum ni
reversal détectable : Sharpe ≈ 0, DSR trop bas, drawdown extrême porté
par une poignée d'années individuelles plutôt qu'une tendance
systématique. C'est le résultat honnête d'une formule simple (première
version volontairement non sophistiquée, sans période de skip, rebalance
quotidien) sur un vrai facteur de risque bruité — pas la preuve que le
momentum crypto n'existe sous aucune forme.

## Ce que ce document ne fait pas

Ne propose aucune nouvelle variante, ne retouche aucun paramètre. La
piste ouverte déjà identifiée (momentum mensuel avec période de skip,
littérature Jegadeesh-Titman) reste non lancée — elle nécessiterait son
propre préenregistrement, pas une extension de celui-ci.
