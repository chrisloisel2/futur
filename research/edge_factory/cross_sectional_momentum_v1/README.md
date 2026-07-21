# cross_sectional_momentum_v1 (MOMENTUM_CRYPTO_V1)

> ## ❌ VERDICT : MOMENTUM_CRYPTO_V1_NO_EDGE — REVERSAL, PAS MOMENTUM (2026-07-21)
>
> Formule unique préenregistrée (0,4×mom7j + 0,4×mom30j + 0,2×mom90j −
> pénalité illiquidité − coût funding, résiduel bêta BTC), univers
> crypto-only 32 noms, rebalance quotidien. CAGR net −66,9 %/an, Sharpe
> −1,22, maxDD −99,9 %, toutes années négatives, coûts ×2 négatif. Ce
> n'est pas un artefact de coûts : le diagnostic IC quotidien (corrélation
> de rang signal → rendement du lendemain, univers complet) est **négatif
> et significatif à l'horizon 7 jours (t = −3,18)** — le signal est un
> reversal court terme, pas un momentum, à cette fréquence. Détail :
> [results/MOMENTUM_CRYPTO_V1_VERDICT_2026-07-21.md](results/MOMENTUM_CRYPTO_V1_VERDICT_2026-07-21.md).

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
- [backtest_momentum_crypto_v1.py](backtest_momentum_crypto_v1.py) —
  étapes 4-7, formule unique, `n_trials=1`.
- [results/MOMENTUM_CRYPTO_V1_BACKTEST_2026-07-21.json](results/MOMENTUM_CRYPTO_V1_BACKTEST_2026-07-21.json) —
  chiffres bruts.
- [results/MOMENTUM_CRYPTO_V1_VERDICT_2026-07-21.md](results/MOMENTUM_CRYPTO_V1_VERDICT_2026-07-21.md) —
  verdict complet + diagnostic IC.

## Prochaine étape

Aucune nouvelle variante lancée immédiatement. Piste ouverte, non
poursuivie maintenant : momentum classique construit sur rendements
mensuels avec période de "skip" (littérature Jegadeesh-Titman), pour
éviter la contamination par le reversal court terme identifié ici — une
thèse différente, pas un retuning des mêmes seuils sur la même fréquence.
