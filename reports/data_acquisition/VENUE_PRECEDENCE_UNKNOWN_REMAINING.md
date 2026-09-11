# VENUE PRECEDENCE — what is still unknown (2026-09-11T23:11:37 UTC)

31 of 174 H2 assets cannot be classified. An unknown is reported as an unknown; it is never counted as Binance-first by default.

| asset | markets found | venues without a date | announcement evidence in the tape |
|---|---|---|---|
| 0G | 9 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| 1000000BOB | 3 on gate, kucoin, mexc | gate | bybit listing announced 2023-05-12T09:20 |
| 1000WHY | 1 on gate | gate | bybit futures_listing announced 2024-10-04T10:45 |
| AI16Z | 0 on — | — | bybit futures_listing announced 2024-12-30T08:28 |
| AZTEC | 10 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| BREV | 12 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| CC | 14 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| CUDIS | 1 on gate | gate | bybit futures_listing announced 2025-06-12T08:47 |
| ESP | 8 on bybit, gate, kucoin, mexc, okx | gate | — |
| FOGO | 12 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| GIGADEV | 2 on gate, kucoin | gate | — |
| IP | 0 on — | — | bybit futures_listing announced 2025-02-07T08:29 |
| KITE | 11 on bybit, gate, kucoin, mexc, okx | gate | — |
| LINEA | 13 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| MEMEFI | 0 on — | — | bybit futures_listing announced 2024-11-12T07:53 |
| MON | 12 on bybit, gate, kucoin, mexc, okx | bybit, gate | bybit listing announced 2024-05-24T10:00 |
| NEIROETH | 0 on — | — | bybit futures_listing announced 2024-08-15T10:00 |
| POPMART | 4 on bybit, gate, kucoin, okx | gate | — |
| QNTX | 3 on bybit, gate, kucoin | gate | — |
| SENT | 12 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| SKHY | 4 on bybit, gate, kucoin, okx | gate | — |
| SLERF | 0 on — | — | bybit futures_listing announced 2024-03-18T13:00 |
| SOMI | 8 on bybit, gate, kucoin, mexc | bybit, gate | — |
| STABLE | 10 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| TON | 0 on — | — | bybit listing announced 2022-12-21T04:24 |
| UXLINK | 1 on gate | gate | bybit listing announced 2024-07-16T05:32 |
| XAG | 4 on bybit, gate, kucoin, okx | gate | — |
| XPT | 4 on gate, kucoin, mexc, okx | gate | — |
| YB | 13 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| ZAMA | 13 on bybit, gate, kucoin, mexc, okx | bybit, gate | — |
| ZRC | 1 on gate | gate | bybit listing announced 2024-11-22T05:45 |

## Which venue client to improve next

Counted by how many unknowns it would settle:

- **gate**: 25 assets. Gate publishes no listing date; the per-pair first-candle call resolves it one symbol at a time, and fails when the pair was renamed or delisted.
- **bybit**: 12 assets. No listing date in the instruments endpoint.

The remaining route for all of them is the same: ask the market for its own first candle, or read the venue's own announcement archive (the P7 body archive already does this for Binance and can be pointed at OKX and Bybit).

