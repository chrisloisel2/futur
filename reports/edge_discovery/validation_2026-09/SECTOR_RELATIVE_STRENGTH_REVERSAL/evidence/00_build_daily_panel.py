"""V5 wave-2 validation -- step 0: build an independent DAILY panel from the raw
5-minute Binance USDM perp OHLCV in data_v2 (own aggregation, no precomputed
feature reused). One DuckDB query per symbol to stay inside the resource budget
(memory_limit 1200MB, threads 2). Output: one small parquet in the V5 scratch.

daily_close  = close of the LAST 5m bar of the UTC calendar day (arg_max on ts)
quote_volume = sum(quote_asset_volume) over the day (USDT dollar volume)
n_bars       = number of 5m bars present that day (completeness diagnostic)
"""
import os, sys, json, time
import duckdb, pandas as pd

ROOT = "/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance"
OUT = "/tmp/claude-1000/-home-qbee-futur/df793692-b596-4e93-91e2-bc55f257c909/scratchpad/V5_SECTOR/daily_panel.parquet"

con = duckdb.connect()
con.execute("SET memory_limit='1200MB'; SET threads=2;")
syms = sorted(d.replace("symbol=", "") for d in os.listdir(ROOT) if d.startswith("symbol="))
frames = []
t0 = time.time()
for i, s in enumerate(syms):
    p = f"{ROOT}/symbol={s}/year=*/perp_5m.parquet"
    try:
        df = con.execute(f"""
            SELECT CAST(date_trunc('day', timestamp AT TIME ZONE 'UTC') AS DATE) AS date,
                   '{s}' AS symbol,
                   arg_max(close, timestamp) AS close,
                   arg_min(open, timestamp)  AS open,
                   max(high) AS high, min(low) AS low,
                   sum(quote_asset_volume) AS quote_volume,
                   sum(volume) AS base_volume,
                   count(*) AS n_bars,
                   min(timestamp) AS first_ts, max(timestamp) AS last_ts
            FROM read_parquet('{p}')
            GROUP BY 1 ORDER BY 1
        """).df()
        frames.append(df)
    except Exception as e:
        print("ERR", s, e, file=sys.stderr)
    if (i + 1) % 25 == 0:
        print(f"{i+1}/{len(syms)} symbols, {time.time()-t0:.0f}s", flush=True)
panel = pd.concat(frames, ignore_index=True)
panel["date"] = pd.to_datetime(panel["date"])
panel.to_parquet(OUT, index=False)
print("rows", len(panel), "symbols", panel.symbol.nunique(), "range", panel.date.min(), panel.date.max())
print("written", OUT, os.path.getsize(OUT) / 1e6, "MB")
