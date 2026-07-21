# Addendum — MOMENTUM_CRYPTO_V1 (2026-07-21, décision humaine)

Écrit après la découverte du 2026-07-21 (voir `DATA_INVENTORY.yaml`,
`update_2026-07-21_funding_expansion_and_verification`) : ~20 des 50
symboles de l'univers étendu par volume sont des perps tokenisés
actions/ETF/commodités Binance (NVDAUSDT, MSTRUSDT, XAUUSDT, QQQUSDT...),
pas des cryptos. Mélanger ces deux familles dans un même classement
momentum fausse la neutralisation bêta/secteur : un perp tokenisé Nasdaq
ne partage pas les mêmes facteurs de risque qu'un altcoin.

Décision : scinder en deux expériences distinctes, le primaire devient
**crypto-only**.

```yaml
experiment_id: cross_sectional_momentum_v1   # aka MOMENTUM_CRYPTO_V1
universe_filter: crypto-only — exclut tout perp tokenisé action/ETF/commodité
  (liste précise dans DATA_INVENTORY.yaml, update_2026-07-21_universe_split,
  vérifiée via l'exchangeInfo Binance, pas devinée depuis la mémoire)
sibling_experiment: momentum_tokenized_macro_v1   # déprioritisé, voir son propre dossier
```

## Formule unique préenregistrée (aucune grille)

```text
score_i =
  0,4 × momentum_résiduel_7j_i
+ 0,4 × momentum_résiduel_30j_i
+ 0,2 × momentum_résiduel_90j_i
− pénalité_illiquidité_i
− coût_funding_attendu_i
```

`momentum_résiduel` = rendement de l'actif sur la fenêtre, moins sa
contribution attendue via son bêta glissant à BTC (régression glissante
rendement_i ~ rendement_BTC) — c'est délibérément la même chose que
« score − bêta BTC » demandé : un actif à bêta élevé voit son momentum brut
déjà réduit par la résidualisation, on ne soustrait pas un second terme
bêta séparé pour éviter un double comptage. La neutralisation bêta au
niveau PORTEFEUILLE (pas seulement au niveau du score) est une étape
distincte de construction (voir ci-dessous) — les deux ne se substituent
pas l'une à l'autre.

## Construction du portefeuille

```text
1. classement par score_i sur l'univers crypto-only du jour (PIT)
2. long top 20 % (equal-count), short bottom 20 % (equal-count)
3. pondération inverse-volatilité dans chaque jambe
4. cap de concentration : aucun actif > 15 % de l'exposition brute d'une jambe
5. hedge BTC explicite pour ramener le bêta net du portefeuille à ~0
   (mesuré, pas seulement supposé nul par construction long-short)
6. rebalance quotidien (00:00 UTC) — pas de test 8h dans cette v1,
   pour rester sur la même cadence que les données PIT quotidiennes déjà
   vérifiées (um_klines_1d) sans introduire un second grain de données
```

## Coûts

Frais taker Binance (assumed, `fee_registry`), slippage (assumed default),
coût de funding réel archivé sur la jambe short (Binance uniquement —
c'est la seule venue avec une couverture funding suffisamment large pour
cet univers), coût de rebalancement (turnover × coûts d'exécution).

## Gates (préenregistrés avant tout calcul)

```text
CAGR net > 12 %/an
Sharpe > 1,2
maxDD < 15 %
coûts ×2 positif
leave-one-year positif
aucun actif > 15 % du PnL total
DSR > seuil du programme (0,95)
PBO : non calculé — une seule formule testée (n_trials=1), aucune grille ;
      gate_research() saute PBO quand trials_matrix=None, comportement
      correct ici, pas une omission
```

## Ce que ce document ne fait pas

Ne re-teste pas l'univers étendu (crypto + tokenisé) — cette version est
`SUPERSEDED` par ce document pour toute décision de production. Le sleeve
tokenisé macro est étudié séparément, sans urgence, dans
`../momentum_tokenized_macro_v1/`.
