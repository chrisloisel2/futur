# calendar_basis_v1 — falsification du prior art basis_term_v0

> ## ❌ VERDICT : BASIS_TERM_V0_PBO_FAIL (2026-07-21)
>
> Falsification indépendante d'`entry5_size50` (runner paper live
> `basis_term_v0`, backtest du 2026-07-13, +8,39 %/an rapporté) sous les
> gates edge_factory complets : DSR = 0,9975 (PASS), coûts ×2 net
> +7,47 %/an (PASS), leave-one-year (PASS), leave-one-asset BTC/ETH
> ~45/45 % (PASS), holdout 2025-2026 positif mais sous-alimenté (7 trades).
> **PBO (CSCV, 6 variantes) = 0,47 — très au-dessus du seuil ≤ 0,10.** La
> sélection a posteriori du primaire parmi 6 variantes est indiscernable du
> hasard. Un seul gate qui échoue suffit à rejeter le candidat figé.
> Détail : [results/BASIS_TERM_V0_FALSIFICATION_2026-07-21.md](results/BASIS_TERM_V0_FALSIFICATION_2026-07-21.md).

Cette piste a découvert, à l'étape 3 (inventaire données), qu'elle n'était
pas un terrain vierge : le carry trimestriel BTC/ETH Binance (cash-and-carry
calendaire) était déjà backtesté et tournait déjà en paper live sous le nom
`basis_term_v0` avant même la préinscription de cette piste. Décision
humaine du 2026-07-21 : ne pas reconstruire un moteur parallèle, mais
soumettre le résultat existant à une falsification indépendante et
complète — ce qui a été fait ci-dessus.

## Documents

- [PREREGISTRATION.md](PREREGISTRATION.md) — thèse et mécanisme, écrits
  avant tout accès aux données.
- [DATA_INVENTORY.yaml](DATA_INVENTORY.yaml) — étape 3, inventaire des
  données réellement disponibles, avec la découverte du prior art.
- [results/BASIS_TERM_V0_FALSIFICATION_2026-07-21.md](results/BASIS_TERM_V0_FALSIFICATION_2026-07-21.md)
  et son détail JSON — la falsification ci-dessus.

## Prochaine étape

Aucune nouvelle variante calendar basis tant que le résultat n'a pas passé
DSR, PBO, coûts ×2, leave-one-year et un holdout indépendant — c'est
maintenant fait, et le résultat est négatif sur PBO. La décision sur le
statut opérationnel de `basis_term_v0` (pause, reclassification, statut
inchangé) reste à prendre par l'humain ; cette piste ne le décide pas.
