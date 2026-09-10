# Data acquisition priority — P4 (2026-09-11)

Everything below is BUILD from public archives or from timers already running, except one BUY marked conditional.
No item consumes a test. No item is a trading hypothesis.

## P0_REQUIRED — without these no serious test is possible

1. **Binance Vision derivatives state for event windows** (`vision_event_window_state`): indexPrice / markPrice /
   premiumIndex 1-min, metrics OI 5-min, fundingRate, bookDepth 1-min, aggTrades — for the 174 H2 launches
   (coverage 167 / 174 / 174 / 166 / 174), the 46 H3 perp announcements (bookDepth 41, metrics 45) and every future event.
   Effort 1.5 days. Impact: H2 measurable against its reference; H3 seal executable (measured cost).
2. **Announcement body + status snapshots** (`announcement_body_and_status_snapshots`): store the body text (launch
   minute, delisting minute, borrow-suspension sentence), daily exchangeInfo and margin-pairs snapshots
   (status transitions, delisted symbols keep their onboardDate). Effort 0.5 day. Closes I22.

## P1_HIGH_VALUE — variance and executability

3. Forward tick L2 + bookTicker recorder armed at announcement time (median 1.7 h before a launch), until +24 h. Effort 2 days.
4. Account actual fees, leverage brackets, margin borrow rates via a read-only key. Effort 0.5 day.
5. Event-tape extension to Gate / MEXC / KuCoin / Bitget listing pages + `fapi/v1/constituents` (index composition). Effort 1 day.

## P2_NICE_TO_HAVE

6. Bybit public tick archives for the 75 assets with a Bybit perp before the Binance launch. Effort 1 day.
7. Vendor tick L2 history (BUY, conditional): only for a preregistered test that needs the 8 / 174 launches without
   Vision bookDepth or sub-minute spread; not before budget > 0.
8. OI polling and depth@100ms for the top-60 (forced flow): only if the liquidation family is ever re-funded.

## IGNORE

- Un-throttled liquidation feeds (do not exist), liquidation heatmaps, more klines breadth, news / sentiment / ML features,
  DEX pre-listing prices, sub-second private feeds.

## Order of work

`vision_event_window_state` (1) → body/status layer (2) → hash and register both as data sources in the loop
(no budget credit: no new episodes) → only then, and only with budget > 0, a preregistration may use them.
