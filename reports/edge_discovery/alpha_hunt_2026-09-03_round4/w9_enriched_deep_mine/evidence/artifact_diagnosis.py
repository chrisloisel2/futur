#!/usr/bin/env python3
"""W9 — DIAGNOSTIC : d'ou vient l'edge de +80 bps rendu par un signal ALEATOIRE ?

Le placebo (permutation de mm20 entre symboles a instant egal) rend le meme edge que le vrai
signal. L'edge est donc fabrique par l'ESTIMATEUR, pas par le signal. Ce script isole la cause.

  D1 baseline    : moyenne demeanee sur TOUTE la population, sans aucune selection, avec et
                   sans declustering L1.
  D2 heure       : la meme, ventilee par heure UTC d'entree.
  D3 placebo x10 : distribution d'echantillonnage du placebo (10 graines).
  D4 estimateur corrige : demeanage calcule sur la MEME population declusteree (et non sur
                   toutes les barres), puis edge du vrai signal vs edge du placebo.
Sortie : evidence/ARTIFACT_DIAGNOSIS.json
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

df = pd.read_parquet(OUT+"/panel.parquet", columns=["symbol","datetime","close"]).rename(columns={"datetime":"dt"})
df["dt"] = pd.to_datetime(df["dt"], utc=True)
df = df.sort_values(["symbol","dt"]).reset_index(drop=True)
g = df.groupby("symbol", sort=False)["close"]
rmax = g.transform(lambda s: s.rolling(20, min_periods=20).max())
rmin = g.transform(lambda s: s.rolling(20, min_periods=20).min())
df["mm20"] = (df["close"]-rmin)/(rmax-rmin).replace(0, np.nan)
df["fwd"] = (g.shift(-24)/df["close"]-1.0)*1e4
df["date"] = df["dt"].dt.floor("D"); df["hour"] = df["dt"].dt.hour
pop = df[(df["dt"] < DISC) & df["fwd"].notna() & df["mm20"].notna()].copy()
dm_all = pop.groupby("date")["fwd"].mean()
pop["ret_dm_allbars"] = pop["fwd"] - pop["date"].map(dm_all).values
out = {}

# D1 : baseline SANS aucune selection
print("=== D1 baseline, aucune selection ===")
print("  moyenne demeanee sur TOUTES les barres        :", round(float(pop["ret_dm_allbars"].mean()),3), "bps  (doit valoir 0)")
sub = pop.sort_values("dt")
L1 = sub[decl(sub["symbol"].values, sub["dt"].values)]
b_L1 = float(L1["ret_dm_allbars"].mean())
b_L2 = float(L1.groupby("date")["ret_dm_allbars"].mean().mean())
print("  apres declustering L1 (1 barre/symbole/24h)   :", round(b_L1,2), "bps")
print("  puis moyenne journaliere L2                   :", round(b_L2,2), "bps   <-- BIAIS DE BASE")
out["D1"] = dict(all_bars=round(float(pop["ret_dm_allbars"].mean()),3), after_L1=round(b_L1,2), after_L2=round(b_L2,2),
                 n_L1=int(len(L1)), mean_hour_L1=round(float(L1["hour"].mean()),2), mean_hour_pop=round(float(pop["hour"].mean()),2))
print("  heure UTC moyenne : population", round(float(pop['hour'].mean()),2), "| echantillon L1", round(float(L1['hour'].mean()),2))

# D2 : ventilation par heure d'entree
h = pop.groupby("hour")["ret_dm_allbars"].agg(["mean","count"])
out["D2_by_hour"] = {int(k): round(float(v),2) for k,v in h["mean"].items()}
print("\n=== D2 rendement 24h demeane par heure UTC d'entree (population entiere) ===")
print("  ", {int(k): round(float(v),1) for k,v in h["mean"].items()})

# D3 : distribution du placebo
print("\n=== D3 placebo, 10 graines ===")
vals = []
for seed in range(10):
    rng = np.random.default_rng(1000+seed)
    perm = pop.groupby("dt", sort=False)["mm20"].transform(lambda s: rng.permutation(s.values))
    d = pop[perm <= 0.10].sort_values("dt")
    l1 = d[decl(d["symbol"].values, d["dt"].values)]
    v = float(l1.groupby("date")["ret_dm_allbars"].mean().mean()); vals.append(round(v,2))
print("  ", vals, "-> moyenne", round(float(np.mean(vals)),2), "ecart-type", round(float(np.std(vals)),2))
out["D3_placebo_10_seeds"] = vals

# D4 : estimateur corrige — demeanage sur la population DECLUSTEREE
print("\n=== D4 estimateur corrige (demeanage sur la population declusteree) ===")
allL1 = L1.copy()
dm_L1 = allL1.groupby("date")["fwd"].mean()
res4 = {}
for nm, m in (("signal_bottom_decile", pop["mm20"] <= 0.10), ("signal_top_decile", pop["mm20"] >= 0.90)):
    d = pop[m].sort_values("dt"); l1 = d[decl(d["symbol"].values, d["dt"].values)].copy()
    l1["dmc"] = l1["fwd"] - l1["date"].map(dm_L1).values
    L2 = l1.groupby("date")["dmc"].mean(); n = len(L2)
    res4[nm] = dict(n_L2=n, edge_bps=round(float(L2.mean()),2),
                    t=round(float(L2.mean()/(L2.std(ddof=1)/np.sqrt(n))),2))
    print("  ", nm, res4[nm])
pl = []
for seed in range(10):
    rng = np.random.default_rng(2000+seed)
    perm = pop.groupby("dt", sort=False)["mm20"].transform(lambda s: rng.permutation(s.values))
    d = pop[perm <= 0.10].sort_values("dt"); l1 = d[decl(d["symbol"].values, d["dt"].values)].copy()
    l1["dmc"] = l1["fwd"] - l1["date"].map(dm_L1).values
    pl.append(round(float(l1.groupby("date")["dmc"].mean().mean()),2))
res4["placebo_10_seeds"] = pl
print("   placebo corrige :", pl, "-> moyenne", round(float(np.mean(pl)),2))
out["D4"] = res4
json.dump(out, open(OUT+"/artifact_diagnosis.json","w"), indent=1, default=str)
print("\necrit:", OUT+"/artifact_diagnosis.json")
