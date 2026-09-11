# CURRENT COLLECTION INVENTORY — P4 market_state_tape (2026-09-11T16:20:37 UTC)

Root `/home/qbee/futur/data_lake/market_state` present: True. Best-effort scan; no price join, no signal, no verdict. Hashes are sha256 of the files named.

## exchangeInfo

| venue | symbols | snapshots archived | changes logged | by type | last change archived | last_view sha256 |
|---|---|---|---|---|---|---|
| binance_spot | 3698 | 15 | 16486 | {'filters_changed': 12769, 'symbol_added': 3698, 'permissions_changed': 8, 'margin_trading_changed': 8, 'status_changed': 3} | 2026-09-11T06:33:38.826695+00:00 | `203a3a13876f214d` |
| binance_um | 897 | 2 | 1794 | {'symbol_added': 897, 'filters_changed': 897} | 2026-09-11T05:08:02.302196+00:00 | `f77ed0f1e98982ae` |

## symbol_lifecycle_event

- events: **13685** (2026-09-10T23:00:40.258495+00:00 → 2026-09-11T06:33:39.320276+00:00); by kind {'filters_change': 13674, 'status_change': 3, 'shortability_change': 8}; by venue {'binance_spot': 12788, 'binance_um': 897}; by change {'filters_changed': 13666, 'permissions_changed': 8}
- state: 4521 symbols tracked, 0 dead, max history 1, 1.04 MB, sha256 `eadac8b473a19d60`
- births (last 10): none yet
- deaths (last 10): none yet
- status changes (last 10): [('2026-09-11T03:00:18.385297+00:00', 'binance_spot', 'SAGAFDUSD', 'TRADING', 'BREAK'), ('2026-09-11T03:00:18.390920+00:00', 'binance_spot', 'VELODROMEUSDC', 'TRADING', 'BREAK'), ('2026-09-11T03:00:18.395024+00:00', 'binance_spot', 'OPENFDUSD', 'TRADING', 'BREAK')]

## market_state_snapshot (watch)

| stream | files | rows | symbols | sources | first | last | size |
|---|---|---|---|---|---|---|---|
| watch_open_interest | 2 | 5269 | 566 | {'watch_open_interest': 5269} | 2026-09-10T22:50:48 | 2026-09-10T23:56:13 | 6.1 MB |
| watch_premium_index | 2 | 59400 | 900 | {'watch_premium_index': 59400} | 2026-09-10T22:50:48 | 2026-09-10T23:59:01 | 69.0 MB |

## triggered windows

- 16 windows (11 final, 5 in progress), by type {'funding_extreme': 2, 'liquidation_burst': 11, 'manual': 3}, mean completeness of final 0.8635, missing fields top {'depth_50bps_bid_usd': 6, 'depth_50bps_ask_usd': 6, 'depth_25bps_bid_usd': 4, 'depth_25bps_ask_usd': 4, 'open_interest': 1, 'depth_10bps_bid_usd': 1, 'depth_10bps_ask_usd': 1, 'imbalance_10bps': 1}, first_* missing in final {'first_oi_ts': 1}, reconnects 18, total 1365.0 MB

