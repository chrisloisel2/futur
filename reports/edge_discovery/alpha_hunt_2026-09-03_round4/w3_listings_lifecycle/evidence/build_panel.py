#!/usr/bin/env python3
"""
W3_LISTINGS_LIFECYCLE — build_panel.py
Construit le panel DAILY PIT (312 symboles binance perp) + le calendrier de vie.
Lecture seule sur data/ et /home/qbee/futur-data-v2/. Ecrit uniquement dans OUT_DIR (scratch).

Sorties (scratch, < 200 Mo) :
  daily_panel.parquet   : date, symbol, close, quote_vol, n_trades, taker_buy_quote,
                          ret_d, hi, lo, amihud, funding_d, oi_d, basis_d
  life.parquet          : symbol, onboard_ts, status, is_dead, last_ts (dernière barre du panel)
"""
import os, sys, glob, duckdb

ROOT = "/home/qbee/futur"
DV2 = "/home/qbee/futur-data-v2/data_v2/normalized"
OUT = os.environ.get("W3_SCRATCH", "/tmp/w3_scratch")
os.makedirs(OUT, exist_ok=True)

con = duckdb.connect()
con.execute(f"SET temp_directory='{OUT}/duckdb_tmp'")
con.execute("SET memory_limit='6GB'")
con.execute("SET threads=6")

PERP = f"{DV2}/perp_ohlcv/venue=binance/symbol=*/year=*/perp_5m.parquet"
EFP  = f"{DV2}/event_feature_panel/venue=binance/symbol=*/year=*/event_feature_panel_5m.parquet"
CAL  = f"{ROOT}/data/listings_backfill/binance/listings_calendar.parquet"

print("[1/3] daily OHLCV/volume from perp_5m ...", flush=True)
con.execute(f"""
CREATE OR REPLACE TABLE px AS
SELECT
  CAST(timestamp AT TIME ZONE 'UTC' AS DATE)          AS date,
  symbol,
  arg_min(open,  timestamp)                            AS open,
  arg_max(close, timestamp)                            AS close,
  max(high)                                            AS hi,
  min(low)                                             AS lo,
  sum(quote_asset_volume)                              AS quote_vol,
  sum(number_of_trades)                                AS n_trades,
  sum(taker_buy_quote_asset_volume)                    AS taker_buy_quote,
  count(*)                                             AS n_bars
FROM read_parquet('{PERP}', hive_partitioning=1)
GROUP BY 1,2
""")
print("   px rows:", con.execute("select count(*) from px").fetchone()[0], flush=True)

print("[2/3] daily derivatives from event_feature_panel ...", flush=True)
con.execute(f"""
CREATE OR REPLACE TABLE dv AS
SELECT
  CAST(timestamp AT TIME ZONE 'UTC' AS DATE)  AS date,
  symbol,
  avg(funding_rate)                            AS funding_d,
  avg(funding_rate_percentile_90d)             AS funding_pct90_d,
  avg(oi)                                      AS oi_d,
  avg(basis)                                   AS basis_d,
  avg(abs(basis_z_7d))                         AS abs_basis_z7_d,
  avg(residual_std_30d)                        AS resid_std30_d,
  sum(aggressive_buy_usd)                      AS agg_buy_usd,
  sum(aggressive_sell_usd)                     AS agg_sell_usd
FROM read_parquet('{EFP}', hive_partitioning=1)
GROUP BY 1,2
""")
print("   dv rows:", con.execute("select count(*) from dv").fetchone()[0], flush=True)

print("[3/3] join + causal features ...", flush=True)
con.execute(f"""
CREATE OR REPLACE TABLE panel AS
WITH j AS (
  SELECT px.*, dv.funding_d, dv.funding_pct90_d, dv.oi_d, dv.basis_d,
         dv.abs_basis_z7_d, dv.resid_std30_d, dv.agg_buy_usd, dv.agg_sell_usd
  FROM px LEFT JOIN dv USING (date, symbol)
),
r AS (
  SELECT *,
    lag(close) OVER w                         AS prev_close,
    close / NULLIF(lag(close) OVER w,0) - 1   AS ret_d
  FROM j WINDOW w AS (PARTITION BY symbol ORDER BY date)
)
SELECT *,
  CASE WHEN quote_vol > 0 THEN abs(ret_d)/(quote_vol/1e6) END AS amihud
FROM r
""")
con.execute(f"COPY (SELECT * FROM panel ORDER BY symbol, date) TO '{OUT}/daily_panel.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
print("   panel rows:", con.execute("select count(*) from panel").fetchone()[0], flush=True)

# calendrier de vie restreint au panel
con.execute(f"""
CREATE OR REPLACE TABLE life AS
SELECT c.symbol,
       c.onboard_ts,
       CAST(c.onboard_ts AT TIME ZONE 'UTC' AS DATE) AS onboard_date,
       c.status,
       (c.status IN ('SETTLING','DELISTED','DELISTED_NO_DATA')) AS is_dead,
       p.last_date, p.first_date, p.n_days
FROM '{CAL}' c
JOIN (SELECT symbol, max(date) last_date, min(date) first_date, count(*) n_days
      FROM panel GROUP BY 1) p USING (symbol)
""")
con.execute(f"COPY (SELECT * FROM life ORDER BY symbol) TO '{OUT}/life.parquet' (FORMAT PARQUET)")
print(con.execute("select status, count(*) from life group by 1 order by 2 desc").df().to_string())
print("OK ->", OUT)
