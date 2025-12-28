# Research & Validation Data Contracts

## Parquet layout

### Raw events (`s3://.../data/raw/{source}/`)
- Partitioning: dt=YYYY-MM-DD, symbol=SYMBOL, source=spot|futures|book|options|macro|cross_venue
- Columns: event_time (timestamp[ms]), recv_time (timestamp[ms]), symbol (string), venue (string), source (string), event_type (string), seq (int64), payload (binary|string)
- Trades: price (float64), qty (float64), side (string), trade_id (string), is_maker (bool)
- Book L2: bid_px/ask_px (list<float64>), bid_sz/ask_sz (list<float64>), checksum (string), update_id (string|int64)
- OHLCV: open, high, low, close, volume (float64), bar_size_s (int32)
- Futures: funding_rate, open_interest, liq_side, liq_qty, liq_price (numeric/string)
- Macro: macro_symbol (string), value (float64)

### Clean events (`s3://.../data/clean/`)
- Partitioning: dt, symbol, source
- Columns: raw columns + is_valid (bool), quality_flags (int64), event_time_aligned (timestamp[ms]), mid_price (float64), spread (float64), staleness_ms (int64)

### Features (`s3://.../data/features/`)
- Partitioning: dt, symbol, horizon_group=fast|mid|slow
- Columns: event_time (timestamp[ms]), symbol (string), x_fast_*, x_mid_*, s_slow_*, quality_* (numeric)
- Examples: x_fast_spread, x_fast_imbalance, x_mid_ret_1m, x_mid_rv_5m, s_slow_funding_z, s_slow_macro_risk_on

### Labels (`s3://.../data/labels/`)
- Partitioning: dt, symbol, label_set=v1
- Columns: t0 (timestamp[ms]), symbol (string), horizon_s (int32), tp_bps (float64), sl_bps (float64), tp_hit (bool), sl_hit (bool), time_stop (bool), barrier_hit (string: tp|sl|time), return_fwd (float64), mfe (float64), mae (float64), duration_ms (int64), label_set (string)

### Backtest outputs (`s3://.../artifacts/backtests/{run_id}/`)
- trades.parquet: trade_id (string), symbol (string), book (string), t_entry (timestamp[ms]), t_exit (timestamp[ms]), side (string), qty (float64), entry_px (float64), exit_px (float64), gross_pnl (float64), net_pnl (float64), fees (float64), slippage (float64), reason_exit (string), run_id (string)
- equity_curve.parquet: event_time (timestamp[ms]), equity (float64), drawdown (float64), exposure_gross (float64), exposure_net (float64), run_id (string)
- fills.parquet: fill_id (string), order_id (string), event_time (timestamp[ms]), symbol (string), side (string), qty (float64), px (float64), fee (float64), liquidity (string: maker|taker), latency_ms (int64), partial (bool)

### State (`s3://.../data/state/`)
- Columns: event_time (timestamp[ms]), symbol (string), position (float64), cash (float64), equity (float64), exposure_gross (float64), exposure_net (float64), quality_flags (int64), regime (string), feature_set (string), label_set (string)

## Versioning
- run_id = YYYYMMDD_HHMMSS_<gitsha>_<tag>
- All artifacts include run_id and feature_set/label_set where applicable.
