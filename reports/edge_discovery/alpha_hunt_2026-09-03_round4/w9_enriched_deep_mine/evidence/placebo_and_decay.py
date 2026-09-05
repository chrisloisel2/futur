#!/usr/bin/env python3
"""W9 Phase 2 — PLACEBO + profil de decroissance.

PLACEBO : on permute `mm20` A L'INTERIEUR de chaque barre horaire, entre symboles (meme
instant, meme population, meme declustering, meme demeanage). Le signal perd tout contenu
informatif mais garde exactement la meme structure d'echantillon. Si le placebo rend encore
un edge, c'est qu'un bug de pipeline fabrique l'edge, pas les donnees.

DECROISSANCE : edge en fonction du nombre de barres sautees entre le signal (a t) et
l'entree (a t+k), k = 0,1,2,4,8,12,24. Un edge de « selection sur un extremum bruite »
decroit vers 0 ; un edge reel plafonne.
Sortie : evidence/PLACEBO_AND_DECAY.json
"""
import sys, os, json, numpy as np, pandas as pd
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
DISC = pd.Timestamp("2026-01-01", tz="UTC")
RNG = np.random.default_rng(20260905)

def decl(sym, ts):
    keep = np.zeros(len(sym), bool); last = {}
    for k in range(len(sym)):
        s = sym[k]; t = ts[k]
        if s not in last or (t - last[s]) / np.timedelta64(1, "h") >= 24:
            keep[k] = True; last[s] = t
    return keep

def edge(pop, signal, retcol, side, thresh_lo, thresh_hi):
    m = (signal <= thresh_hi) if side > 0 else (signal >= thresh_lo)
    d = pop[m].copy()
    if len(d) < 2000: return None
    dm = pop.groupby("date")[retcol].mean()
    d["ret"] = d[retcol]*side
    d["ret_dm"] = d["ret"] - d["date"].map(dm).values*side
    d = d.sort_values("dt")
    L1 = d[decl(d["symbol"].values, d["dt"].values)]
    L2 = L1.groupby("date")["ret_dm"].mean(); L2r = L1.groupby("date")["ret"].mean()
    n = len(L2); mu = float(L2.mean()); sd = float(L2.std(ddof=1))
    return dict(n_L1=int(len(L1)), n_L2=n, edge_dm_bps=round(mu,2),
                gross_bps=round(float(L2r.mean()),2), net_bps=round(float(L2r.mean())-14,2),
                t=round(mu/(sd/np.sqrt(n)),2) if sd else None)

df = pd.read_parquet(OUT+"/panel.parquet", columns=["symbol","datetime","close"]).rename(columns={"datetime":"dt"})
df["dt"] = pd.to_datetime(df["dt"], utc=True)
df = df.sort_values(["symbol","dt"]).reset_index(drop=True)
g = df.groupby("symbol", sort=False)["close"]
rmax = g.transform(lambda s: s.rolling(20, min_periods=20).max())
rmin = g.transform(lambda s: s.rolling(20, min_periods=20).min())
df["mm20"] = (df["close"]-rmin)/(rmax-rmin).replace(0, np.nan)
for k in (0,1,2,4,8,12,24):
    df[f"ret_k{k}"] = (g.shift(-(24+k))/g.shift(-k) - 1.0)*1e4
df["date"] = df["dt"].dt.floor("D")
out = {}
for period, msk in (("discovery", df["dt"] < DISC), ("oos", df["dt"] >= DISC)):
    base = df[msk & df["mm20"].notna()]
    # ---- profil de decroissance
    for k in (0,1,2,4,8,12,24):
        rc = f"ret_k{k}"; pop = base[base[rc].notna()]
        for nm, side in (("bottom_long", +1), ("top_short", -1)):
            r = edge(pop, pop["mm20"], rc, side, 0.90, 0.10)
            if r: out[f"decay|{period}|skip{k}|{nm}"] = r; print(f"decay|{period}|skip{k:2d}|{nm:11s}", r, flush=True)
    # ---- placebo : permutation de mm20 entre symboles, a instant EGAL
    pop = base[base["ret_k0"].notna()].copy()
    perm = pop.groupby("dt", sort=False)["mm20"].transform(lambda s: RNG.permutation(s.values))
    for nm, side in (("bottom_long", +1), ("top_short", -1)):
        r = edge(pop, perm, "ret_k0", side, 0.90, 0.10)
        if r: out[f"placebo|{period}|{nm}"] = r; print(f"PLACEBO|{period}|{nm:11s}", r, flush=True)
json.dump(out, open(OUT+"/placebo_and_decay.json","w"), indent=1, default=str)
print("\necrit:", OUT+"/placebo_and_decay.json")
