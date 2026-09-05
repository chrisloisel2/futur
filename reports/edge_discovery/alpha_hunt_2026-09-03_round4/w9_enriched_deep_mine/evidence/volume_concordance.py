#!/usr/bin/env python3
"""W9 Phase 1 / test A8-ter — concordance du VOLUME enrichi vs panel V2, par symbole x annee.
Le close de `enriched` colle au perp V2 ; la question est de savoir si le VOLUME colle aussi.
Sortie : evidence/VOLUME_CONCORDANCE.csv
"""
import os, glob, duckdb, pandas as pd, numpy as np
V2="/home/qbee/futur-data-v2/data_v2/normalized"; HERE=os.path.dirname(os.path.abspath(__file__))
V2NAME={"PEPEUSDT":"1000PEPEUSDT","RNDRUSDT":"RENDERUSDT"}
con=duckdb.connect(); con.execute("SET memory_limit='1500MB'; SET threads=2; SET TimeZone='UTC';")
rows=[]
for f in sorted(glob.glob("/home/qbee/futur/data/enriched/*_1h_enriched.parquet")):
    sym=os.path.basename(f).split("_")[0]; v2s=V2NAME.get(sym,sym)
    en=con.execute(f"SELECT date_trunc('hour',datetime) h, close c1, volume v1 FROM read_parquet('{f}')").fetchdf()
    for kind in ("perp","spot"):
        p=f"{V2}/{kind}_ohlcv/venue=binance/symbol={v2s}/year=*/{kind}_5m.parquet"
        if not glob.glob(p): continue
        r=con.execute(f"""SELECT date_trunc('hour',timestamp) h, last(close ORDER BY timestamp) c2,
             sum(volume) v2v FROM read_parquet('{p}') GROUP BY 1""").fetchdf()
        m=en.merge(r,on="h",how="inner")
        if len(m)<300: continue
        m["year"]=pd.to_datetime(m.h,utc=True).dt.year
        for y,g in m.groupby("year"):
            if len(g)<200: continue
            dc=np.abs(g.c1.astype(float)/g.c2.astype(float)-1)*1e4
            a=g.v1.astype(float).values; b=g.v2v.astype(float).values
            ok=np.isfinite(a)&np.isfinite(b)&(b>0)
            rel=np.abs(a[ok]/b[ok]-1)
            rows.append(dict(symbol=sym, ref=kind, year=int(y), n=int(len(g)),
                frac_close_eq=round(float(np.mean(dc<=1.0)),4),
                frac_vol_eq_1pct=round(float(np.mean(rel<0.01)),4),
                vol_median_ratio=round(float(np.nanmedian(a[ok]/b[ok])),4)))
    print(sym,flush=True)
out=pd.DataFrame(rows); out.to_csv(HERE+"/VOLUME_CONCORDANCE.csv",index=False)
# la reference "vraie" est celle qui matche le close
best=out[out.frac_close_eq>=0.98]
bad=best[best.frac_vol_eq_1pct<0.95]
pd.set_option("display.width",220); pd.set_option("display.max_rows",400)
print("\n=== (symbole, annee) ou le CLOSE matche V2 mais le VOLUME NON (>1% d'ecart sur >5% des heures) ===")
print(bad.to_string(index=False))
print("\nn lignes close-matchees:",len(best)," dont volume discordant:",len(bad))
