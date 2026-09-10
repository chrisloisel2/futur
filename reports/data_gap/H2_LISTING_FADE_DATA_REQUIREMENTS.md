# H2 — listing perp fade: data requirements (audit, no look)

Verdict on record: `event_listing_perp_fade_v1` INDECIDABLE (regard seq 8: +251 / +230 bps, t 2.12 < 2.3263, σ 1 546 bps, 174 events).
The verdict is not touched. This audit lists what the dataset lacked, by field.

| field | status | what we have | decision | priority | what to do |
|---|---|---|---|---|---|
| exact tradable_start_ts | PARTIAL | first Vision 1-min bar (minute resolution); onboardDate agrees within 5 min for 170/174 | **BUILD** | P1_HIGH_VALUE | second-level start from the first aggTrade (Vision aggTrades launch day: 174/174 available) |
| first_trade_ts | MISSING | not extracted | **BUILD** | P1_HIGH_VALUE | Vision aggTrades, first row of the launch-day file; also gives the opening print and its size |
| first_orderbook_ts | MISSING | no bookTicker on Vision for new symbols (404 on launch days); no history anywhere public | **BUILD** | P2_NICE_TO_HAVE | only forward: subscribe depth/bookTicker at announcement time (median 1.7 h before launch) |
| onboardDate officiel | HAVE | exchangeInfo snapshot 897 symbols (current only; 4/174 had a date-only value) | **BUILD** | P1_HIGH_VALUE | daily exchangeInfo snapshot so delisted symbols keep their onboardDate |
| announcement_ts | HAVE | CMS releaseDate to the second; the announced launch time is in the body, not stored | **BUILD** | P1_HIGH_VALUE | store the announcement body text (cheap) and parse the announced launch time |
| symbol status transitions | MISSING | no history of PENDING_TRADING -> TRADING -> SETTLING | **BUILD** | P1_HIGH_VALUE | exchangeInfo snapshot-diff timer (same pattern as Coinbase/HL in the event tape) |
| L2 order book t0 -> t+6h | PARTIAL | Vision bookDepth (1-min snapshots at % levels) exists on the launch day for 166/174; no tick L2 | **BUILD** | P1_HIGH_VALUE | Vision bookDepth for the 166 covered launches; forward tick L2 recorder for future listings; BUY (Tardis) only if tick L2 history is ever required |
| trades tick-by-tick t0 -> t+6h | MISSING | Vision aggTrades launch day: 174/174 | **BUILD** | P1_HIGH_VALUE | entry/exit at the second, volume profile, trade-size distribution for the slippage model |
| mark price | MISSING | Vision markPriceKlines 1m exists for new symbols on launch day | **BUILD** | P0_REQUIRED | the perpetual's own fair-value line; without it the 6-h move is measured against BTC, which is not the token's reference |
| index price | MISSING | Vision indexPriceKlines / premiumIndexKlines: 167/174 on launch day | **BUILD** | P0_REQUIRED | the index is Binance's composite of OTHER venues' prices = the external reference the hypothesis is about (overshoot vs outside); 90/174 assets have an OKX/Bybit listing before the Binance launch |
| funding initial | MISSING | Vision fundingRate monthly | **BUILD** | P1_HIGH_VALUE | first settlement funding = price of the crowding; 8-h resolution |
| open interest | MISSING | Vision metrics 5-min: 174/174 on launch day; REST openInterestHist only 30 days | **BUILD** | P1_HIGH_VALUE | OI accumulated in the first 15 min = leveraged crowding; reduces variance of the fade estimate |
| spread / depth 10/25/50 bps | PARTIAL | bookDepth 1-min at % levels (166/174); no spread history for new symbols | **BUILD** | P1_HIGH_VALUE | executability: size a 100 k$ short at +15 min; declared 8 + 6 bps becomes measured |
| 24h volume after launch | HAVE | Vision klines quote volume (median 55 M$ per 6-h window measured in seq 8) | **BUILD** | P2_NICE_TO_HAVE | already in the results; nothing to add |
| venue precedence (token already traded where?) | PARTIAL | event tape: 90/174 with an OKX/Bybit announcement before launch; 84 with no trace (DEX, Gate, MEXC, KuCoin, Bitget not covered); Binance Alpha events exist (59) but unlinked | **BUILD** | P1_HIGH_VALUE | extend the event tape to Gate/MEXC/KuCoin/Bitget official listing pages + fapi/v1/constituents (index composition per symbol) |
| spot Binance existed before perp? | HAVE | Vision spot file existence (21 excluded in seq 8) | **BUILD** | P2_NICE_TO_HAVE | done; keep the rule |
| perp was truly first Binance market? | PARTIAL | true vs spot; Binance Alpha (pre-listing venue) not checked | **BUILD** | P2_NICE_TO_HAVE | link the tape's alpha events to the perp universe |
| executable short path | PARTIAL | perp exists => short allowed; leverage brackets, marketTakeBound, maxMoveOrderLimit known only for current symbols | **BUILD** | P1_HIGH_VALUE | exchangeInfo snapshot + leverageBracket (read-only key) at launch |
| taker/maker fee actual | PARTIAL | published VIP0 (P1.1 official manifest); account actual never fetched | **BUILD** | P1_HIGH_VALUE | scripts/fetch_account_fees.py with a read-only key |
| slippage model | MISSING | declared 6 bps | **BUILD** | P1_HIGH_VALUE | from bookDepth + aggTrades trade-through on the 166 covered launches; no vendor needed |

## Reading

- **Two P0 fields**: mark price and index price. The hypothesis is about an overshoot relative to where the token is
  valued elsewhere; the look measured the perp against BTC because the dataset had nothing else. The index is Binance's
  own composite of other venues; it exists on Vision for 167 of the 174 launch days. 90 of the 174 assets had an
  OKX/Bybit listing before the Binance launch (75 a Bybit perp): the external reference is real, not hypothetical.
- **Variance**: the per-event dispersion (1 546 bps) is the reason for INDECIDABLE. The P1 fields (OI in the first 15 min,
  funding at first settlement, depth, first trade) are the ex-ante states that a preregistered test could condition on.
  Choosing which one is a new preregistration and costs a test; this audit only says the data must exist first.
- **Executability**: the short path is real (perp exists, 55 M$ median 6-h quote volume) but the cost was declared
  (8 + 6 bps). bookDepth on 166 launch days makes it measurable.
- **Not needed**: more listings (≈ 50 per year: four years to decide at the current σ), DEX prices, sentiment.
