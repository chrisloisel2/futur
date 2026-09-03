#!/usr/bin/env python3
"""
W3 — build_benchmark.py
Indice HORAIRE equal-weight de la coupe transversale ELIGIBLE (age>=30j, qvol median
roulant causal 30j >= 1M$), + serie BTC horaire. Sert de contrefactuel pour l'axe A :
sans lui, "les nouveaux listings baissent" ne se distingue pas de "les alts baissent".
Sortie scratch: bench_hourly.parquet (timestamp, idx_logret, n_eligible, btc_logret)
"""
import os, duckdb
DV2 = "/home/qbee/futur-data-v2/data_v2/normalized"
ROOT = "/home/qbee/futur"
OUT = os.environ["W3_SCRATCH"]
con = duckdb.connect(); con.execute(f"SET temp_directory='{OUT}/duckdb_tmp'")
con.execute("SET memory_limit='6GB'"); con.execute("SET threads=6")

PERP = f"{DV2}/perp_ohlcv/venue=binance/symbol=*/year=*/perp_5m.parquet"

# 1) eligibilite quotidienne PIT (fenetre fermee a gauche : shift 1 jour)
con.execute(f"""
CREATE OR REPLACE TABLE elig AS
WITH p AS (SELECT date, symbol, quote_vol FROM '{OUT}/daily_panel.parquet'),
     l AS (SELECT symbol, onboard_date FROM '{OUT}/life.parquet'),
     m AS (SELECT p.*, l.onboard_date,
             median(p.quote_vol) OVER (PARTITION BY p.symbol ORDER BY p.date
                                       ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING) AS qvol_med30_causal
           FROM p JOIN l USING (symbol))
SELECT date, symbol,
       (date_diff('day', onboard_date, date) >= 30 AND qvol_med30_causal >= 1e6) AS eligible
FROM m
""")
print("elig rows", con.execute("select count(*), sum(case when eligible then 1 else 0 end) from elig").fetchone())

# 2) log-returns horaires par symbole
con.execute(f"""
CREATE OR REPLACE TABLE hr AS
WITH h AS (
  SELECT date_trunc('hour', timestamp AT TIME ZONE 'UTC') AS ts_h, symbol,
         arg_max(close, timestamp) AS close
  FROM read_parquet('{PERP}', hive_partitioning=1) GROUP BY 1,2)
SELECT ts_h, symbol, ln(close / NULLIF(lag(close) OVER (PARTITION BY symbol ORDER BY ts_h),0)) AS lr
FROM h
""")

# 3) indice equal-weight des eligibles + BTC
con.execute(f"""
CREATE OR REPLACE TABLE bench AS
SELECT hr.ts_h AS ts_h,
       avg(hr.lr) FILTER (WHERE e.eligible) AS idx_logret,
       count(*)   FILTER (WHERE e.eligible) AS n_eligible,
       avg(hr.lr) FILTER (WHERE hr.symbol='BTCUSDT') AS btc_logret
FROM hr JOIN elig e ON e.symbol = hr.symbol AND e.date = CAST(hr.ts_h AS DATE)
WHERE hr.lr IS NOT NULL AND abs(hr.lr) < 1.0
GROUP BY 1 ORDER BY 1
""")
con.execute(f"COPY bench TO '{OUT}/bench_hourly.parquet' (FORMAT PARQUET)")
print(con.execute("select count(*) n, min(ts_h), max(ts_h), avg(n_eligible) from bench").df().to_string())
