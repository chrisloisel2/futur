# DATA TO IGNORE — P4 (2026-09-11)

Time traps. Each line is measured or structural, not a taste.

| data | why it is ignored | evidence |
|---|---|---|
| general OHLCV / more candles / more symbols | breadth is saturated; every zero of the last looks was well-measured | 696 perps daily since 2019; 7 449 events; seq 7/8/9 rejections with SE 4.8-9.6 bps |
| technical indicators (RSI, MACD, Bollinger, moving averages) | no payer, no structure; banned by the kernel spec validator | `research_kernel.mechanism_spec.BANNED_HYPOTHESIS_PATTERNS` |
| ML on candles / feature stores | adds degrees of freedom, not information; forbidden by project rules | LEGACY_FREEZE.md, PROJECT_TRUTH.md |
| funding alone as a signal | exhausted by arbitrage 2025-26 in the loop's own record; kept only as a STATE field | loop digest, family funding closed |
| long/short ratio alone | INDECIDABLE at t 2.399 vs 2.955; forward sealed until 2028-12-06; nothing to add before | `lsr_globacct_x` truth entry |
| general sentiment / generic news / social feeds | not an official event; no timestamp of record; the official pump is priced in < 60 s anyway | seq 7: +1 253 bps inside the publication minute |
| public liquidation stream as a direct signal | one order per second per symbol, published after execution: −7 bps after a +2 s entry | seq 9 |
| liquidation-level heatmaps / positioning vendors | OI plus leverage guesses on the same throttled stream | structural |
| "complete" or un-throttled liquidation feeds | do not exist publicly; every vendor consumes the same stream | Binance forceOrder documentation |
| sub-second co-located / private feeds | not purchasable at this tier; the maker route is market making (P1 verdict) | P1 payer discovery |
| DEX pre-listing prices | manipulable at listing time; the Binance index already embeds the venues that matter | P4 audit |
| an H2 bot live now | INDECIDABLE at t 2.12 with σ 1 546 bps; no state data yet | seq 8 |
