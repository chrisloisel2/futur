#!/usr/bin/env python
"""W1_CALENDAR_CLOCK — build compact clock panels from event_feature_panel.

Writes 3 small parquets to SCRATCH (never to data/ or reports/):
  daily_liquidity.parquet : (symbol, d) dollar volume + bar count  -> universe eligibility
  hourly.parquet          : (symbol, hour_ts) close/dv/oi/funding/basis -> Family B/C/D
  funding_events.parquet  : one row per (symbol, settlement) with the 6 role prices -> Family A

PIT: a 5m row labelled T covers [T,T+5m); close(T) is the price at T+5m, available T+5m05s.
     => one full bar of implementation lag between signal row and entry price.
TZ:  DuckDB renders tz-aware timestamps in LOCAL time by default. SET TimeZone='UTC' is
     mandatory on this axis and is asserted below.
"""
import os, sys, time
import duckdb

SCRATCH = "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w1"
GLOB = "/home/qbee/futur-data-v2/data_v2/normalized/event_feature_panel/venue=binance/symbol=*/year=*/*.parquet"
os.makedirs(SCRATCH, exist_ok=True)

con = duckdb.connect()
con.execute("SET TimeZone='UTC'")
con.execute("SET memory_limit='10GB'")
con.execute("SET preserve_insertion_order=false")

# --- TZ assertion: 08:00 UTC must render as hour 8 -------------------------------
tzchk = con.execute(
    "SELECT hour(TIMESTAMPTZ '2025-03-01 08:00:00+00') AS h"
).fetchone()[0]
assert tzchk == 8, f"TIMEZONE NOT UTC (got hour={tzchk}) — abort, every clock bucket would be shifted"
print("TZ assertion OK: 08:00Z renders as hour 8")

t0 = time.time()

# --- 1. daily liquidity (universe eligibility) -----------------------------------
con.execute(f"""
COPY (
  SELECT symbol,
         date_trunc('day', timestamp) AS d,
         sum(volume*close)            AS dv_usd,
         count(*) FILTER (WHERE volume>0 AND close IS NOT NULL) AS n_bars
  FROM read_parquet('{GLOB}')
  WHERE close IS NOT NULL
  GROUP BY 1,2
) TO '{SCRATCH}/daily_liquidity.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
""")
print("daily_liquidity done", round(time.time()-t0,1))

# --- 2. hourly panel (Families B/C/D/F) ------------------------------------------
# close_at_hour = close of the :55 bar = price exactly at the hour boundary.
con.execute(f"""
COPY (
  SELECT symbol,
         date_trunc('hour', timestamp) + INTERVAL 1 HOUR      AS hour_end,   -- price timestamp
         max(close) FILTER (WHERE minute(timestamp)=55)       AS close_at_hour_end,
         max(close) FILTER (WHERE minute(timestamp)=0)        AS close_first5,
         sum(volume*close)                                    AS dv_usd,
         max(oi)    FILTER (WHERE minute(timestamp)=55)       AS oi_end,
         max(funding_rate) FILTER (WHERE minute(timestamp)=55) AS funding_rate,
         max(basis) FILTER (WHERE minute(timestamp)=55)       AS basis,
         sum(residual_logret_5m)                              AS resid_logret_hour,
         count(*) FILTER (WHERE close IS NOT NULL)            AS n_bars
  FROM read_parquet('{GLOB}')
  WHERE close IS NOT NULL
  GROUP BY 1,2
) TO '{SCRATCH}/hourly.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
""")
print("hourly done", round(time.time()-t0,1))

# --- 3. funding-clock event panel (Family A) -------------------------------------
# Settlements at 00:00/08:00/16:00 UTC. off = seconds into the current 8h block.
# roles (row timestamp T -> what its close price is):
#   off=24900 (F-65m) signal row  : state at F-60m  (close = price @ F-60m)
#   off=25200 (F-60m) entry_pre   : close = price @ F-55m
#   off=28500 (F-5m ) exit_pre    : close = price @ F      (state at F)
#   off=0     (F    ) entry_post  : close = price @ F+5m   (funding just settled, is_settlement)
#   off=300   (F+5m ) entry_postb : close = price @ F+10m
#   off=3300  (F+55m) exit_post   : close = price @ F+60m
con.execute(f"""
CREATE OR REPLACE TABLE roles AS
SELECT symbol, timestamp,
       CAST(epoch(timestamp) AS BIGINT) % 28800 AS off,
       CASE WHEN CAST(epoch(timestamp) AS BIGINT) % 28800 >= 14400
            THEN timestamp + to_seconds(28800 - (CAST(epoch(timestamp) AS BIGINT) % 28800))
            ELSE timestamp - to_seconds(CAST(epoch(timestamp) AS BIGINT) % 28800)
       END AS F,
       close, funding_rate, funding_rate_percentile_90d, funding_is_settlement,
       basis, oi, volume
FROM read_parquet('{GLOB}')
WHERE close IS NOT NULL
  AND (CAST(epoch(timestamp) AS BIGINT) % 28800) IN (24900, 25200, 27600, 28500, 0, 300, 900, 3300, 6900)
""")
n = con.execute("SELECT count(*) FROM roles").fetchone()[0]
print("roles rows", n, round(time.time()-t0,1))

# sanity: the off=0 rows must be the settlement bars
chk = con.execute("""SELECT avg(CASE WHEN funding_is_settlement THEN 1.0 ELSE 0.0 END)
                     FROM roles WHERE off=0 AND funding_rate IS NOT NULL""").fetchone()[0]
print("frac of off=0 rows flagged funding_is_settlement:", round(chk, 4))

con.execute(f"""
COPY (
  SELECT symbol, F,
         hour(F) AS settle_hour,
         max(funding_rate)                 FILTER (WHERE off=24900) AS fr_prev,       -- PIT signal
         max(funding_rate_percentile_90d)  FILTER (WHERE off=24900) AS fr_pct90,
         max(basis)                        FILTER (WHERE off=24900) AS basis_sig,
         max(oi)                           FILTER (WHERE off=24900) AS oi_sig,
         max(close)                        FILTER (WHERE off=24900) AS p_sig,         -- @F-60m
         max(close)                        FILTER (WHERE off=25200) AS p_entry_pre,   -- @F-55m
         max(close)                        FILTER (WHERE off=28500) AS p_exit_pre,    -- @F
         max(basis)                        FILTER (WHERE off=28500) AS basis_at_F,
         max(funding_rate)                 FILTER (WHERE off=0)     AS fr_settled,    -- settled @F
         max(close)                        FILTER (WHERE off=0)     AS p_entry_post,  -- @F+5m
         max(close)                        FILTER (WHERE off=300)   AS p_entry_postb, -- @F+10m
         max(close)                        FILTER (WHERE off=3300)  AS p_exit_post,   -- @F+60m
         max(close)                        FILTER (WHERE off=27600) AS p_entry_pre15,  -- @F-15m
         max(close)                        FILTER (WHERE off=900)   AS p_exit_post20,  -- @F+20m
         max(close)                        FILTER (WHERE off=6900)  AS p_exit_post120, -- @F+120m
         count(*) AS n_roles
  FROM roles GROUP BY 1,2,3
) TO '{SCRATCH}/funding_events.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
""")
print("funding_events done", round(time.time()-t0,1))

for f in ["daily_liquidity", "hourly", "funding_events"]:
    p = f"{SCRATCH}/{f}.parquet"
    r = con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]
    print(f"  {f}: {r:,} rows, {os.path.getsize(p)/1e6:.0f} MB")
