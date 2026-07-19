# Portfolio V1 Report — multi-leg (Phase 37-38, 2026-06-28)

Branche `feat/portfolio-multileg`. Backtester multi-jambes **comptablement correct**
(LONG_SPOT + DELTA_NEUTRAL_CARRY + PORTFOLIO_HEDGE), PnL décomposé, funding accrual,
carry gaté par régime de funding, hedge lié au book long, invariants no-naked-short.
67/67 tests (dont 19 multileg/carry/hedge accounting).

## Ablation 2022-2026 (`reports/multileg_ablation_2022_2026.json`)

| Run | ROI | PF | ret/mo | maxDD | PnL [dir / carry / hedge / fees] |
|---|---:|---:|---:|---:|---|
| A long | −30.5% | 0.82 | −0.42% | −31.9% | [−506 / 0 / 0 / −2546] |
| B long+carry | −26.2% | 0.94 | +0.11% | −31.7% | carry **+846** |
| C long+hedge | −33.2% | 0.81 | −0.43% | −33.6% | hedge −31 |
| D long+carry+hedge | −28.6% | 0.93 | −0.19% | −31.2% | carry +843, hedge +14 |
| **E carry seul** | **+6.3%** | **1.02** | +0.08% | **−0.5%** | carry **+1152**, fees −522 |
| F carry+hedge | +6.3% | 1.02 | +0.08% | −0.5% | hedge inactif (pas de long) |
| **G hedge seul** | 0.0% | — | 0% | 0% | **0 trade (no naked short)** ✅ |

## Lectures

1. **A_long −30.5% ≈ regime report −31%** → comptabilité multi-leg validée (cohérence croisée).
2. **Carry delta-neutral = seul sleeve positif** : E +6.3% sur 4.5 ans, PF>1, **maxDD 0.5%**,
   non-directionnel, gaté par funding (FUNDING_POSITIVE_STABLE). Price legs s'annulent, PnL = funding.
3. **Carry améliore le book long** : B (−26.2%) > A (−30.5%) → +846 de funding amortit les pertes longs.
4. **Hedge = neutre/coûteux en full-cycle** : il protège en bear mais coûte en bull → net wash 2022-2026.
   C'est une assurance, pas un générateur d'alpha (attendu).
5. **G prouve l'invariant clé** : le hedge ne trade JAMAIS sans long (aucun short nu).

## Gates Phase 38

| Gate | Cible | Résultat |
|---|---|---|
| Carry net | BTC > 0.25%/mo, DD<1.5% | E carry DD 0.5% ✓, ret/mo +0.08% (sous 0.25% une fois gaté) |
| Hedge | DD −20%, ROI −<10% | hedge neutre full-cycle (utile en bear seulement) |
| Portefeuille D | ROI méd ≥1%, DD≤3%, PF≥1.30 | **FAIL** (D −28.6%, longs dominent) |

## Verdict — PAS `PAPER_PORTFOLIO_V1` pour le système complet

Le système D (long+carry+hedge) reste **négatif full-cycle** : les longs saignent en
bear/mixed (2022/2025/2026) et dominent le PnL. Le carry est trop petit (+6.3%/4.5 ans)
pour compenser. Donc **micro-live interdit**, conforme aux règles.

**Ce qui est promotable** :
```
CARRY_SLEEVE          : PAPER_CANDIDATE (delta-neutral, +6.3% 4.5y, DD 0.5%, gaté funding)
HEDGE_GOVERNOR_V1     : PAPER_ONLY (insurance, invariants prouvés)
LONG_BOOK             : RÉGIME-GATED REQUIS (ne trader qu'en bull/recovery)
MULTILEG_ACCOUNTING   : VALIDÉ (67 tests)
PORTFOLIO_V1 (D)      : FAIL — pas de micro-live
```

## Prochaine étape décisive (la vraie)

Le diagnostic est désormais sans ambiguïté : **le book long doit être régime-gaté**
(actif seulement quand le régime BTC est bull/recovery — où il a fait +6.3% en 2024).
En l'état il tourne dans tous les régimes et détruit le carry. Séquence :
1. Régime-gate du long book (n'ouvrir des longs que si btc_regime ∈ {bull, recovery}).
2. Re-run D régime-gaté : long(bull) + carry(always, gaté funding) + hedge(bear).
3. Si D régime-gaté > 0 full-cycle avec DD ≤ 3% → PAPER_PORTFOLIO_V1.
Sinon : le carry seul devient le premier sleeve paper, et il faut une vraie source
d'alpha convexe (event/liquidation avec vrai feed OI) pour viser 3-5%/mois.
