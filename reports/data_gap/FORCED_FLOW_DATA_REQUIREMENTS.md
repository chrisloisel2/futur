# Forced flow — data requirements (audit, no look)

Verdicts on record (branch `p3-forced-flow-first-look`, regard seq 9): `forced_liquidation_reaction_v1` REJECTED_NO_GROSS
(−7 bps at 30 s after a +2 s entry, n 368), `forced_liquidation_exhaustion_v1` REJECTED_COST_WALL (+13.8 bps at 5 min < 43 bps wall, n 62).

| field | status | what we have | decision | priority | what to do |
|---|---|---|---|---|---|
| public liquidation delay | HAVE | measured: stream_delay ~1 010 ms (1/s/symbol throttle), receive latency 480-1 100 ms; entry at +2 s | **IGNORE** | P0_REQUIRED | cannot be reduced with public data: the message is late by construction; a +2 s reader sees -7 bps of 'continuation' |
| mark / index pre-pressure | HAVE | mark_price, index_price in every Binance record (0 % null) | **BUILD** | P2_NICE_TO_HAVE | descriptive only; a hypothesis on it would be new (forbidden here) |
| OI change before liquidation | MISSING | open_interest null 98.7 % (Binance) / 87 % (Bybit): no OI stream exists | **BUILD** | P2_NICE_TO_HAVE | REST openInterest poll (weight 1) every 5 s for the top-60 = 720 weight/min; only worth it if the family is ever re-funded |
| L2 imbalance before liquidation | PARTIAL | BBO imbalance only (book_imbalance_before); no depth | **BUILD** | P2_NICE_TO_HAVE | depth@100ms 20 levels for the top-60 (several GB/day); same condition |
| estimated liquidation levels | MISSING | requires positions (private); vendors sell heatmaps derived from OI + leverage guesses | **IGNORE** | P2_NICE_TO_HAVE | noise by construction; forbidden |
| burst clustering | HAVE | 67 % of inter-event gaps < 1 s; 513 seconds with >= 5 liquidations; 607 messages in the peak second; top 5-min window = 18 % of events | **BUILD** | P2_NICE_TO_HAVE | already in the tape; cluster = 5-min window is the right unit |
| private vs public latency limitation | HAVE | the engine executes before publishing; public feed throttled AND sampled (1 order/s/symbol): cascade size unobservable | **IGNORE** | P0_REQUIRED | structural; the only earlier position is being the passive counterparty (market making at institutional tier) = the P1 verdict |
| are public liquidation messages too late? | HAVE | YES for continuation at 5 s / 30 s / 5 min (-1.2 / -7.0 / -10.9 bps after a +2 s entry); the 5-min reversal sign exists (+13.8, 71 % wins) but under the 43 bps wall | **IGNORE** | P0_REQUIRED | measured in seq 9; no data purchase changes the latency |

## Reading

- **The public message is too late for continuation, and no purchase fixes it.** Binance publishes one liquidation order
  per second per symbol (the most recent), Bybit one aggregate per second; the engine has already executed. In the frozen
  session 67 % of inter-event gaps were under one second and the peak second carried 607 messages: the cascade is visible
  as a burst, not as a sequence. Continuation after a +2 s entry measured −1.2 / −7.0 / −10.9 bps at 5 s / 30 s / 5 min.
- **What the tape can still be used for**: descriptive state (mark/index pre-pressure is already recorded), burst
  statistics, and as the depth/BBO source at announcement time for H3's forward seal on the top-60 symbols.
- **What is not worth building unless the family is re-funded**: OI polling and depth@100ms for the top-60 (P2). They
  would not change the latency; they would only describe the state before a message that arrives after the move.
- **Forbidden**: liquidation-level heatmaps and any 'complete liquidation' vendor feed — they are derived from the same
  throttled stream plus guesses.
