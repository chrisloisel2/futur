# Multi-horizon ensemble — mesuré (cascade, walk-forward, net de frais)

## Corrélation des probabilités inter-horizons

|          |   p_fwd_1h |   p_fwd_4h |   p_fwd_8h |
|:---------|-----------:|-----------:|-----------:|
| p_fwd_1h |      1     |      0.582 |      0.466 |
| p_fwd_4h |      0.582 |      1     |      0.765 |
| p_fwd_8h |      0.466 |      0.765 |      1     |

_Si ~1.0 → les horizons disent la même chose, diversification illusoire._


## Résultats à horizon de trade commun (4h)

| config | n | PF | mean bps | WR |
|---|---:|---:|---:|---:|
| A · single 4h (ACTUEL) | 6284 | 1.203 | +23.2 | 0.527 |
| B · ensemble (moy 3) | 6283 | 1.273 | +31.1 | 0.532 |
| C · consensus (3 d'accord) | 2540 | 1.443 | +55.7 | 0.551 |
| D · term-structure exit | 6284 | 1.373 | +47.4 | 0.534 |

**Verdict** : meilleur = C · consensus (3 d'accord) (PF 1.443 vs baseline 1.203). L'ensemble AIDE.