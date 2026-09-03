#!/usr/bin/env python3
"""W9 Phase 1 — audit colonne par colonne de data/enriched/*_1h_enriched.parquet.
Streaming par blocs de colonnes (jamais le fichier entier en RAM). Ecrit un JSON de stats.
Usage: .venv/bin/python evidence/audit_columns.py <SYM> [SYM...]"""
import sys, os, json, hashlib
import numpy as np, pandas as pd, pyarrow.parquet as pq

ROOT = "/home/qbee/futur"
OUT  = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
WARMUP = 200
CHUNK  = 400

def audit(sym):
    f = f"{ROOT}/data/enriched/{sym}_1h_enriched.parquet"
    pf = pq.ParquetFile(f); names = pf.schema_arrow.names
    dt = pq.read_table(f, columns=["datetime"]).column(0).to_pandas()
    dt = pd.to_datetime(dt, utc=True)
    n = len(dt)
    year = dt.dt.year.values
    # segment de generation : coupure au changement de feature_count
    try:
        fc = pq.read_table(f, columns=["feature_count"]).column(0).to_pandas().values
    except Exception:
        fc = np.zeros(n)
    fc_vals = pd.unique(fc)
    warm = np.zeros(n, bool); warm[:WARMUP] = True
    keep = ~warm
    stats = {}
    hashes = {}
    numeric = [c for c in names if c not in ("datetime",)]
    for i in range(0, len(numeric), CHUNK):
        block = numeric[i:i+CHUNK]
        tb = pq.read_table(f, columns=block)
        for c in block:
            col = tb.column(c)
            try:
                v = col.to_pandas()
            except Exception:
                continue
            if str(col.type).startswith("list") or str(col.type).startswith("large_list"):
                lens = [len(z) if z is not None else -1 for z in v.head(2000)]
                stats[c] = dict(dtype="list:"+str(col.type), null_rate=float(v.isna().mean()),
                                list_len_min=int(min(lens)), list_len_max=int(max(lens)))
                continue
            if v.dtype == object or str(v.dtype).startswith("string"):
                nu = v.nunique(dropna=True)
                stats[c] = dict(dtype="string", null_rate=float(v.isna().mean()),
                                n_unique=int(nu), sample=str(v.dropna().iloc[0]) if nu else None)
                continue
            a = v.to_numpy(dtype="float64", na_value=np.nan)
            fin = np.isfinite(a)
            aw = a[keep]; finw = np.isfinite(aw)
            d = dict(dtype=str(v.dtype))
            d["null_rate"] = float(1.0 - fin.mean())
            d["null_rate_postwarmup"] = float(1.0 - finw.mean()) if len(aw) else 1.0
            if finw.sum() == 0:
                d["degenerate"] = "all_null"; stats[c] = d; continue
            x = aw[finw]
            d["mean"] = float(np.mean(x)); d["std"] = float(np.std(x))
            d["min"] = float(np.min(x)); d["max"] = float(np.max(x))
            d["zero_rate"] = float(np.mean(x == 0.0))
            sub = x if len(x) <= 20000 else x[np.linspace(0, len(x)-1, 20000).astype(int)]
            d["n_unique_sample"] = int(len(np.unique(sub)))
            # rupture par segment de generation
            seg = {}
            for fv in fc_vals:
                m = (fc == fv) & fin & keep
                if m.sum() > 50:
                    xm = a[m]
                    seg[str(fv)] = dict(n=int(m.sum()), mean=float(np.mean(xm)),
                                        std=float(np.std(xm)),
                                        null_rate=float(1.0 - np.isfinite(a[(fc == fv) & keep]).mean()))
            d["by_generation"] = seg
            # rupture par annee : moyenne/std/nullrate
            yr = {}
            for y in np.unique(year):
                m = (year == y) & keep
                if m.sum() > 100:
                    am = a[m]; fm = np.isfinite(am)
                    yr[int(y)] = dict(n=int(m.sum()), null_rate=float(1.0-fm.mean()),
                                      mean=float(np.mean(am[fm])) if fm.sum() else None,
                                      std=float(np.std(am[fm])) if fm.sum() else None)
            d["by_year"] = yr
            hashes[c] = hashlib.md5(np.nan_to_num(a, nan=-1.2345e300).tobytes()).hexdigest()
            stats[c] = d
        del tb
    # A4 : colonnes bit-a-bit identiques
    dup = {}
    inv = {}
    for c, h in hashes.items(): inv.setdefault(h, []).append(c)
    for h, cs in inv.items():
        if len(cs) > 1: dup[cs[0]] = cs[1:]
    return dict(symbol=sym, n_rows=int(n), t0=str(dt.iloc[0]), t1=str(dt.iloc[-1]),
                n_cols=len(names), feature_counts=[str(x) for x in fc_vals],
                stats=stats, duplicate_groups=dup, col_hashes=hashes)

if __name__ == "__main__":
    for sym in sys.argv[1:]:
        r = audit(sym)
        p = f"{OUT}/audit_{sym}.json"
        json.dump(r, open(p, "w"))
        print(f"{sym}: {r['n_cols']} cols, {r['n_rows']} rows, dup_groups={len(r['duplicate_groups'])} -> {p}", flush=True)
