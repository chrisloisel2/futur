# FUTURES FEE DECISION — P13 (2026-09-12)

**Decision: `USE_OFFICIAL_PUBLISHED_VIP0_FUTURES_FEES`** (option B, decided by the user).

## What was observed

- The read-only key (`enableReading` only, no trading permission) reads the spot account: `makerCommission = takerCommission = 10 bps`
  → **VIP0** on the official spot schedule (VIP1 is 9/10).
- Every signed `/fapi` endpoint answers `-2015`. Binance serves futures signed endpoints, reads included, only to a key with
  "Enable Futures" — a trading permission. **There is no futures-read-only key.** `account_actual` for the futures fee is
  unavailable by Binance's permission model, not by a defect of the key or the client.

## Rationale

- spot account confirms VIP0
- futures signed read endpoints require enabling futures permission
- enabling futures permission creates an avoidable trading-key risk
- expected uncertainty is <= 0.5 bps (the 10 % BNB discount on a 5 bps taker fee)
- spread/slippage dominate the cost chain
- no alpha promotion may depend on the BNB discount

## What is used from now on

| item | value |
|---|---|
| taker futures fee | 5 bps per side |
| round-trip taker fee | 10 bps |
| BNB discount | not applied |
| fee status | `official_published_vip0_confirmed_by_spot_tier` |
| schema | `account_actual = false`, `official_published = true`, `tier_confirmed = VIP0`, `bnb_discount_applied = false`, `residual_uncertainty_bps = 0.5` |

## Key policy

- No futures trading key. No `--allow-trading-key`. No permission capable of sending an order.
- The current key must be **IP-whitelisted** (`ipRestrict` was `false` at collection): Binance API Management → edit the key →
  "Restrict access to trusted IPs only" → add this machine → re-run the `apiRestrictions` probe (`--collect`).

## The real lesson: t0 is too expensive

Measured chain (P11 depth archives, official Vision data, 174 launches, 500 $ round trip, fee 10 bps):
t0 **52.9 bps** · +5 min 24.8 · **+15 min 23.7** · +60 min 18.6. A fade entered exactly at the Binance open pays twice the
spec's declared cost; by +15 min the spread has normalised. The preregistration therefore does not enter at t0.

**Default prereg candidate: entry t0 + 15 min, notional 500 $, cost chain 23.7 bps round trip (`official_published`).**
Chosen on cost/capacity only; no return was read. The preregistration may restate this window, never move it after a look.

Machine-readable twin: `FUTURES_FEE_DECISION.json` (read by `account_execution_reality.build_costs` to compute the H2 chain
at the decided window).
