# Data Layer Contracts

## Parquet schemas

### RawEventStream (data/raw/*)
- Partitioning: dt=YYYY-MM-DD, symbol, venue, source
- Columns: event_time (timestamp[ms, UTC]), recv_time (timestamp[ms, UTC]), event_time_aligned (timestamp[ms, UTC]), skew_ms (int64), symbol (string), venue (string), source (string: spot|futures|book|options|macro|cross_venue), event_type (string), seq (int64), ingest_run_id (string), payload_version (int32), is_snapshot (bool)
- Trades: trade_id (string), price (float64), qty (float64), side (string), is_maker (bool), trade_flags (int32)
- Book: update_id (int64), depth (int32), bid_px/bid_sz/ask_px/ask_sz (list<float64>), mid_price (float64), spread (float64), checksum (string), book_flags (int32)
- OHLCV: bar_start (timestamp[ms]), bar_end (timestamp[ms]), bar_size_s (int32), open/high/low/close/volume (float64), trades_count (int64)
- Futures: funding_rate (float64), funding_time (timestamp[ms]), open_interest (float64), liq_side (string), liq_qty (float64), liq_price (float64)
- Macro: macro_symbol (string), value (float64)
- Cross-venue: ref_venue (string), ref_price (float64), target_venue (string), target_price (float64), premium_bps (float64), basis_bps (float64), cross_source_ok (bool), cross_source_error_code (int32)

### CleanEventStream (data/clean/*)
- Same partitioning and base columns as Raw plus: is_valid (bool), quality_flags (int64), staleness_ms (int64)

### Features (data/features/*)
- Partitioning: dt, symbol, horizon_group
- Columns: event_time (timestamp[ms]), symbol (string), x_fast_*, x_mid_*, s_slow_*, quality_* (numeric), feature_set (string)

### Labels (data/labels/*)
- Partitioning: dt, symbol, label_set
- Columns: t0 (timestamp[ms]), symbol (string), horizon_s (int32), tp_bps (float64), sl_bps (float64), tp_hit (bool), sl_hit (bool), time_stop (bool), barrier_hit (string), return_fwd (float64), mfe (float64), mae (float64), duration_ms (int64), label_set (string)

### Backtest outputs (artifacts/backtests/{run_id}/)
- trades.parquet, fills.parquet, equity_curve.parquet as documented in research pipeline.

## Examples
- Raw trade: event_time=2024-01-01T00:00:00Z, recv_time=2024-01-01T00:00:00.010Z, event_time_aligned=2024-01-01T00:00:00.010Z, skew_ms=10, symbol=BTCUSDT, venue=binance, source=spot, event_type=trade, trade_id=abc, price=42000.1, qty=0.01, side=buy, ingest_run_id=20240101_000000_ingest, payload_version=1, is_snapshot=false, dt=2024-01-01.
- Book snapshot: includes bid_px/bid_sz/ask_px/ask_sz arrays length<=depth, mid_price/spread computed, is_snapshot=true.
