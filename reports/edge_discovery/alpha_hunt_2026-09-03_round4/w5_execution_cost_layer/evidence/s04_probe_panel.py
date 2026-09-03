"""W5/s04 - build the causal (PIT) feature panel on the probe's own 30s mid grid.

All features are trailing/causal by construction: a rolling window ending at t uses only
placements at or before t, and mid_at_place at t is legitimately known at t.
"""
import duckdb, os, numpy as np, pandas as pd
S = os.environ["W5_SCRATCH"]
con = duckdb.connect(); con.execute("PRAGMA threads=8")

# one row per (symbol, placement instant): the BUY and SELL share ts/mid/spread
g = con.execute(f"""
SELECT symbol, ts, avg(mid_at_place) mid, avg(spread_bps) spread_bps,
       max(CASE WHEN side='BUY'  THEN CAST(filled AS INT) END) fill_buy,
       max(CASE WHEN side='SELL' THEN CAST(filled AS INT) END) fill_sell,
       max(CASE WHEN side='BUY'  THEN ttf_s END) ttf_buy,
       max(CASE WHEN side='SELL' THEN ttf_s END) ttf_sell,
       max(CASE WHEN side='BUY'  THEN adv_bps_60s END) adv_buy,
       max(CASE WHEN side='SELL' THEN adv_bps_60s END) adv_sell
FROM read_parquet('{S}/probe.parquet') GROUP BY symbol, ts ORDER BY symbol, ts
""").df()
print("grid rows:", len(g))

out = []
for sym, d in g.groupby("symbol", sort=False):
    d = d.sort_values("ts").reset_index(drop=True)
    lr = np.log(d.mid).diff()
    d["ret_5m"]  = np.log(d.mid).diff(10)            # 10 x 30s
    d["ret_30m"] = np.log(d.mid).diff(60)
    d["rvol_10m"] = lr.rolling(20).std() * np.sqrt(20)   # causal trailing
    d["rvol_60m"] = lr.rolling(120).std() * np.sqrt(120)
    d["spread_ma_1d"] = d.spread_bps.rolling(2880, min_periods=200).median()
    d["spread_rel"] = d.spread_bps / d.spread_ma_1d
    d["shock_5m_bps"] = d.ret_5m.abs() * 1e4
    d["rvol_pct"] = d.rvol_10m.rolling(2880, min_periods=200).rank(pct=True)
    d["hour"] = pd.to_datetime(d.ts).dt.hour
    d["date"] = pd.to_datetime(d.ts).dt.date.astype(str)
    out.append(d)
p = pd.concat(out, ignore_index=True)
p.to_parquet(f"{S}/panel.parquet", index=False)
print(p[["symbol","spread_bps","rvol_10m","shock_5m_bps","fill_buy","fill_sell"]].describe().round(4).to_string())
print("saved", f"{S}/panel.parquet", len(p))
