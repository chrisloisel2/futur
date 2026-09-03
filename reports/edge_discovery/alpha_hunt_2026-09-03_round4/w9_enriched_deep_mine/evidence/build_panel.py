#!/usr/bin/env python3
"""W9 Phase 2 — extrait un panel compact (colonnes USABLE uniquement) depuis data/enriched.
Streaming symbole par symbole, ecrit UN parquet float32 (<400 Mo). Ne copie jamais les 86 Go."""
import duckdb, glob, os, sys
import pyarrow as pa, pyarrow.parquet as pq
OUT=os.environ.get("W9_OUT","/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
FEATS=["volatility_percentile_20","volatility_percentile_50","bollinger_squeeze_20","ttm_squeeze_20",
 "range_compression_20","efficiency_ratio_20","efficiency_ratio_50","directional_persistence_20",
 "upper_wick_range","lower_wick_range","volume_percentile_20","volume_percentile_50",
 "close_position_in_range","volume_zscore_20","zscore_return_20","rsi_14","atr_percent_20",
 "realized_volatility_20","realized_volatility_50","choppiness_index_20","volume_ratio_20",
 "trend_strength_20","bollinger_width_20","adx_14","volatility_compression_20",
 "intrabar_range_expansion_20","rolling_autocorrelation_return_20","minmax_norm_close_20",
 "hurst_exponent_50","hour_of_day"]
BASE=["datetime","open","high","low","close","volume"]
# exclusions issues de l'AUDIT phase 1 (hygiene de source, pas un refit de seuil)
DROP_SYMBOLS={"RNDRUSDT"}                 # serie morte 2024-07
TRUNCATE={"MKRUSDT":"2025-09-08"}          # volume=0 ensuite
SPOT_SOURCED={"DOGEUSDT","XRPUSDT"}        # spot, pas perp (verifie vs data_v2)
con=duckdb.connect(); con.execute("PRAGMA threads=4; SET memory_limit='6GB';")
writer=None; ntot=0
for f in sorted(glob.glob("data/enriched/*_1h_enriched.parquet")):
    s=os.path.basename(f).split("_")[0]
    if s in DROP_SYMBOLS: continue
    have=set(pq.ParquetFile(f).schema_arrow.names)
    cols=[c for c in BASE+FEATS if c in have]
    miss=[c for c in FEATS if c not in have]
    where=""
    if s in TRUNCATE: where=f"WHERE datetime < TIMESTAMPTZ '{TRUNCATE[s]}'"
    sel=",".join([f'"{c}"' for c in cols])
    df=con.execute(f"SELECT {sel} FROM read_parquet('{f}') {where} ORDER BY datetime").fetchdf()
    for c in miss: df[c]=float("nan")
    df=df[BASE+FEATS]
    df.insert(1,"symbol",s)
    df.insert(2,"src_spot", 1 if s in SPOT_SOURCED else 0)
    for c in FEATS+["open","high","low","close","volume"]:
        df[c]=df[c].astype("float32")
    t=pa.Table.from_pandas(df,preserve_index=False)
    if writer is None:
        writer=pq.ParquetWriter(OUT+"/panel.parquet", t.schema, compression="zstd")
    writer.write_table(t); ntot+=len(df)
    print(f"{s}: {len(df)} rows, missing_feats={len(miss)}", flush=True)
writer.close()
print("TOTAL rows:",ntot, "size:", os.path.getsize(OUT+"/panel.parquet")//1024//1024, "MB")
