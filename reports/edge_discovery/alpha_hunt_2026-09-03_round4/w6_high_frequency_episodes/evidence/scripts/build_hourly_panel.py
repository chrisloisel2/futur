#!/usr/bin/env python3
"""W6 round-4: build a compact HOURLY decision panel from the PIT 5m event_feature_panel.

PIT contract
------------
* Every feature at decision hour H is built ONLY from panel rows at timestamp <= H.
  The panel stamps research_available_at = timestamp + 305s (verified constant), so a
  signal read at H is knowable at H+5m05s.
* Entry is assumed at the CLOSE of bar H+10m (two 5m bars after the decision bar),
  i.e. 295s after the feature is actually available.  Forward returns therefore start
  at bar index i+2 and never overlap the signal window.
* residual_return_1h[j] = beta-hedged (vs BTC/ETH, causal daily-frozen betas) log return
  over (j-12, j].  Hence LEAD(r1h, 2+12k) chains give exactly the forward residual
  return from bar i+2 onward.  fwd_1h = LEAD(r1h,14); fwd_4h = sum of LEAD 14/26/38/50;
  fwd_12h = 12 terms up to LEAD 146.
* BTCUSDT / ETHUSDT are the hedge factors: their "residual" is the raw return.  They are
  written out but EXCLUDED from every cross-sectional / pooled statistic downstream.

Output: one parquet per symbol in <out>/symbol=<S>.parquet, float32, zstd.
"""
import duckdb, glob, os, sys, time, argparse

PANEL = "/home/qbee/futur-data-v2/data_v2/normalized/event_feature_panel/venue=binance"

SQL = """
WITH b AS (
  SELECT timestamp AS ts, close, oi,
         CASE WHEN abs(oi_delta_pct_1h) < 1.0 THEN oi_delta_pct_1h END AS doi_1h_raw,
         COALESCE(aggressive_buy_usd,0) AS ab, COALESCE(aggressive_sell_usd,0) AS asl,
         (aggressive_buy_usd IS NOT NULL) AS flow_ok,
         funding_rate, funding_rate_percentile_90d, basis_z_1d, basis_z_7d,
         residual_return_1h AS r1h, residual_logret_5m AS r5m, residual_std_30d AS sd30,
         cross_section_size
  FROM read_parquet('{glob}')
), w AS (
  SELECT *,
    SUM(ab+asl) OVER (ORDER BY ts ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)   AS dv_1h,
    SUM(ab+asl) OVER (ORDER BY ts ROWS BETWEEN 287 PRECEDING AND CURRENT ROW)  AS dv_24h,
    SUM(ab+asl) OVER (ORDER BY ts ROWS BETWEEN 2015 PRECEDING AND CURRENT ROW) AS dv_7d,
    SUM(ab-asl) OVER (ORDER BY ts ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)   AS sv_1h,
    SUM(ab-asl) OVER (ORDER BY ts ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)    AS sv_15m,
    SUM(ab+asl) OVER (ORDER BY ts ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)    AS dv_15m,
    SUM(CASE WHEN flow_ok THEN 1 ELSE 0 END) OVER (ORDER BY ts ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS nflow_1h,
    r1h + LAG(r1h,12) OVER (ORDER BY ts) + LAG(r1h,24) OVER (ORDER BY ts) + LAG(r1h,36) OVER (ORDER BY ts) AS r4h,
    CASE WHEN LAG(oi,48) OVER (ORDER BY ts) > 0
         THEN LEAST(GREATEST(oi / LAG(oi,48) OVER (ORDER BY ts) - 1, -0.99), 0.99) END AS doi_4h,
    LEAD(r1h,14) OVER (ORDER BY ts) AS f1,  LEAD(r1h,26) OVER (ORDER BY ts) AS f2,
    LEAD(r1h,38) OVER (ORDER BY ts) AS f3,  LEAD(r1h,50) OVER (ORDER BY ts) AS f4,
    LEAD(r1h,62) OVER (ORDER BY ts) AS f5,  LEAD(r1h,74) OVER (ORDER BY ts) AS f6,
    LEAD(r1h,86) OVER (ORDER BY ts) AS f7,  LEAD(r1h,98) OVER (ORDER BY ts) AS f8,
    LEAD(r1h,110) OVER (ORDER BY ts) AS f9, LEAD(r1h,122) OVER (ORDER BY ts) AS f10,
    LEAD(r1h,134) OVER (ORDER BY ts) AS f11,LEAD(r1h,146) OVER (ORDER BY ts) AS f12
  FROM b
)
SELECT ts,
  CAST(close AS REAL) AS close, CAST(sd30 AS REAL) AS sd30, CAST(cross_section_size AS SMALLINT) AS xs_size,
  CAST(r1h AS REAL) AS r1h, CAST(r4h AS REAL) AS r4h, CAST(r5m AS REAL) AS r5m,
  CAST(doi_1h_raw AS REAL) AS doi_1h, CAST(doi_4h AS REAL) AS doi_4h,
  CAST(bz1 AS REAL) AS bz1, CAST(bz7 AS REAL) AS bz7, CAST(fr AS REAL) AS fr, CAST(fpct AS REAL) AS fpct,
  CAST(dv_1h AS REAL) AS dv_1h, CAST(dv_24h AS REAL) AS dv_24h, CAST(dv_7d AS REAL) AS dv_7d,
  CAST(sv_1h/NULLIF(dv_1h,0) AS REAL) AS fi_1h, CAST(sv_15m/NULLIF(dv_15m,0) AS REAL) AS fi_15m,
  CAST(nflow_1h AS TINYINT) AS nflow_1h,
  CAST(f1 AS REAL) AS fwd_1h,
  CAST(f1+f2+f3+f4 AS REAL) AS fwd_4h,
  CAST(f1+f2+f3+f4+f5+f6+f7+f8+f9+f10+f11+f12 AS REAL) AS fwd_12h
FROM (SELECT *, basis_z_1d AS bz1, basis_z_7d AS bz7, funding_rate AS fr,
             funding_rate_percentile_90d AS fpct FROM w)
WHERE date_part('minute', ts) = 0
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshard", type=int, default=1)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    syms = sorted(os.path.basename(p).split("=")[1] for p in glob.glob(f"{PANEL}/symbol=*"))
    syms = [s for i, s in enumerate(syms) if i % a.nshard == a.shard]
    con = duckdb.connect(); con.execute("SET TimeZone='UTC'"); con.execute("PRAGMA threads=2")
    con.execute("PRAGMA memory_limit='3GB'")
    t0 = time.time()
    for k, s in enumerate(syms):
        dst = f"{a.out}/symbol={s}.parquet"
        if os.path.exists(dst):
            continue
        g = f"{PANEL}/symbol={s}/year=*/event_feature_panel_5m.parquet"
        if not glob.glob(g):
            continue
        try:
            con.execute(f"COPY ({SQL.format(glob=g)}) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        except Exception as e:
            print(f"ERR {s}: {e}", flush=True)
        if k % 25 == 0:
            print(f"[shard{a.shard}] {k}/{len(syms)} {s} {time.time()-t0:.0f}s", flush=True)
    print(f"[shard{a.shard}] DONE {len(syms)} in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
