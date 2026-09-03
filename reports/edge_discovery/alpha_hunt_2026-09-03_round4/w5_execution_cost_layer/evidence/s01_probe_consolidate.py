"""W5/s01 - consolidate execution_probe into one scratch parquet + tick-size table."""
import duckdb, os, sys, json
SCRATCH = os.environ.get("W5_SCRATCH", "/tmp/w5")
os.makedirs(SCRATCH, exist_ok=True)
con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute(f"""
COPY (
  SELECT symbol, side, "limit" AS lim, spread_bps, filled, ttf_s,
         adv_bps_60s, adv_bps_300s, mid_at_place,
         CAST(ts_place AS TIMESTAMP) AS ts
  FROM read_parquet('data/execution_probe/date=*/part-*.parquet', union_by_name=true)
) TO '{SCRATCH}/probe.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
""")
# tick size inferred from the empirical price grid (min positive diff of distinct limits)
tick = con.execute(f"""
WITH d AS (SELECT DISTINCT symbol, lim FROM read_parquet('{SCRATCH}/probe.parquet')),
     g AS (SELECT symbol, lim - lag(lim) OVER (PARTITION BY symbol ORDER BY lim) AS dp FROM d)
SELECT symbol, min(dp) AS tick FROM g WHERE dp > 1e-12 GROUP BY symbol ORDER BY symbol
""").df()
med = con.execute(f"SELECT symbol, median(mid_at_place) m FROM read_parquet('{SCRATCH}/probe.parquet') GROUP BY symbol").df()
t = tick.merge(med, on="symbol"); t["tick_bps"] = t["tick"] / t["m"] * 1e4
t.to_csv(f"{SCRATCH}/ticks.csv", index=False)
print(t.to_string())
print("rows:", con.execute(f"SELECT count(*) FROM read_parquet('{SCRATCH}/probe.parquet')").fetchone())
