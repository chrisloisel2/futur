# PRE-BINANCE VENUE TAPE — coverage (2026-09-12T17:22:08 UTC)

For the assets that already traded elsewhere before the Binance perpetual opened, the state of that other market **before t0**. Nothing at or after t0 is requested by construction; this tape cannot measure anything post-Binance. No signal, no verdict, no budget.

`OTHER_VENUE_FIRST` events: **137**, by first venue {'bybit': 10, 'okx': 8, 'mexc': 113, 'kucoin': 6}.

| venue | status | manifests | route |
|---|---|---|---|
| mexc | collected_by_this_module | {'collected': 112, 'not_collected': 1} | free; `GET https://api.mexc.com/api/v3/klines?symbol=<BASE>USDT&interval=<iv>&startTime…` |
| okx | route_documented_not_collected | {'collected': 8} | free; `GET https://www.okx.com/api/v5/market/history-candles?instId=<BASE>-USDT&bar=1Du…` |
| bybit | route_documented_not_collected | {'collected': 10} | free; `GET https://api.bybit.com/v5/market/kline?category=spot&symbol=<BASE>USDT&interv…` |
| kucoin | route_documented_not_collected | {'collected': 6} | free; `GET https://api.kucoin.com/api/v1/market/candles?symbol=<BASE>-USDT&type=1day|1h…` |
| gate | route_documented_not_collected | — | free; `GET https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=<BASE>_USDT&int…` |

Windows: t0 − 30d → t0, t0 − 14d → t0, t0 − 7d → t0, t0 − 3d → t0, t0 − 24h → t0, t0 − 6h → t0. Granularity: daily over 30 d, hourly over 3 d, 5-min over 6 h.

Only MEXC is collected by this branch (it is the first venue for the large majority). The other venues have their expected manifests written with the public endpoint that would fill them, so the next pass has nothing to guess.

