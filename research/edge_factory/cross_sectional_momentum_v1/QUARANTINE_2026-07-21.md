# QUARANTINE — MOMENTUM_CRYPTO_V1 verdict retiré

> **✅ AUDIT TERMINÉ le 2026-07-22 — voir
> [results/MOMENTUM_CRYPTO_V1_PIT_FINAL_VERDICT_2026-07-22.md](results/MOMENTUM_CRYPTO_V1_PIT_FINAL_VERDICT_2026-07-22.md)
> pour le verdict final.** Tous les défauts listés ci-dessous ont été
> corrigés et re-vérifiés (exécution, poids, univers, invariants). Le
> nouveau verdict est NO_EDGE propre (Sharpe ≈ 0), PAS le reversal
> significatif rapporté avant la mise en quarantaine.

```yaml
cross_sectional_momentum_crypto_v1:
  previous_status: NO_EDGE
  current_status: QUARANTINED_IMPLEMENTATION_AUDIT
  reasons:
    - execution_alignment_not_proven
    - current_survivor_universe_not_valid_for_family_level_verdict
    - long_short_accounting_not_independently_reconciled
    - beta_residualization_and_weight_neutralization_not_yet_audited
  forbidden:
    - runner_creation
    - signal_inversion
    - parameter_retuning
    - family_level_no_edge_claim
```

Le verdict `MOMENTUM_CRYPTO_V1_NO_EDGE` du 2026-07-21
(`948cb26`, `README.md`, `results/MOMENTUM_CRYPTO_V1_VERDICT_2026-07-21.md`)
est **retiré comme verdict de famille**. Il reste vrai que *cette
implémentation précise* (univers = snapshot du 2026-06-30 appliqué à tout
l'historique, exécution close→close décalée d'un jour, résidualisation bêta
non auditée mécaniquement) a produit un résultat négatif — mais rien ne
prouve encore que ce résultat parle du momentum crypto en général plutôt que
d'un artefact de méthode.

## Défaut confirmé avant toute autre vérification : univers survivant

Vérifié immédiatement (pas supposé) : l'univers `CRYPTO_32` utilisé dans
`backtest_momentum_crypto_v1.py` vient de `build_membership()` **évalué une
seule fois, à la barre du 2026-06-30**, puis appliqué tel quel sur toute la
période 2020-2026. C'est exactement le biais déjà nommé et corrigé une fois
dans ce même dépôt (CTREND v0 → v1, `859ebad`) : l'univers d'aujourd'hui
appliqué au passé. La jambe short ne pouvait shorter que des noms qui ont
survécu jusqu'à aujourd'hui parmi le top-50 actuel — jamais les vrais
perdants historiques désormais délistés. Ce défaut, à lui seul, invalide un
verdict de famille dans les deux sens (il aurait tout aussi bien pu produire
un faux positif qu'un faux négatif).

## Ce qui reste à faire avant tout nouveau verdict

Voir les commits suivants (`fix execution alignment`, `sign-symmetry +
PnL identity tests`, `portfolio invariants`, `restore full PIT universe`,
`rerun unchanged formula once`). Aucune retouche de paramètres, aucune
inversion de signe, aucun nouveau runner tant que cet audit n'est pas
terminé.
