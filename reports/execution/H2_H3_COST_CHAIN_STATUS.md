# H2 / H3 COST CHAIN STATUS (2026-09-12T20:45:45 UTC)

A cost chain has three links — fee, spread, slippage — and is worth its weakest. `confirmed` and `contradicted` require every link measured (`account_actual`) or officially published; a chain that rests on a declared number is `unknown`. `unknown` is an honest state, not a defect.

| hypothesis | spec declared round trip | chain round trip | fee | spread | slippage | weakest | status |
|---|---|---|---|---|---|---|---|
| H2 | 24.0 bps | 23.7 bps | `official_published` | `official_published` | `official_published` | `official_published` | **confirmed** |
| H3 | 18.0 bps | 18.0 bps | `official_published` | `declared` | `declared` | `declared` | **unknown** |

## Decision in force

`USE_OFFICIAL_PUBLISHED_VIP0_FUTURES_FEES` (FUTURES_FEE_DECISION.md): official VIP0 futures fee at the tier confirmed by the spot account, BNB discount not applied, no futures trading key. Cost-chain window for H2: **+15 min, 500 $** — chosen on cost/capacity only.

## Fee link

- provenance now: `official_published` — official VIP0 schedule; tier VIP0 confirmed from the account's spot commission; BNB discount not applied (status unknown: conservative)
- tier inferred from the account's spot commission: **VIP0** (spot makerCommission/takerCommission (account_actual) matched to the official spot schedule)
- `account_actual` for the futures fee is **not reachable with a read-only key**: Binance serves every signed /fapi endpoint, reads included, only to a key with 'Enable Futures' -- a trading permission. There is no futures-read-only key: a read-only key gets -2015 on /fapi and the futures fee cannot be read as account_actual without accepting a trading-capable key (IP-restricted, --allow-trading-key, journaled).
- consequence: the fee link stays `official_published` at the confirmed tier; on VIP0 the only residual is the 10 % BNB discount (0.5 bps taker), not applied here (conservative).
- **the key has no IP restriction** (`ipRestrict=false`): restrict it to this machine before any further use.

## Measured chain by window and notional (H2, P11 depth archives) — descriptive, no window chosen here

Spread = median effective spread (full, round trip); slippage = median of sell + buy linear estimates within the book band (band upper bound alongside); fee = 2 × per-side fee. `status` compares to the spec's declared round trip at ±0.5 bps.

| window | notional | n | fee/side | spread | slippage rt | band ub | round trip | spec declared | status | weakest |
|---|---|---|---|---|---|---|---|---|---|---|
| +0 min | 100 $ | 174 | 5.0 | 28.4 | 2.9 | 200.0 | **41.3** | 24.0 | contradicted | `official_published` |
| +0 min | 500 $ | 174 | 5.0 | 28.4 | 14.5 | 200.0 | **52.9** | 24.0 | contradicted | `official_published` |
| +0 min | 1000 $ | 174 | 5.0 | 28.4 | 28.9 | 200.0 | **67.3** | 24.0 | contradicted | `official_published` |
| +1 min | 100 $ | 174 | 5.0 | 17.7 | 1.7 | 200.0 | **29.4** | 24.0 | contradicted | `official_published` |
| +1 min | 500 $ | 174 | 5.0 | 17.7 | 8.6 | 200.0 | **36.3** | 24.0 | contradicted | `official_published` |
| +1 min | 1000 $ | 174 | 5.0 | 17.7 | 17.1 | 200.0 | **44.7** | 24.0 | contradicted | `official_published` |
| +5 min | 100 $ | 174 | 5.0 | 10.4 | 0.9 | 200.0 | **21.3** | 24.0 | contradicted | `official_published` |
| +5 min | 500 $ | 174 | 5.0 | 10.4 | 4.3 | 200.0 | **24.8** | 24.0 | contradicted | `official_published` |
| +5 min | 1000 $ | 174 | 5.0 | 10.4 | 8.7 | 200.0 | **29.1** | 24.0 | contradicted | `official_published` |
| +15 min | 100 $ | 174 | 5.0 | 10.3 | 0.7 | 200.0 | **21.0** | 24.0 | contradicted | `official_published` |
| +15 min | 500 $ | 174 | 5.0 | 10.3 | 3.4 | 200.0 | **23.7** | 24.0 | confirmed | `official_published` |
| +15 min | 1000 $ | 174 | 5.0 | 10.3 | 6.7 | 200.0 | **27.0** | 24.0 | contradicted | `official_published` |
| +30 min | 100 $ | 174 | 5.0 | 8.6 | 0.6 | 200.0 | **19.2** | 24.0 | contradicted | `official_published` |
| +30 min | 500 $ | 174 | 5.0 | 8.6 | 2.8 | 200.0 | **21.4** | 24.0 | contradicted | `official_published` |
| +30 min | 1000 $ | 174 | 5.0 | 8.6 | 5.5 | 200.0 | **24.1** | 24.0 | confirmed | `official_published` |
| +60 min | 100 $ | 174 | 5.0 | 6.3 | 0.5 | 200.0 | **16.8** | 24.0 | contradicted | `official_published` |
| +60 min | 500 $ | 174 | 5.0 | 6.3 | 2.2 | 200.0 | **18.6** | 24.0 | contradicted | `official_published` |
| +60 min | 1000 $ | 174 | 5.0 | 6.3 | 4.5 | 200.0 | **20.8** | 24.0 | contradicted | `official_published` |

The chain leaves `unknown` the moment a preregistration names one window and one notional from this table; it then reads `confirmed` or `contradicted` against the spec at `official_published` grade. This table chooses nothing and reads no return.

## What would move each chain

- fee → `account_actual`: only a key with 'Enable Futures' (a trading permission) can read `GET /fapi/v1/commissionRate`; the repository does not require it.
- spread, slippage → measured: P11 `depth_capacity_features` derives an effective-spread proxy and slippage bounds from the Vision archives; they enter the chain as `official_published`-grade links once a preregistration names which window it uses.
- The official fee may serve to **reject** a hypothesis whose gross is below 3 × the published cost. It may never serve to promote one.

