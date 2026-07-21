# cross_sectional_momentum_v1 (MOMENTUM_CRYPTO_V1)

> ## 🔶 QUARANTAINE (2026-07-21) — le verdict NO_EDGE ci-dessous est RETIRÉ
>
> Défaut confirmé : l'univers `CRYPTO_32` était un **snapshot du
> 2026-06-30 appliqué à tout l'historique 2020-2026** — exactement le
> biais de survivance déjà corrigé une fois dans ce dépôt (CTREND v0→v1).
> La jambe short ne pouvait shorter que des survivants du top-50 actuel,
> jamais les vrais perdants historiques délistés. Ce défaut invalide le
> verdict de famille dans les deux sens. Détail :
> [QUARANTINE_2026-07-21.md](QUARANTINE_2026-07-21.md). Audit et
> reconstruction en cours avant tout nouveau verdict — aucune retouche de
> paramètres, aucune inversion de signe.

> ## ~~❌ VERDICT (RETIRÉ) : MOMENTUM_CRYPTO_V1_NO_EDGE — REVERSAL, PAS MOMENTUM (2026-07-21)~~
>
> ~~Formule unique préenregistrée (0,4×mom7j + 0,4×mom30j + 0,2×mom90j −
> pénalité illiquidité − coût funding, résiduel bêta BTC), univers
> crypto-only 32 noms, rebalance quotidien. CAGR net −66,9 %/an, Sharpe
> −1,22, maxDD −99,9 %, toutes années négatives, coûts ×2 négatif.~~
> Ce verdict reposait sur l'univers survivant décrit ci-dessus — voir la
> quarantaine. Détail historique (ne plus citer comme preuve) :
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
