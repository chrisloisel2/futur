#!/usr/bin/env python3
"""W9 — LA CAUSE : le controle par JOUR CALENDAIRE est trop grossier.

Le placebo qui fait feu permute mm20 ENTRE SYMBOLES a instant egal : il conserve donc
exactement la distribution TEMPORELLE des evenements et ne detruit que l'information
cross-sectionnelle. Il rend +80 bps => l'edge vient du QUAND, pas du QUI.
Un tirage 10% uniforme sur toutes les barres (qui casse aussi le quand) rend -1,4 bps.

On compare donc trois niveaux de controle, a mecanisme identique :
  C1 aucun            : rendement brut
  C2 jour calendaire  : rendement - moyenne du jour (le controle du briefing §1.3)
  C3 barre horaire    : rendement - moyenne de TOUS les symboles a la MEME heure  <-- correct
Sortie : evidence/CONTROL_LEVEL.json
"""
import os, json, numpy as np, pandas as pd
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
DISC = pd.Timestamp("2026-01-01", tz="UTC")

def decl(sym, ts):
    keep = np.zeros(len(sym), bool); last = {}
    for k in range(len(sym)):
        s = sym[k]; t = ts[k]
        if s not in last or (t-last[s])/np.timedelta64(1,"h") >= 24:
            keep[k] = True; last[s] = t
    return keep

def run(panel, name):
    df = panel.sort_values(["symbol","dt"]).reset_index(drop=True)
    g = df.groupby("symbol", sort=False)["close"]
    rmax = g.transform(lambda s: s.rolling(20, min_periods=20).max())
    rmin = g.transform(lambda s: s.rolling(20, min_periods=20).min())
    df["mm20"] = (df["close"]-rmin)/(rmax-rmin).replace(0, np.nan)
    df["fwd"] = (g.shift(-24)/df["close"]-1.0)*1e4
    df["date"] = df["dt"].dt.floor("D")
    res = {}
    for period, msk in (("discovery", df["dt"] < DISC), ("oos", df["dt"] >= DISC)):
        pop = df[msk & df["fwd"].notna() & df["mm20"].notna()].copy()
        if len(pop) < 50000: continue
        pop["c1"] = pop["fwd"]
        pop["c2"] = pop["fwd"] - pop["date"].map(pop.groupby("date")["fwd"].mean()).values
        pop["c3"] = pop["fwd"] - pop["dt"].map(pop.groupby("dt")["fwd"].mean()).values
        rng = np.random.default_rng(4242)
        pop["perm"] = pop.groupby("dt", sort=False)["mm20"].transform(lambda s: rng.permutation(s.values))
        for arm, sigcol in (("signal", "mm20"), ("placebo", "perm")):
            for nm, m, side in (("bottom_decile_long", pop[sigcol] <= 0.10, +1),
                                ("top_decile_long",    pop[sigcol] >= 0.90, +1)):
                d = pop[m].sort_values("dt")
                L1 = d[decl(d["symbol"].values, d["dt"].values)]
                r = {}
                for lvl in ("c1","c2","c3"):
                    L2 = L1.groupby("date")[lvl].mean(); n = len(L2)
                    mu = float(L2.mean()); sd = float(L2.std(ddof=1))
                    r[lvl] = dict(edge_bps=round(mu,2), t=round(mu/(sd/np.sqrt(n)),2) if sd else None)
                r["n_L1"] = int(len(L1)); r["n_L2"] = int(L1.groupby("date").ngroups)
                res[f"{name}|{period}|{arm}|{nm}"] = r
                print(f"{name}|{period}|{arm:7s}|{nm:19s} n_L2={r['n_L2']:>5} "
                      f"brut {r['c1']['edge_bps']:>8} (t={r['c1']['t']:>6}) | "
                      f"ctrl_JOUR {r['c2']['edge_bps']:>8} (t={r['c2']['t']:>6}) | "
                      f"ctrl_HEURE {r['c3']['edge_bps']:>8} (t={r['c3']['t']:>6})", flush=True)
    return res

out = {}
e = pd.read_parquet(OUT+"/panel.parquet", columns=["symbol","datetime","close"]).rename(columns={"datetime":"dt"})
e["dt"] = pd.to_datetime(e["dt"], utc=True); out.update(run(e, "ENRICHED")); del e
p = pd.read_parquet(OUT+"/pit_1h.parquet", columns=["symbol","dt","close"])
p["dt"] = pd.to_datetime(p["dt"], utc=True); out.update(run(p, "PIT")); del p
json.dump(out, open(OUT+"/control_level.json","w"), indent=1, default=str)
print("\necrit:", OUT+"/control_level.json")
