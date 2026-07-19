# Carry Return Reconciliation (Phase 40)

Réconcilie les deux chiffres carry qui semblaient incohérents :
- **+0.37 %/mois** = BTC carry standalone (`backtest_carry_basis.py`, always-on, **notional plein**)
- **+6.3 % sur 4.5 ans** (≈ +0.12 %/mois) = sleeve carry **dans le portefeuille** (gated, **sizing 20 %**)

## Table de réconciliation

| Métrique | Valeur |
|---|---:|
| BTC carry brut/mois (always-on, 100% notional) | +0.37% |
| ETH carry brut/mois (always-on, 100% notional) | +0.34% |
| **Portfolio carry sleeve total (4.5 ans)** | **+6.3%** |
| Portfolio carry sleeve ret/mois (médiane) | +0.078% |
| Taille moyenne utilisée (carry_fraction) | 20% de l'equity / asset |
| Funding reçu (PnL, 10k capital) | +1152 |
| Fees (maker, 2 jambes) | −522 |
| Borrow cost | −1.3 |
| Basis PnL | **non modélisé** (pas de feed basis — documenté) |
| Net PnL sleeve | +630 (sur 10 000 → +6.3%) |

## Explication de l'écart 0.37% → 0.12%

Le facteur dominant est le **sizing**, pas une erreur :

```
0.37%/mois (BTC, notional plein)
× 0.20 (carry_fraction)                 → 0.074%/mois
+ blend ETH (0.34%) et gating funding   → ~0.08-0.12%/mois observé
```

- **Sizing 20 %** : le carry n'utilise que 20 % de l'equity par asset (×0.20 ≈ 5× moins).
- **Gating funding** : actif seulement en FUNDING_POSITIVE_STABLE (temps en marché réduit) — protège
  du flip (le stress −1σ cassait l'always-on) au prix d'un rendement moindre.
- **Maker fees + borrow** : −522 / −1.3, soustraits proprement (PnL séparés).
- **Basis omis** : conservateur (le basis ajouterait potentiellement du rendement).

## Conséquence

Les deux chiffres sont **cohérents** : +0.37 %/mois est le rendement brut BTC à notional plein ;
+0.12 %/mois est le rendement net du sleeve à 20 % de taille, gaté et après coûts.

→ Le carry est **réel et propre** mais **petit à cette taille**. Pour qu'il pèse, il faudrait
soit augmenter le sizing (le delta-neutral le permet, DD 0.5 %), soit ajouter ETH proprement.
Réconciliation faite → **CARRY_SLEEVE peut passer PAPER_CANDIDATE → PAPER** (chiffres traçables).
