# Examen de Bitfinex — refusée à la condition 3, n = 1 consommé

*2026-09-09. Règle appliquée : `SOURCE_SELECTION_RULE` (branche `prereg/source-selection-rule`,
commit `e765081`), écrite avant l'inventaire. Aucune donnée Bitfinex chargée : seuls des
horodatages et des codes HTTP ont été lus.*

| condition | verdict |
|---|---|
| 1 — accès libre, granularité quotidienne | **passe** — `api-pub.bitfinex.com` v2 sans clé, 1 m → 1 M |
| 2 — profondeur `T ≥ 2,24` ans à n = 1 | **passe** — 2016-07-31 → 2026-09-09, 10,1 ans |
| 3 — un mécanisme qu'aucun script commité n'a mesuré | **échoue** |

## Pourquoi la condition 3 échoue structurellement

`fUSD` est le pool de prêt USD qui sert la marge spot de *toutes* les paires : une série
unique dont le seul contenu économique est **le prix du levier spot**. Quel que soit
l'habillage, le payeur est *le long à effet de levier qui emprunte* — et c'est le payeur le
plus mesuré du dépôt :

- **en niveau** : 12 signaux funding dans le balayage (`fund_lvl_x`, `fund_cum*`, `fund_z60`,
  `carry_crowded`, …), `backtest_funding_extreme.py`, `measure_funding_timing.py` ;
- **en écart cross-venue** : `backtest_funding_rv_v1.py`, `run_a2rv_backtest.py`,
  `test_funding_xvenue_v0.py` ;
- **en structure par terme** : `backtest_basis_term.py` (carry de structure par terme,
  cash-and-carry trimestriel).

La variante envisagée — inversion du terme `p2` contre `p30` comme signal de stress — n'est
pas un mécanisme neuf : c'est un instrument neuf sur le même payeur. La retenir aurait été la
rationalisation que la règle existe pour empêcher. **Aucune seconde hypothèse Bitfinex n'a
été formée.**

## Conséquence

Une source examinée = un essai. `n = 1` consommé. CME est examinée ensuite à `n = 2` :
seuil unilatéral `threshold_t(2) = 1,9600`, profondeur requise `T ≥ 2,85` ans.
