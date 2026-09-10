# forced_liquidation_reaction_v1 — preregistered first look (P3B)

Written and pushed BEFORE any price join. Two hypotheses, one look, one verdict each. Budget 1 → 0
at the look. Family `liquidation` (research kernel: 0 sealed before) → threshold_t(2) = 1.9600 one-sided.
The loop's lifetime bar (threshold_t(23) ≈ 2.87) is reported, not applied.

## Frozen inputs

- Forced-flow tape frozen by `first_look_gate.py --freeze forced_flow forced_liquidation_reaction_v1` at
  2026-09-10T19:54:31+00:00: **24930 liquidations**, sha256 `b78d1b509b9ceddf3b2a07771e687ac07aea23cfc1b8a9db7dd94f5819442758`,
  events 2026-09-10T11:12:49 → 2026-09-10T19:54:06 UTC (one session; Binance 19 583, Bybit 5 347).
- Gate at freeze: n_events 24930, enriched 0.9994, min age 0 ms, negative ages 0,
  stream delay measured (median 1009 ms), all return fields null: **open**.
- Universe `reports/first_look/forced_liquidation_reaction_v1_UNIVERSE.json`, sha256 `a61be7254e2877396e9fbb817a76c1b098d6c7218fe0a58b55a64e02a4d203fc`, built by pure
  rules from the frozen tape (no price): Binance, `*USDT`, notional ≥ 50 k$, enriched with `bbo_age ≥ 0`,
  30-min horizon observable at freeze (event ≤ freeze − 32 min), no post-event field filled.
  Exclusions: {'notional_lt_50k': 18488, 'not_usdt_quote': 725, 'horizon_30m_not_observable_at_freeze': 2, 'not_binance (bybit: no sub-minute history)': 5347}.
- **H1 universe: 368 events, 51 five-minute clusters, 44 symbols, 272 liquidated longs.**
  By hour UTC: {'11': 4, '12': 221, '13': 26, '14': 7, '15': 9, '16': 61, '17': 25, '18': 11, '19': 4} — one cascade (12:00 UTC) carries most events.
- **H2 universe (≥ 250 k$): 62 events, 15 clusters.**
- Prices: Binance REST `fapi/v1/aggTrades`, short windows around each event, cached under `data/forced_flow_prices/`,
  fetched only at the look. No live data, no order.

## Hypotheses (direction fixed by the side of the forced flow)

sign = −1 after a liquidated **long** (forced sell), +1 after a liquidated **short** (forced buy).

| H | mechanism | universe | statistic | primary | sensitivities |
|---|---|---|---|---|---|
| H1 | `forced_liquidation_reaction_v1` | ≥ 50 k$ | **continuation**: sign × 10⁴ ln(P_exit / P_entry) | 30 s | 5 s, 5 min |
| H2 | `forced_liquidation_exhaustion_v1` | ≥ 250 k$ | **reversal**: −sign × 10⁴ ln(P_exit / P_entry) | 5 min | 30 s, 30 min |

Entry: first aggTrade at or after event_ts + **2 s** (measured receive latency ≈ 1.1 s under the 1 s/symbol
throttle, plus margin); lookahead 10 s then 60 s, else excluded. Exit: last aggTrade at or before
entry + horizon (must be after entry). Raw returns, no benchmark (seconds-scale, symbol-specific flow).

## Buckets (descriptive only, reported at the primary horizon, never selected on)

side (long / short liquidated) · notional (50–100 k, 100–250 k, 250 k–1 M, ≥ 1 M) · group (BTC/ETH vs alts) ·
spread_before (≤ 0.1 bps vs >) · book imbalance before (with the flow vs against it). Venue is Binance only
(Bybit has no sub-minute history; its tape is not used). Volatility proxy: not available pre-event, not used.

## Costs

Per event: taker VIP0 5 bps × 2 + `spread_before_bps` of the event (median 0.1 bps on the universe) + 4 bps
slippage ⇒ ≈ 14 bps round trip; wall = 3 × the event-weighted mean (≈ 42 bps). Minimum gross 10 bps.

## Statistics

N, mean, median, cluster-robust SE by **5-minute window across all symbols** (a cascade is one cluster),
t, win rate, N_eff = (Σ|x|)²/Σx², top-1 event share of positive contributions, top-cluster share,
number of clusters, median entry lag. Nothing is estimated on the data before the verdict.

## Verdict (fixed order, per hypothesis, primary horizon)

1. N < 30 → `INDECIDABLE`
2. mean gross < 10 bps → `REJECTED_NO_GROSS`
3. mean gross < 3 × cost → `REJECTED_COST_WALL`
4. t < 1.96, or N_eff < 30, or top-1 event > 20 %, or **clusters < 20**, or top cluster > 30 % of the positive
   contributions → `INDECIDABLE`
5. otherwise → `FORWARD_SEAL_REQUIRED`

Declared before the look: H2 has 15 clusters < 20, so H2 cannot reach FORWARD_SEAL on this tape;
a rejection (no gross / cost wall) would be its only decisive outcome. H1 has 51 clusters, but
one cascade carries most events: the top-cluster rule exists for that.

## Power (declared)

Assumed σ of the 30-s continuation ≈ 25 bps on large liquidations; with ≈ 50 effective clusters the MDE on the
mean is ≈ 1.96 × 25 / √50 ≈ 7 bps — H1 is powered for its 10 bps floor if events inside a cluster are not fully
redundant; if they are, the effective N is the cluster count and the test is marginal. Said, not moved.

## Execution discipline

- This file, the universe and the FREEZE are committed and pushed before the look (branch `p3-forced-flow-first-look`).
- `python mechanisms/forced_liquidation_reaction_v1/first_look.py --run` refuses without FREEZE, if the frozen
  tape, the universe or any pinned file changed, if the prereg is not pushed, or if a result exists.
- The LOOK_LEDGER entry is written and 1 test debited before the first price; the kernel multiplicity
  ledger records the two trials and the burned period of the family at the end of the run.
- Positive control (synthetic trades, 40 bps continuation injected, 200 events): recovered 39.8 bps,
  t 53, null REJECTED_NO_GROSS — `results/positive_control.json`.
- No parameter search beyond the prereg buckets, no live trading, no old alpha import, no interim peeking,
  no second look.
