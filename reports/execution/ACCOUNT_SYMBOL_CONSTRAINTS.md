# ACCOUNT SYMBOL CONSTRAINTS — P8 (2026-09-11T23:06:16 UTC)

What the exchange lets an order be, per symbol: tick size, lot step, minimum notional, market-order bound, liquidation fee, maintenance margin. Public `exchangeInfo`, no key needed. These are the constraints any capacity or slippage estimate has to respect.

Coverage: **207** of 221 H2 + H3 symbols are still listed; **14** are not in `exchangeInfo` any more (delisted contracts disappear from it, which is itself the fact that a delisting happened).

| symbol | scope | status | tick | step | min qty | min notional | market take bound | liq. fee | maint. margin |
|---|---|---|---|---|---|---|---|---|---|
| 0GUSDT | H2 | TRADING | 0.0001000 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| 1000000BOBUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| 1000000MOGUSDT | H2 | TRADING | 0.0001000 | 0.1 | 0.1 | 5 | 0.15 | 0.020000 | 2.5000 |
| 1000CATUSDT | H2 | TRADING | 0.0000010 | 1 | 1 | 5 | 0.15 | 0.015000 | 2.5000 |
| 1000PEPEUSDT | H2 | TRADING | 0.0000001 | 1 | 1 | 5 | 0.15 | 0.015000 | 2.5000 |
| 1000RATSUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.025000 | 2.5000 |
| 1000SATSUSDT | H2 | TRADING | 0.00000001 | 1 | 1 | 5 | 0.15 | 0.015000 | 2.5000 |
| 1000WHYUSDT | H2 | SETTLING | 0.0000001 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| 4USDT | H2 | TRADING | 0.0000010 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| A2ZUSDT | H3 | SETTLING | 0.0000001 | 1 | 1 | 5 | 0.15 | 0.015000 | 2.5000 |
| ACUUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| ACXUSDT | H3 | SETTLING | 0.0000100 | 0.1 | 0.1 | 5 | 0.15 | 0.015000 | 2.5000 |
| AGTUSDT | H2 | TRADING | 0.0000010 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| AI16ZUSDT | H2 | SETTLING | 0.0000100 | 0.1 | 0.1 | 5 | 0.15 | 0.020000 | 2.5000 |
| AIAUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| AIGENSYNUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| AIOUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| AKEUSDT | H2 | TRADING | 0.0000010 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| ALPACAUSDT | H3 | SETTLING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.025000 | 2.5000 |
| ALPHAUSDT | H3 | SETTLING | 0.00001 | 1 | 1 | 5 | 0.10 | 0.025000 | 2.5000 |
| AMBUSDT | H3 | SETTLING | 0.0000010 | 1 | 1 | 5 | 0.15 | 0.015000 | 2.5000 |
| ANTHROPICUSDT | H2 | TRADING | 0.01000 | 0.01 | 0.01 | 5 | 0.03 | 0.015000 | 2.5000 |
| APTUSDT | H3 | TRADING | 0.00010 | 0.1 | 0.1 | 5 | 0.10 | 0.015000 | 2.5000 |
| ARCUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| ARIAUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| ARKUSDT | H2 | TRADING | 0.0001000 | 1 | 1 | 5 | 0.15 | 0.025000 | 2.5000 |
| ARXUSDT | H2 | TRADING | 0.0001000 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| ASTERUSDT | H2 | TRADING | 0.0001000 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| ATAUSDT | H3 | SETTLING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.015000 | 2.5000 |
| ATHUSDT | H2 | TRADING | 0.0000010 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| ATUSDT | H2 | TRADING | 0.0001000 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| AZTECUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| B2USDT | H2 | TRADING | 0.0001000 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| B3USDT | H2 | SETTLING | 0.0000001 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| BABYUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.015000 | 2.5000 |
| BAKEUSDT | H3 | SETTLING | 0.0001 | 1 | 1 | 5 | 0.10 | 0.020000 | 2.5000 |
| BANKUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| BANUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| BASEDUSDT | H2 | TRADING | 0.0000100 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |
| BASUSDT | H2 | TRADING | 0.0000010 | 1 | 1 | 5 | 0.15 | 0.020000 | 2.5000 |

(first 40 of 207; the full set is in `ACCOUNT_EXECUTION_REALITY.json` inputs and can be regenerated with `--collect`)

## Symbols no longer listed

ACAUSDT, AERGOUSDT, AIONUSDT, AKROUSDT, ALCXUSDT, ANTUSDT, BEAMUSDT, BIFIUSDT, BTSUSDT, CVPUSDT, DREPUSDT, FOOTBALLUSDT, GFTUSDT, USDSUSDT

For these, constraints at the time of the event can only come from a historical source; `exchangeInfo` is a snapshot of now.

## What still needs a key

- `maxLeverage` per notional bracket (`GET /fapi/v1/leverageBracket`): decides the margin a short actually costs.
- Margin borrowability and borrow rate per asset (`sapi` margin endpoints): decides whether a spot short exists at all.