| window | type | symbol | start | final | duration s | snapshots | completeness | missing | first_* | reconnects | latency ms | pre-window missing | files hashed | size MB | manifest sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| funding_extreme_NEWTUSDT_20260911T050801 | funding_extreme | NEWTUSDT | 2026-09-11T05:08 | True | 21611 | 21542 | 0.9987 | - | 11111 | 0 | 114 | True | 8 | 26.7 | `f14e1c96e8fc` |
| funding_extreme_VTHOUSDT_20260911T111356 | funding_extreme | VTHOUSDT | 2026-09-11T11:13 | False | - | 18300 | 0.9966 | - | 11111 | 0 | 169 | True | 0 | 84.5 | `abf24921f7a6` |
| liquidation_burst_AKEUSDT_20260911T051018 | liquidation_burst | AKEUSDT | 2026-09-11T05:10 | True | 21611 | 21542 | 0.996 | - | 11111 | 0 | 113 | True | 8 | 113.4 | `de24da87e09d` |
| liquidation_burst_BEATUSDT_20260911T050415 | liquidation_burst | BEATUSDT | 2026-09-11T05:04 | False | - | 180 | 0.9991 | - | 11111 | 0 | 112 | True | 0 | 0.7 | `2f5f8b9b9b82` |
| liquidation_burst_BTCUSDT_20260910T225909 | liquidation_burst | BTCUSDT | 2026-09-10T22:59 | True | 21611 | 21546 | 0.7866 | depth_25bps_bid_usd, depth_25bps_ask_usd, depth_50bps_bid_usd, depth_50bps_ask_usd | 11111 | 4 | 111 | True | 15 | 161.6 | `32207f28cb39` |
| liquidation_burst_ETHUSDT_20260910T225909 | liquidation_burst | ETHUSDT | 2026-09-10T22:59 | True | 21611 | 21548 | 0.8848 | depth_50bps_bid_usd, depth_50bps_ask_usd | 11111 | 4 | 111 | True | 15 | 176.1 | `b545194691bb` |
| liquidation_burst_LABUSDT_20260911T112406 | liquidation_burst | LABUSDT | 2026-09-11T11:24 | False | - | 17700 | 0.9968 | - | 11111 | 1 | 180 | True | 0 | 97.6 | `dae118d6b4a9` |
| liquidation_burst_METUSDT_20260911T111845 | liquidation_burst | METUSDT | 2026-09-11T11:18 | False | - | 18060 | 1.0 | - | 11111 | 0 | 111 | True | 0 | 94.1 | `1a5866ddde4f` |
| liquidation_burst_PUMPUSDT_20260910T225710 | liquidation_burst | PUMPUSDT | 2026-09-10T22:57 | True | 21609 | 21547 | 1.0 | - | 11111 | 3 | 111 | True | 15 | 90.2 | `c0d6e934af45` |
| liquidation_burst_RAYSOLUSDT_20260911T051618 | liquidation_burst | RAYSOLUSDT | 2026-09-11T05:16 | True | 21609 | 21545 | 0.9934 | - | 11111 | 0 | 114 | True | 8 | 129.3 | `fcac9c38f5f6` |
| liquidation_burst_ZECUSDT_20260910T225847 | liquidation_burst | ZECUSDT | 2026-09-10T22:58 | True | 21611 | 21540 | 0.9877 | - | 11111 | 4 | 111 | True | 15 | 161.8 | `fa4c24241e99` |
| liquidation_burst_牛来USDT_20260911T050920 | liquidation_burst | 牛来USDT | 2026-09-11T05:09 | True | 21609 | 21553 | 0.7368 | depth_50bps_bid_usd, depth_50bps_ask_usd, open_interest | 11110 | 0 | 114 | True | 6 | 125.9 | `f1f433d2e143` |
| liquidation_burst_牛来USDT_20260911T111916 | liquidation_burst | 牛来USDT | 2026-09-11T11:19 | False | - | 17999 | 0.7368 | depth_50bps_bid_usd, depth_50bps_ask_usd, open_interest | 11110 | 2 | 112 | True | 0 | 102.4 | `bd153b910ebc` |
| manual_BTCUSDT_20260910T224418_f9bb40 | manual | BTCUSDT | 2026-09-10T22:44 | True | 97 | 90 | 0.6292 | depth_10bps_bid_usd, depth_10bps_ask_usd, depth_25bps_bid_usd, depth_25bps_ask_usd, depth_50bps_bid_usd, depth_50bps_ask_usd, imbalance_10bps | 11111 | 0 | 111 | True | 5 | 0.1 | `8d579682c4cb` |
| manual_BTCUSDT_20260910T224938_ad8af1 | manual | BTCUSDT | 2026-09-10T22:49 | True | 70 | 60 | 0.7 | depth_25bps_bid_usd, depth_25bps_ask_usd, depth_50bps_bid_usd, depth_50bps_ask_usd | 11111 | 0 | 113 | True | 7 | 0.3 | `67a33cc82b03` |
| manual_BTCUSDT_20260910T225257_07b0f1 | manual | BTCUSDT | 2026-09-10T22:52 | True | 49 | 40 | 0.7855 | depth_25bps_bid_usd, depth_25bps_ask_usd, depth_50bps_bid_usd, depth_50bps_ask_usd | 11111 | 0 | 110 | True | 7 | 0.3 | `a594526d2b1d` |

(first_* column order: orderbook, trade, mark, index, oi; 1 = present)

## triggers

- 15279 decisions, **13 captures fired** ({'liquidation_burst': 11, 'funding_extreme': 2}), 4 logged-only (dry runs), skipped by reason {'max_concurrent_captures': 12368, 'cooldown': 2891, 'spot_symbol_no_ws_capture': 3}, by type {'funding_extreme': 2621, 'liquidation_burst': 12653, 'status_change': 3, 'oi_spike': 2}, last 2026-09-11T16:20:33.475161+00:00, journal sha256 `1f89301d14f9da80`

## watch.log

- heartbeats 134 (2026-09-11T05:12:58.538697+00:00 → 2026-09-11T16:19:33.771181+00:00), RSS first/last/max 347/564/564 MB, errors {}, polls {'spot_polls': 650, 'oi_polls': 50940, 'premium_polls': 667, 'ticker_polls': 135, 'um_polls': 6680}, force orders seen 22868, light snapshots 600300, active captures 4

## disk

- tape 1741.4 MB; free 43.0 GB of 982.3 GB
