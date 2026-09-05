#!/usr/bin/env python3
"""W9 Phase 1 / test A6 — detection des COUTURES (seams) dans data/enriched.
Deux coutures possibles : (a) changement de `feature_count` = changement de generation
de features ; (b) bascule de source perp->spot (detectee par un saut du niveau de volume).
Sortie : evidence/SEAMS.csv
Usage: .venv/bin/python evidence/seam_scan.py
"""
import os, glob, duckdb, pandas as pd, numpy as np
HERE=os.path.dirname(os.path.abspath(__file__))
con=duckdb.connect(); con.execute("SET memory_limit='1500MB'; SET threads=2; SET TimeZone='UTC';")
rows=[]
for f in sorted(glob.glob("/home/qbee/futur/data/enriched/*_1h_enriched.parquet")):
    sym=os.path.basename(f).split("_")[0]
    d=con.execute(f"""SELECT datetime, feature_count, volume, obv FROM read_parquet('{f}') ORDER BY datetime""").fetchdf()
    fc=d.feature_count.values
    chg=np.where(fc[1:]!=fc[:-1])[0]+1
    for i in chg:
        rows.append(dict(symbol=sym, seam_type="feature_count", t=str(d.datetime.iloc[i])[:19],
                         detail=f"{fc[i-1]} -> {fc[i]}", ratio=None))
    # saut de niveau de volume : mediane 168h avant vs apres
    v=d.volume.values.astype(float)
    n=len(v)
    if n>800:
        w=168
        med_pre=pd.Series(v).rolling(w).median().values
        med_post=pd.Series(v[::-1]).rolling(w).median().values[::-1]
        with np.errstate(all="ignore"):
            r=med_post/np.where(med_pre>0,med_pre,np.nan)
        r[:w]=np.nan; r[-w:]=np.nan
        # ignore les 2 premieres semaines (rampe de listing)
        r[:2*w]=np.nan
        if np.isfinite(r).any():
            j=int(np.nanargmax(np.abs(np.log(r))))
            if abs(np.log(r[j]))>np.log(2.0):
                rows.append(dict(symbol=sym, seam_type="volume_level_jump", t=str(d.datetime.iloc[j])[:19],
                                 detail=f"mediane volume 168h x{r[j]:.2f}", ratio=round(float(r[j]),3)))
    # reset obv (feature cumulative)
    o=d.obv.values.astype(float)
    do=np.abs(np.diff(o)); sd=np.nanstd(do[np.isfinite(do)])
    if np.isfinite(sd) and sd>0:
        k=int(np.nanargmax(do))
        if do[k]>50*sd:
            rows.append(dict(symbol=sym, seam_type="obv_reset", t=str(d.datetime.iloc[k+1])[:19],
                             detail=f"{o[k]:.3g} -> {o[k+1]:.3g} ({do[k]/sd:.0f} sigma)", ratio=None))
out=pd.DataFrame(rows).sort_values(["seam_type","t"])
out.to_csv(HERE+"/SEAMS.csv",index=False)
pd.set_option("display.width",220); pd.set_option("display.max_rows",300)
print(out.to_string(index=False))
