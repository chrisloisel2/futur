# H2 CAUSAL POPULATIONS — P11 (2026-09-12T14:48:19 UTC)

H2 is not one population. It is the union of markets born in different circumstances that regard seq 8 averaged into one number. This report splits it by **who priced the asset first** and by **what still blocks a test**. Two axes, never confused. No return, no verdict, no budget.

## Populations (market structure)

| population | events | share |
|---|---|---|
| `TRUE_BINANCE_PERP_FIRST` | 6 | 3 % |
| `MEXC_FIRST` | 113 | 65 % |
| `OKX_FIRST` | 8 | 5 % |
| `BYBIT_FIRST` | 10 | 6 % |
| `KUCOIN_FIRST` | 6 | 3 % |
| `GATE_FIRST_UNKNOWN_DATE` | 13 | 7 % |
| `UNKNOWN_PRECEDENCE` | 18 | 10 % |

## Lead time of the first external listing over the Binance perpetual

| bucket | events |
|---|---|
| < 1h | 4 |
| 1h-24h | 12 |
| 1d-7d | 46 |
| 7d-30d | 22 |
| 30d-180d | 26 |
| >180d | 27 |
| unknown | 37 |

## The seven answers

1. True first listings (the Binance perpetual is the first market anywhere collected): **6**.
2. MEXC-first: **113**.
3. Other-venue-first excluding MEXC (OKX, Bybit, KuCoin, other): **24**.
4. Bad timestamps: **8**.
5. Blocked only by execution cost: **153**.
6. Blocked only by capacity: **0**; blocked by cost and capacity together and nothing else: 6.
7. Clean per population if cost and capacity are lifted: {'OKX_FIRST': 7, 'MEXC_FIRST': 102, 'KUCOIN_FIRST': 6, 'UNKNOWN_PRECEDENCE': 17, 'BYBIT_FIRST': 8, 'GATE_FIRST_UNKNOWN_DATE': 13, 'TRUE_BINANCE_PERP_FIRST': 6}.

Clean now: 0. Capacity inputs available: 174 events.

## What this means

A test on `H2_POOLED` would mix a MEXC migration effect, an OKX / Bybit cross-listing effect and a handful of true births. Any future preregistration must name one population. The count of true first listings is the ceiling of what a first-listing hypothesis can ever use from history; it is not increased by any backfill.

