# CURRENT COLLECTION HEALTH (2026-09-11T16:20:37 UTC)

| level | check | detail |
|---|---|---|
| **ok** | heartbeat age | last heartbeat 2026-09-11T16:19:33.771181+00:00 (1 min ago) |
| **ok** | watch RSS | first 347 MB, last 564 MB, max 564 MB over 134 heartbeats (plateau ≈ 560 MB observed; unbounded growth not confirmed) |
| **ok** | watch errors | none (cumulative since start) |
| **ok** | exchangeInfo binance_spot | 3698 symbols, 15 snapshots, last change archived 2026-09-11T06:33:38.826695+00:00 (587 min ago; a quiet market changes rarely: age is not an alarm by itself) |
| **ok** | exchangeInfo binance_um | 897 symbols, 2 snapshots, last change archived 2026-09-11T05:08:02.302196+00:00 (673 min ago; a quiet market changes rarely: age is not an alarm by itself) |
| **warn** | lifecycle noise | 13685 events, by kind {'filters_change': 13674, 'status_change': 3, 'shortability_change': 8}, by change {'filters_changed': 13666, 'permissions_changed': 8}; max history per symbol 1, state 1.04 MB |
| **ok** | triggered windows | 16 windows (11 final, 5 in progress), mean completeness of final 0.8635, missing fields top {'depth_50bps_bid_usd': 6, 'depth_50bps_ask_usd': 6, 'depth_25bps_bid_usd': 4, 'depth_25bps_ask_usd': 4, 'open_interest': 1, 'depth_10bps_bid_usd': 1, 'depth_10bps_ask_usd': 1, 'imbalance_10bps': 1}, reconnects 18, 1365.0 MB |
| **warn** | first_* timestamps in final captures | missing counts {'first_oi_ts': 1} — a capture without them is debug material, not alpha material |
| **warn** | captures past their window without final manifest | ['liquidation_burst_BEATUSDT_20260911T050415'] |
| **warn** | trigger journal volume | 15279 decisions, 13 fired ({'liquidation_burst': 11, 'funding_extreme': 2}), skipped by reason {'max_concurrent_captures': 12368, 'cooldown': 2891, 'spot_symbol_no_ws_capture': 3} |
| **ok** | disk | tape 1741.4 MB, free 43.0 GB of 982.3 GB |
| **ok** | real perp births captured so far | 0 USDS-M births since the watch started; new_perp_listing captures fired: 0 |
