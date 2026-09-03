"""W5/s06 - compact microstructure_reduced (jsonl.gz) into per-(venue,symbol,date) parquet.
BBO decimated to 100ms buckets (last quote in bucket) - markouts start at 1s so this is lossless
for every downstream use. Trades kept at full resolution (queue consumption needs them exact).
"""
import duckdb, os, sys, glob
S = os.environ["W5_SCRATCH"]; os.makedirs(f"{S}/micro", exist_ok=True)
con = duckdb.connect(); con.execute("PRAGMA threads=8"); con.execute("PRAGMA memory_limit='6GB'")
venue, symbol, date = sys.argv[1], sys.argv[2], sys.argv[3]
b = f"data/microstructure_reduced/raw/bbo/venue={venue}/symbol={symbol}/date={date}/*.jsonl.gz"
t = f"data/microstructure_reduced/raw/trades/venue={venue}/symbol={symbol}/date={date}/*.jsonl.gz"
ob, ot = f"{S}/micro/bbo_{venue}_{symbol}_{date}.parquet", f"{S}/micro/trd_{venue}_{symbol}_{date}.parquet"
if not os.path.exists(ob):
    con.execute(f"""COPY (
      SELECT bucket*100000000 AS ts, last(bid_price ORDER BY event_ts_ns) bid, last(ask_price ORDER BY event_ts_ns) ask,
             last(bid_qty ORDER BY event_ts_ns) bq, last(ask_qty ORDER BY event_ts_ns) aq,
             min(bid_price) bid_lo, max(ask_price) ask_hi, count(*) nq
      FROM (SELECT *, event_ts_ns//100000000 AS bucket FROM read_json_auto('{b}', ignore_errors=true))
      WHERE bid_price>0 AND ask_price>bid_price GROUP BY bucket ORDER BY bucket
    ) TO '{ob}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
if not os.path.exists(ot):
    con.execute(f"""COPY (
      SELECT event_ts_ns AS ts, price, qty, side FROM read_json_auto('{t}', ignore_errors=true) ORDER BY event_ts_ns
    ) TO '{ot}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
print(venue, symbol, date,
      "bbo100ms=", con.execute(f"SELECT count(*) FROM '{ob}'").fetchone()[0],
      "trades=",   con.execute(f"SELECT count(*) FROM '{ot}'").fetchone()[0])
