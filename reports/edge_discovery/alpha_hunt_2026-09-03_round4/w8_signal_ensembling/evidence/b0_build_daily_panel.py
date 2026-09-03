"""W8 / Track B step 0 - build a daily perp OHLCV panel from data_v2/normalized/perp_ohlcv
(5m bars, binance venue) out-of-core via duckdb. Written ONCE to scratch (never to data/ or
reports/, per BRIEFING section 5 disk constraint). Read-only on the source.

Adds high/low/n_trades vs the round-3 W5 version because Amihud, MAX-effect and range
signals need them.
"""
import duckdb, sys, time

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/w8_daily_ohlcv.parquet"
t0 = time.time()
con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='12GB'")
q = f"""
COPY (
  SELECT
    symbol,
    CAST(date_trunc('day', timestamp AT TIME ZONE 'UTC') AS DATE) AS day,
    arg_min(open, timestamp)               AS open,
    max(high)                              AS high,
    min(low)                               AS low,
    arg_max(close, timestamp)              AS close,
    sum(quote_asset_volume)                AS quote_volume,
    sum(number_of_trades)                  AS n_trades,
    count(*)                               AS n_bars
  FROM parquet_scan('/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance/**/*.parquet',
                    hive_partitioning=1)
  GROUP BY 1, 2
) TO '{OUT}' (FORMAT PARQUET)
"""
con.execute(q)
n = con.execute(f"SELECT count(*), count(distinct symbol), min(day), max(day) FROM parquet_scan('{OUT}')").fetchone()
print("daily panel built in", round(time.time() - t0, 1), "s ->", OUT, n)
