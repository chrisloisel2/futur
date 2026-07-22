# cross_sectional_momentum_v1 (MOMENTUM_CRYPTO_V1)

> ## 🔒 STATUT : CLOSED_NO_EDGE (2026-07-22, décision humaine, gelé définitivement)
>
> Formule (poids 0,4/0,4/0,2, horizons 7/30/90j, signe résiduel bêta BTC)
> **gelée** : aucun changement de poids, d'horizon ou de signe pour la
> sauver. Réouverture seulement sur thèse structurellement différente
> avec son propre préenregistrement — pas un retuning de celle-ci.
> Registre : `configs/alpha20.yaml` →
> `experiment_registry.closed_no_edge.cross_sectional_momentum_crypto_v1`.

> ## ❌ VERDICT FINAL (post-audit) : MOMENTUM_CRYPTO_V1_NO_EDGE — CLEAN, PAS UN REVERSAL (2026-07-22)
>
> Après l'audit complet du 2026-07-21 (`QUARANTINE_2026-07-21.md` — moteur
> d'exécution corrigé, poids water-filling, univers point-in-time réel de
> 311 symboles reconstruit, funding réel backfillé, invariants vérifiés à
> zéro violation sur 2254 jours), la formule unique préenregistrée donne :
> **CAGR −20,4 %/an, Sharpe ≈ 0 (−0,04), DSR 0,46, maxDD −87,8 %**, PnL
> annuel très bruité (de +113 % à −89 % selon l'année). Tous les gates
> économiques échouent, mais **le verdict "reversal" du 2026-07-21 est
> retiré** : c'était un artefact du même univers survivant et de la même
> exécution mal alignée que ce qui a motivé l'audit. Sur le moteur
> corrigé, il n'y a ni momentum ni reversal détectable — juste du bruit.
> Détail : [results/MOMENTUM_CRYPTO_V1_PIT_FINAL_VERDICT_2026-07-22.md](results/MOMENTUM_CRYPTO_V1_PIT_FINAL_VERDICT_2026-07-22.md).

## Historique de l'audit (2026-07-21 → 2026-07-22)

1. Premier run (univers `CRYPTO_32`, snapshot du 2026-06-30) → NO_EDGE,
   diagnostiqué comme "reversal" (IC quotidien négatif significatif).
2. Mise en quarantaine (`QUARANTINE_2026-07-21.md`) : univers survivant,
   exécution non prouvée, comptabilité long-short non auditée.
3. Corrections, une par une, chacune testée indépendamment :
   exécution open-to-open (délai réel 2 jours), cap sans violation, tests
   de symétrie de signe et d'identité comptable (10 tests), invariants
   quotidiens (a trouvé et fait corriger un vrai bug de neutralité dollar
   via water-filling), univers PIT réel (311 noms vs 32), funding
   backfillé pour tous.
4. Rerun final unique, formule inchangée → verdict ci-dessus.

Univers scindé le 2026-07-21 : voir
[PREREGISTRATION_CRYPTO_V1_ADDENDUM.md](PREREGISTRATION_CRYPTO_V1_ADDENDUM.md)
(primaire, crypto-only) et
[../momentum_tokenized_macro_v1/](../momentum_tokenized_macro_v1/)
(déprioritisé, perps tokenisés actions/ETF/commodités).

## Documents

- [PREREGISTRATION.md](PREREGISTRATION.md) — thèse d'origine.
- [PREREGISTRATION_CRYPTO_V1_ADDENDUM.md](PREREGISTRATION_CRYPTO_V1_ADDENDUM.md) —
  formule unique, univers crypto-only, gates.
- [DATA_INVENTORY.yaml](DATA_INVENTORY.yaml) — étape 3, inventaire +
  extension funding (50 puis split 33 crypto/17 macro).
- [QUARANTINE_2026-07-21.md](QUARANTINE_2026-07-21.md) — audit complet,
  défauts trouvés et corrigés.
- [momentum_engine.py](momentum_engine.py) — fonctions pures testées
  (`tests/test_momentum_engine.py`, 10 tests).
- [build_pit_universe.py](build_pit_universe.py) — reconstruction de
  l'univers point-in-time (311 symboles).
- [backtest_momentum_crypto_v1.py](backtest_momentum_crypto_v1.py) —
  étapes 4-7, formule unique, `n_trials=1`, univers PIT.
- [results/MOMENTUM_CRYPTO_V1_PIT_FINAL_2026-07-22.json](results/MOMENTUM_CRYPTO_V1_PIT_FINAL_2026-07-22.json) —
  chiffres bruts du run final.
- [results/MOMENTUM_CRYPTO_V1_PIT_FINAL_VERDICT_2026-07-22.md](results/MOMENTUM_CRYPTO_V1_PIT_FINAL_VERDICT_2026-07-22.md) —
  verdict final complet.
- Runs antérieurs (historique, ne plus citer comme preuve) :
  [results/MOMENTUM_CRYPTO_V1_BACKTEST_2026-07-21.json](results/MOMENTUM_CRYPTO_V1_BACKTEST_2026-07-21.json),
  [results/MOMENTUM_CRYPTO_V1_VERDICT_2026-07-21.md](results/MOMENTUM_CRYPTO_V1_VERDICT_2026-07-21.md),
  [results/MOMENTUM_CRYPTO_V1_ENGINE_VALIDATION_2026-07-22.json](results/MOMENTUM_CRYPTO_V1_ENGINE_VALIDATION_2026-07-22.json).

## Prochaine étape

Aucune nouvelle variante lancée immédiatement. Piste ouverte, non
poursuivie maintenant : momentum classique construit sur rendements
mensuels avec période de "skip" (littérature Jegadeesh-Titman) — une
thèse différente, avec son propre préenregistrement, pas une extension
de celui-ci.
