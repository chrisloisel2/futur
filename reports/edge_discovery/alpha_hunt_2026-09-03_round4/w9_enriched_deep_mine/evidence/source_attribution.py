#!/usr/bin/env python3
"""W9 Phase 1 / test A8-bis — ATTRIBUTION DE SOURCE de data/enriched, symbole x annee.

Pour chaque (symbole, annee) : la barre 1h de `enriched` provient-elle du PERP Binance
ou du SPOT Binance ? Reference = panel PIT V2 (/home/qbee/futur-data-v2/data_v2/normalized),
agrege 5m -> 1h. Verdict PERP / SPOT / MIX / NO_V2_REF.
Audite aussi les colonnes taker_* et number_of_trades/trades (tolerance RELATIVE).
Sortie : evidence/SOURCE_ATTRIBUTION.csv
Usage: .venv/bin/python evidence/source_attribution.py
"""
import os, glob, duckdb, pandas as pd, numpy as np

ROOT="/home/qbee/futur"; V2="/home/qbee/futur-data-v2/data_v2/normalized"
HERE=os.path.dirname(os.path.abspath(__file__))
V2NAME={"PEPEUSDT":"1000PEPEUSDT","RNDRUSDT":"RENDERUSDT"}
TOL_BPS=1.0            # ecart de close considere comme "meme source"
con=duckdb.connect(); con.execute("SET memory_limit='1500MB'; SET threads=2; SET TimeZone='UTC';")

def v2_close_hourly(kind, sym):
    v2s=V2NAME.get(sym,sym)
    base=f"{V2}/{kind}_ohlcv/venue=binance/symbol={v2s}"
    if not os.path.isdir(base): return None
    p=f"{base}/year=*/{kind}_5m.parquet"
    if not glob.glob(p): return None
    return con.execute(f"""SELECT date_trunc('hour',timestamp) AS h,
        last(close ORDER BY timestamp) AS c FROM read_parquet('{p}') GROUP BY 1""").fetchdf()

rows=[]
for f in sorted(glob.glob(f"{ROOT}/data/enriched/*_1h_enriched.parquet")):
    sym=os.path.basename(f).split("_")[0]
    en=con.execute(f"""SELECT date_trunc('hour',datetime) AS h, close AS c1, volume AS v1,
        quote_asset_volume AS qav1, taker_buy_base_asset_volume AS tbb1,
        taker_buy_quote_asset_volume AS tbq1, taker_buy_ratio_base AS tbrb1,
        number_of_trades AS ntr1, trades AS trd1 FROM read_parquet('{f}')""").fetchdf()
    en["year"]=pd.to_datetime(en["h"],utc=True).dt.year
    ref={k:v2_close_hourly(k,sym) for k in ("perp","spot")}
    for y,g in en.groupby("year"):
        rec=dict(symbol=sym, year=int(y), n_hours=int(len(g)))
        v1=g["v1"].astype(float).values; qav=g["qav1"].astype(float).values
        tbb=g["tbb1"].astype(float).values; tbq=g["tbq1"].astype(float).values
        def relhalf(a,b):
            m=np.isfinite(a)&np.isfinite(b)&(np.abs(b)>0)
            return float(np.mean(np.abs(a[m]/(0.5*b[m])-1.0)<1e-6)) if m.sum() else np.nan
        rec["tbb_is_half_volume"]=round(relhalf(tbb,v1),4)
        rec["tbq_is_half_qav"]=round(relhalf(tbq,qav),4)
        rec["tbratio_base_eq_0p5"]=round(float(np.mean(np.abs(g["tbrb1"].astype(float).values-0.5)<1e-6)),4)
        rec["ntr_zero_rate"]=round(float((g["ntr1"].fillna(0)==0).mean()),4)
        rec["trades_zero_rate"]=round(float((g["trd1"].fillna(0)==0).mean()),4)
        rec["vol_zero_rate"]=round(float((g["v1"].fillna(0)==0).mean()),4)
        fr={}
        for k in ("perp","spot"):
            if ref[k] is None: fr[k]=np.nan; rec[f"n_match_{k}"]=0; continue
            m=g.merge(ref[k],on="h",how="inner")
            rec[f"n_match_{k}"]=int(len(m))
            if len(m)<100: fr[k]=np.nan; continue
            d=np.abs(m["c1"].astype(float)/m["c"].astype(float)-1.0)*1e4
            fr[k]=float(np.nanmean(d<=TOL_BPS))
        rec["frac_eq_perp"]=None if np.isnan(fr.get("perp",np.nan)) else round(fr["perp"],4)
        rec["frac_eq_spot"]=None if np.isnan(fr.get("spot",np.nan)) else round(fr["spot"],4)
        p=fr.get("perp",np.nan); s=fr.get("spot",np.nan)
        if np.isnan(p) and np.isnan(s): src="NO_V2_REF"
        elif (not np.isnan(p)) and p>=0.98: src="PERP"
        elif (not np.isnan(s)) and s>=0.98: src="SPOT"
        elif max([x for x in (p,s) if not np.isnan(x)] or [0])>=0.05: src="MIX"
        else: src="NEITHER"
        rec["source"]=src
        rows.append(rec)
    print(f"{sym} ok",flush=True)

out=pd.DataFrame(rows)
out.to_csv(HERE+"/SOURCE_ATTRIBUTION.csv",index=False)
print("\n=== repartition source (symbole x annee) ===")
print(out["source"].value_counts().to_string())
print("\n=== symboles NON purement PERP ===")
bad=out[out["source"]!="PERP"]
pd.set_option("display.width",250); pd.set_option("display.max_rows",300)
print(bad[["symbol","year","n_hours","frac_eq_perp","frac_eq_spot","source"]].to_string(index=False))
print("\n=== taker: agregat par symbole (toutes annees) ===")
agg=out.groupby("symbol").agg(tbb_half=("tbb_is_half_volume","mean"), tbq_half=("tbq_is_half_qav","mean"),
    tbr_0p5=("tbratio_base_eq_0p5","mean"), ntr0=("ntr_zero_rate","mean"), trd0=("trades_zero_rate","mean"))
print(agg.round(3).to_string())
