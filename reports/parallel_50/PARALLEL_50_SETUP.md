# V1.1_PARALLEL_50_RESEARCH — setup (2026-06-28)

> 50 cryptos = univers de SURVEILLANCE, pas 50 tradées. Top 3-7 sélectionnées.
> Risque inchangé, ledgers séparés, baseline officielle V1.1 INTOUCHÉE. Capital réel = 0.

## Machinerie de sélection construite (testée 8/8)
- `universe/asset_quality_filter.py` : PASS/WARN/BLOCK par actif (données valides+récentes+coverage).
- `risk/correlation_buckets.py` : 10 buckets (majors/sol_beta/eth_l2/ai/defi/memes/legacy/infra/...).
- `portfolio/opportunity_ranker.py` : tri par score → top-k sous caps (max 7 total, 5 alts, 2/bucket, 1 meme, no BLOCK).
- `configs/portfolio_v1_1_parallel_50.yaml` : real_capital=0, carry BTC/ETH only, carry_gate_v2=off,
  liquidation=off, ledgers séparés `artifacts/paper_live/v1_1_parallel_50`.

## Réalité data (honnête)
| | n |
|---|---|
| Univers surveillé | 50 |
| **PASS (données valides → tradable)** | **9** (BTC ETH SOL BNB XRP DOGE ADA AVAX LINK) |
| **BLOCK (NO_DATA)** | **41** (LTC BCH DOT NEAR OP ARB … PEPE WIF …) |

→ **41/50 n'ont aucun historique** : non backtestables, non tradables. La question "50 augmente-t-il
le rendement ?" est **DATA-GATED** pour 41 actifs. Seuls 9 sont tradables aujourd'hui.

## Action : collecteur étendu à 50
`futur-derivatives.service` étendu de 9 → 50 symboles (REST 300s gentle, WS forceOrder all-symbols).
Commence à accumuler OI/funding/liquidations pour les 41 manquants. L'enriched (features/prix) de ces
41 reste à construire (backfill Binance Vision klines — étape future) avant tout backtest/paper.

## Prochaines étapes honnêtes
1. Collecteur 50 accumule (semaines) + backfill klines des 41 (Binance Vision) → enriched.
2. Quand un actif atteint PASS (coverage 30j) → quality filter le promeut → entre dans le ranking.
3. Backtest parallel-50 sur les actifs réellement PASS (pas de backtest silencieux sur données absentes).
4. Paper-research séparé (capital réel 0) quand ≥ ~15-20 actifs PASS.

**STATUT : framework prêt ; tradable=9 ; 41 en accumulation. PARALLEL_50 = DATA_GATED.**
