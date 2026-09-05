#!/usr/bin/env python3
"""W9 Phase 2 — TEST DECISIF de survivorship.

L'univers de `data/enriched` = les 50 symboles de la liste « frozen-50 » figee en 2026,
appliquee RETROACTIVEMENT a 2017-2025. Acheter les creux d'un panier dont on sait qu'il a
survecu jusqu'en 2026 est structurellement biaise. Ce script rejoue EXACTEMENT le meme
mecanisme (decile de la position du close dans son range 20 barres, horizon 24 h) sur
l'univers PIT de futur-data-v2 (perp_ohlcv, tous les symboles listes, delistes inclus),
construit ici a partir des barres 5 m -> 1 h.

Etape 1 : construit scratch/pit_1h.parquet (close 1h, tous symboles v2 perp).
Etape 2 : rejoue le balayage par decile + le screen, avec le MEME declustering L1/L2/L3.
Usage: .venv/bin/python evidence/pit_replication.py [--build]
"""
import sys, os, json, glob, duckdb, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate2 import block_boot
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
V2  = "/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance"
PIT = OUT + "/pit_1h.parquet"
DISCOVERY_END = pd.Timestamp("2026-01-01", tz="UTC")
OOS_END       = pd.Timestamp("2026-06-29", tz="UTC")

def build():
    con = duckdb.connect(); con.execute("SET memory_limit='2500MB'; SET threads=2; SET TimeZone='UTC';")
    con.execute(f"""
      COPY (
        SELECT symbol, date_trunc('hour', timestamp) AS dt,
               last(close ORDER BY timestamp)  AS close,
               max(high) AS high, min(low) AS low, sum(volume) AS volume
        FROM read_parquet('{V2}/symbol=*/year=*/perp_5m.parquet', hive_partitioning=1)
        GROUP BY 1,2
      ) TO '{PIT}' (FORMAT PARQUET, COMPRESSION ZSTD);
    """)
    print("pit_1h.parquet ecrit,", os.path.getsize(PIT)//1024//1024, "Mo")

def decluster_np(sym, ts, idx):
    """L1 vectorise : au plus un evenement par symbole par fenetre glissante de 24 h."""
    keep = np.zeros(len(idx), bool); last = {}
    for k in range(len(idx)):
        s = sym[k]; t = ts[k]
        if s not in last or (t - last[s]) / np.timedelta64(1, "h") >= 24:
            keep[k] = True; last[s] = t
    return keep

def stats(sub, pop_daymean, H, side=1, label=""):
    d = sub.copy()
    d["ret"] = d[f"fwd{H}"] * side
    d["ret_dm"] = d["ret"] - d["date"].map(pop_daymean).values * side
    d = d.sort_values(["dt"])
    k = decluster_np(d["symbol"].values, d["dt"].values, d.index.values)
    L1 = d[k]
    L2  = L1.groupby("date")["ret_dm"].mean()
    L2r = L1.groupby("date")["ret"].mean()
    n2 = len(L2)
    if n2 < 30: return dict(label=label, n_raw=int(len(d)), n_L2=n2, verdict="DATA_LIMITED")
    mu = float(L2.mean()); sd = float(L2.std(ddof=1))
    t = mu/(sd/np.sqrt(n2)) if sd > 0 else 0.0
    wk = L1.groupby("date")["week"].first().reindex(L2.index).values
    ci = block_boot(L2.values, wk)
    yby = {int(y): dict(n_L2=int(len(g.groupby('date'))), edge_bps=round(float(g.groupby('date')['ret_dm'].mean().mean()),2))
           for y, g in L1.groupby("year") if g.groupby('date').ngroups >= 5}
    return dict(label=label, n_raw=int(len(d)), n_L1=int(len(L1)), n_L2=n2, n_L3=int(L1.week.nunique()),
                edge_dm_bps=round(mu,2), gross_bps=round(float(L2r.mean()),2),
                net_bps=round(float(L2r.mean())-14,2), net_bps_stress28=round(float(L2r.mean())-28,2),
                t=round(float(t),2), ci95=[round(float(ci[0]),2), round(float(ci[1]),2)],
                year_by_year=yby, n_symbols=int(L1.symbol.nunique()))

def run():
    df = pd.read_parquet(PIT, columns=["symbol","dt","close"])
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    df = df.sort_values(["symbol","dt"]).reset_index(drop=True)
    g = df.groupby("symbol", sort=False)["close"]
    # minmax_norm_close_20 CAUSAL, meme definition que le generateur enrichi (rolling trailing)
    rmax = g.transform(lambda s: s.rolling(20, min_periods=20).max())
    rmin = g.transform(lambda s: s.rolling(20, min_periods=20).min())
    df["mm20"] = (df["close"] - rmin) / (rmax - rmin).replace(0, np.nan)
    for H in (24,):
        df[f"fwd{H}"] = (g.shift(-H)/df["close"] - 1.0)*1e4
    df["date"] = df["dt"].dt.floor("D"); df["year"] = df["dt"].dt.year
    df["week"] = df["dt"].dt.tz_localize(None).dt.to_period("W").astype(str)
    print("panel PIT:", len(df), "lignes,", df.symbol.nunique(), "symboles,", df.dt.min(), "->", df.dt.max(), flush=True)
    res = {}
    for period, lo, hi in (("discovery", df.dt.min(), DISCOVERY_END), ("oos", DISCOVERY_END, OOS_END)):
        ok = df["fwd24"].notna() & df["mm20"].notna() & (df.dt >= lo) & (df.dt < hi)
        pop = df[ok]
        daymean = pop.groupby("date")["fwd24"].mean()
        print(f"\n=== PIT {period} : n={len(pop)} symboles={pop.symbol.nunique()} ===", flush=True)
        tab = []
        for dcl in range(10):
            sub = pop[np.clip((pop["mm20"]*10).astype(int), 0, 9) == dcl]
            r = stats(sub, daymean, 24, +1, f"decile_{dcl}")
            tab.append(r); print("  ", {k: r[k] for k in ("label","n_raw","n_L2","edge_dm_bps","gross_bps","t") if k in r}, flush=True)
        res[period+"_deciles"] = tab
        for nm, m, side in (("SCREEN_bottom_decile_long", pop["mm20"] <= 0.10, +1),
                            ("SCREEN_top_decile_short",   pop["mm20"] >= 0.90, -1)):
            r = stats(pop[m], daymean, 24, side, nm); res[f"{period}_{nm}"] = r
            print("  ", nm, {k: r[k] for k in ("n_raw","n_L1","n_L2","n_symbols","edge_dm_bps","gross_bps","net_bps","net_bps_stress28","t","ci95") if k in r}, flush=True)
            print("     annees:", r.get("year_by_year"), flush=True)
    json.dump(res, open(OUT+"/pit_replication.json","w"), indent=1, default=str)
    print("\necrit:", OUT+"/pit_replication.json")

if __name__ == "__main__":
    if "--build" in sys.argv or not os.path.exists(PIT): build()
    run()
