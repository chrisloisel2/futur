#!/usr/bin/env python3
"""W9 Phase 2 — TEST DECISIF : l'effet « position du close dans son range 20 barres » est-il
un vrai edge de reversion 24 h, ou un artefact de REBOND BID-ASK ?

`mm20 = 0` signifie exactement « close[t] est le plus bas des 20 derniers closes ». Selectionner
un minimum de prix de TRANSACTION selectionne preferentiellement des prints cote BID ; le
rendement mesure ensuite jusqu'a t+24 recupere mecaniquement la moitie du spread, sans qu'un
euro soit gagnable (on ne peut pas acheter au bid qu'on vient de constater).

Test « skip-one-bar » (standard) :
  - variante A (celle des §5) : signal a t, entree close[t],   sortie close[t+24]
  - variante B (anti-rebond)  : signal a t, entree close[t+1], sortie close[t+25]
La variante B garde toute l'information du signal mais paie un prix de transaction DIFFERENT
de celui qui a defini l'extreme. Si l'edge s'effondre en B, c'est un artefact de rebond.
Ajoute une decomposition par tercile de liquidite (volume $ median 30 j, causal).
Sortie : evidence/BOUNCE_TEST.json
"""
import sys, os, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate2 import block_boot
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
DISCOVERY_END = pd.Timestamp("2026-01-01", tz="UTC"); OOS_END = pd.Timestamp("2026-06-29", tz="UTC")
COST = 14.0

def decluster_np(sym, ts):
    keep = np.zeros(len(sym), bool); last = {}
    for k in range(len(sym)):
        s = sym[k]; t = ts[k]
        if s not in last or (t - last[s]) / np.timedelta64(1, "h") >= 24:
            keep[k] = True; last[s] = t
    return keep

def stat(sub, daymean, retcol, side, label):
    d = sub.copy()
    d["ret"] = d[retcol] * side
    d["ret_dm"] = d["ret"] - d["date"].map(daymean).values * side
    d = d.sort_values("dt")
    L1 = d[decluster_np(d["symbol"].values, d["dt"].values)]
    L2 = L1.groupby("date")["ret_dm"].mean(); L2r = L1.groupby("date")["ret"].mean()
    n2 = len(L2)
    if n2 < 30: return dict(label=label, n_L2=n2, verdict="DATA_LIMITED")
    mu = float(L2.mean()); sd = float(L2.std(ddof=1)); t = mu/(sd/np.sqrt(n2))
    wk = L1.groupby("date")["week"].first().reindex(L2.index).values
    ci = block_boot(L2.values, wk)
    return dict(label=label, n_raw=int(len(d)), n_L1=int(len(L1)), n_L2=n2,
                edge_dm_bps=round(mu,2), gross_bps=round(float(L2r.mean()),2),
                net_bps=round(float(L2r.mean())-COST,2), net_bps_stress28=round(float(L2r.mean())-28,2),
                t=round(float(t),2), ci95=[round(float(ci[0]),2), round(float(ci[1]),2)])

def run(panel, name, symcol="symbol", tcol="dt", ccol="close", vcol=None):
    df = panel.sort_values([symcol, tcol]).reset_index(drop=True)
    g = df.groupby(symcol, sort=False)[ccol]
    rmax = g.transform(lambda s: s.rolling(20, min_periods=20).max())
    rmin = g.transform(lambda s: s.rolling(20, min_periods=20).min())
    df["mm20"] = (df[ccol] - rmin) / (rmax - rmin).replace(0, np.nan)
    c = df[ccol]
    df["retA"] = (g.shift(-24)/c - 1.0)*1e4                      # entree close[t]
    df["retB"] = (g.shift(-25)/g.shift(-1) - 1.0)*1e4            # entree close[t+1]
    df["retC"] = (g.shift(-26)/g.shift(-2) - 1.0)*1e4            # entree close[t+2]
    df["date"] = df[tcol].dt.floor("D"); df["year"] = df[tcol].dt.year
    df["week"] = df[tcol].dt.tz_localize(None).dt.to_period("W").astype(str)
    if vcol:
        dv = df[vcol]*df[ccol]
        df["liq"] = df.groupby(symcol, sort=False).apply(
            lambda x: (x[vcol]*x[ccol]).rolling(720, min_periods=200).median()).reset_index(level=0, drop=True)
    res = {}
    for period, lo, hi in (("discovery", df[tcol].min(), DISCOVERY_END), ("oos", DISCOVERY_END, OOS_END)):
        base = df[(df[tcol] >= lo) & (df[tcol] < hi) & df["mm20"].notna()]
        for var, rc in (("A_entree_close_t", "retA"), ("B_entree_close_t+1", "retB"), ("C_entree_close_t+2", "retC")):
            pop = base[base[rc].notna()]
            if not len(pop): continue
            dm = pop.groupby("date")[rc].mean()
            for nm, m, side in (("bottom_decile_long", pop["mm20"] <= 0.10, +1),
                                ("top_decile_short",   pop["mm20"] >= 0.90, -1)):
                r = stat(pop[m], dm, rc, side, f"{name}|{period}|{var}|{nm}")
                res[r["label"]] = r
                print("  ", r["label"], {k: r.get(k) for k in ("n_L2","edge_dm_bps","gross_bps","net_bps","net_bps_stress28","t","ci95")}, flush=True)
        # terciles de liquidite, variante A vs B, decile bas long
        if vcol and "liq" in base.columns:
            b = base[base["liq"].notna() & base["retA"].notna() & base["retB"].notna()]
            q = b["liq"].quantile([1/3, 2/3]).values
            for ti, (a_, b_) in enumerate(((-np.inf, q[0]), (q[0], q[1]), (q[1], np.inf))):
                sub = b[(b["liq"] > a_) & (b["liq"] <= b_)]
                for var, rc in (("A", "retA"), ("B", "retB")):
                    dm = sub.groupby("date")[rc].mean()
                    r = stat(sub[sub["mm20"] <= 0.10], dm, rc, +1, f"{name}|{period}|liqT{ti}|{var}|bottom_long")
                    res[r["label"]] = r
                    print("  ", r["label"], {k: r.get(k) for k in ("n_L2","edge_dm_bps","gross_bps","t")}, flush=True)
    return res

out = {}
print("=== PANEL PIT (futur-data-v2, 312 symboles, delistes inclus) ===", flush=True)
p = pd.read_parquet(OUT+"/pit_1h.parquet", columns=["symbol","dt","close","volume"])
p["dt"] = pd.to_datetime(p["dt"], utc=True)
out.update(run(p, "PIT", vcol="volume"))
del p
print("\n=== PANEL ENRICHED (frozen-50) ===", flush=True)
e = pd.read_parquet(OUT+"/panel.parquet", columns=["symbol","datetime","close","volume"]).rename(columns={"datetime":"dt"})
e["dt"] = pd.to_datetime(e["dt"], utc=True)
out.update(run(e, "ENRICHED", vcol="volume"))
json.dump(out, open(OUT+"/bounce_test.json","w"), indent=1, default=str)
print("\necrit:", OUT+"/bounce_test.json")
