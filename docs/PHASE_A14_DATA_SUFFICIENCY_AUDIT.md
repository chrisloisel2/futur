# A14 (options surface shock) — data sufficiency audit, 2026-08-29

## Question

Can `data/options_backfill/deribit/` reconstruct, historically and point-in-time, what
catalog A14 requires: ATM IV, RR25, BF25, term structure, OI, a gamma proxy, volume/trade
flow? If yes, launch A14. If no, live Deribit options collection becomes priority.

## What exists

- `trades/BTC/*.parquet` (43 monthly files, 2023-01 → 2026, **BTC only, no ETH**): per-trade
  `trade_id, timestamp, instrument_name, price, mark_price, iv, index_price, direction,
  amount, liquidation, ts, expiry, strike, cp, is_block`.
- `features/BTC_daily.parquet`: pre-aggregated **daily** — `atm_iv_traded, skew_25ish,
  pc_volume_ratio, net_call_flow_btc, net_put_flow_btc, block_share, top_strike_share,
  n_trades, notional_btc` + day-over-day deltas. This is the file behind the
  `OPTIONS_POSITIONING_SIGNAL_SCAN` `NO_EDGE_DEFINITIF` verdict (684f497/dabc9f9) — a
  different mechanism (daily positioning *levels* → BTC forward return 1-7 *days*), not A14
  (short-horizon IV/skew *shocks* → realized-vol repricing, 1min-1h). That verdict does not
  cover A14.
- `DVOL_BTC_1d.parquet` / `DVOL_ETH_1d.parquet`: just a daily OHLC volatility index, no
  surface detail.

## Gap against catalog A14's `required_any_groups`

`strict_catalog.py`: `(("option__iv_*","option__skew*","option__rr25*"),
("option__oi*","option__gamma*"))` — **both** groups need non-trivial coverage.

| requirement | status |
|---|---|
| ATM IV | reconstructible from trade-level `iv`, but only at irregular trade timestamps (no continuous quoted surface) |
| RR25 / BF25 | not directly present; `skew_25ish` is a rougher single proxy, not verified against a true 25-delta risk-reversal/butterfly construction |
| term structure | reconstructible in principle (`expiry` is on every trade row) but never built |
| **open interest** | **absent everywhere** — not in trades, not in features, not backfilled anywhere under `data/` |
| gamma proxy | not present; computable from iv/strike/expiry/spot via a pricing model, but needs OI to weight into anything resembling real dealer/GEX exposure, which needs the missing OI |
| volume/trade flow | present (`n_trades`, `notional_btc`, per-trade `amount`) |
| ETH coverage | **trades/features missing entirely** — only DVOL exists for ETH |

## Verdict

**Not sufficient. A14 cannot pass its own readiness check** (`option_oi`/`gamma` group has
zero coverage) **without OI data that doesn't exist in this backfill.** Reconstructing a
trade-implied ATM IV / term structure is possible as a follow-up (sparse, trade-print-only,
not a true continuous surface), but OI is a hard blocker regardless, and ETH has no
trade-level data at all.

## Consequence

Live Deribit **options** collection (ticker channel per BTC/ETH instrument across the
strike/expiry grid — carries `open_interest`, `mark_iv`, `bid_iv`/`ask_iv`, greeks directly,
per the public API) is now the priority path for A14, not further mining of the existing
backfill. This reuses ~80% of the Deribit perpetuals collector already built today
(connection handling, heartbeat, canonicalization) — new work is the instrument-grid
subscription list and an options-specific parser (`OptionQuote` in `schema.py` already
anticipates this, unused until now).
