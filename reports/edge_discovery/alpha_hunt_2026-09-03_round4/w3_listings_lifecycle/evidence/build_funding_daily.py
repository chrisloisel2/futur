#!/usr/bin/env python3
"""
W3 — build_funding_daily.py  (script de REPRODUCTIBILITE, ecrit apres coup)

Reconstruit `funding_daily.parquet` (funding effectivement REGLE, agrege au jour) a partir
de `event_feature_panel_5m` (venue=binance). Ce fichier etait consomme par run_axis_CD.py et
run_fixups.py mais son builder n'avait pas ete sauvegarde avant l'interruption de session.

Formule (verifiee identique a l'artefact d'origine sur BTCUSDT / SOLUSDT / 1000FLOKIUSDT :
max |delta funding_paid_d| = 0.0, n_settle_d identique) :
    funding_paid_d    = sum(funding_rate)      FILTER (funding_is_settlement)
    n_settle_d        = count(*)               FILTER (funding_is_settlement)
    abs_funding_avg_d = avg(abs(funding_rate)) FILTER (funding_is_settlement)

Convention de signe (importante pour A3/A5/C2) : funding_rate > 0 => les LONGS paient les
SHORTS. Donc le P&L de funding d'une position SHORT sur la fenetre = +somme(funding_rate).

Sortie : $W3_SCRATCH/funding_daily.parquet  (~4,6 Mo)
Cout : un scan streaming de event_feature_panel (14 Go) — ~10-20 min avec 2 threads.
"""
import os, duckdb

DV2 = "/home/qbee/futur-data-v2/data_v2/normalized"
OUT = os.environ["W3_SCRATCH"]
EFP = f"{DV2}/event_feature_panel/venue=binance/symbol=*/year=*/event_feature_panel_5m.parquet"

con = duckdb.connect()
con.execute(f"SET temp_directory='{OUT}/duckdb_tmp'")
con.execute("SET memory_limit='1500MB'")
con.execute("SET threads=2")
con.execute(f"""
COPY (
  SELECT CAST(timestamp AT TIME ZONE 'UTC' AS DATE)      AS date,
         symbol,
         sum(funding_rate)      FILTER (WHERE funding_is_settlement) AS funding_paid_d,
         count(*)               FILTER (WHERE funding_is_settlement) AS n_settle_d,
         avg(abs(funding_rate)) FILTER (WHERE funding_is_settlement) AS abs_funding_avg_d
  FROM read_parquet('{EFP}', hive_partitioning=1)
  GROUP BY 1, 2
  ORDER BY symbol, date
) TO '{OUT}/funding_daily.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
""")
print(con.execute(f"select count(*) n, count(distinct symbol) nsym, min(date), max(date) from '{OUT}/funding_daily.parquet'").df().to_string())
