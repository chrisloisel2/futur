"""Build a daily OHLCV panel from data_v2/normalized/perp_ohlcv (5m bars, binance venue),
out-of-core via duckdb, single pass, written once to scratchpad parquet for reuse.
Mirrors W1's (alpha_hunt_2026-08-30) approach: no giant panel kept in repo, just scratchpad.
"""
import duckdb
import time

t0 = time.time()
con = duckdb.connect()
con.execute("PRAGMA threads=8")

q = """
COPY (
  SELECT
    symbol,
    date_trunc('day', timestamp) AS day,
    arg_max(close, timestamp) AS close,
    arg_min(open, timestamp) AS open,
    sum(quote_asset_volume) AS quote_volume,
    count(*) AS n_bars
  FROM parquet_scan('/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance/**/*.parquet', hive_partitioning=1)
  GROUP BY symbol, day
) TO '/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w5/evidence/daily_ohlcv.parquet' (FORMAT PARQUET)
"""
con.execute(q)
print("daily_ohlcv built in", round(time.time() - t0, 1), "s")
