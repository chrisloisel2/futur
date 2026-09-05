#!/usr/bin/env python3
"""W9 — ISOLATION de l'artefact : quelle etape fabrique les +80 bps ?

On tire un sous-echantillon ALEATOIRE de taux p (aucun contenu informatif) et on mesure la
moyenne du rendement 24 h demeane, en activant/desactivant chaque etape :
  - sans declustering  vs  avec declustering L1 (>= 24 h entre deux evenements d'un symbole)
  - moyenne simple sur les evenements  vs  moyenne journaliere L2 puis moyenne des jours
Si l'artefact apparait a une etape precise, il devient nommable et reparable.
Sortie : evidence/ARTIFACT_ISOLATION.json
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
df["fwd"] = (g.shift(-24)/df["close"]-1.0)*1e4
df["date"] = df["dt"].dt.floor("D")
pop = df[(df["dt"] < DISC) & df["fwd"].notna()].copy().sort_values("dt").reset_index(drop=True)
dm = pop.groupby("date")["fwd"].mean()
pop["dmv"] = pop["date"].map(dm).values
pop["rdm"] = pop["fwd"] - pop["dmv"]
print("population:", len(pop), "barres,", pop.symbol.nunique(), "symboles")
print("moyenne demeanee sur toute la population :", round(float(pop["rdm"].mean()),3), "bps\n")
out = {}
rng = np.random.default_rng(7)
for p in (1.0, 0.5, 0.10, 0.03):
    sel = pop if p >= 1.0 else pop[rng.random(len(pop)) < p]
    sel = sel.sort_values("dt")
    row = {}
    row["n"] = int(len(sel))
    row["mean_events_nodecl"] = round(float(sel["rdm"].mean()), 2)
    row["mean_daily_nodecl"]  = round(float(sel.groupby("date")["rdm"].mean().mean()), 2)
    L1 = sel[decl(sel["symbol"].values, sel["dt"].values)]
    row["n_L1"] = int(len(L1))
    row["mean_events_decl"] = round(float(L1["rdm"].mean()), 2)
    row["mean_daily_decl"]  = round(float(L1.groupby("date")["rdm"].mean().mean()), 2)
    # meme chose sur le rendement BRUT (non demeane), pour situer
    row["gross_events_decl"] = round(float(L1["fwd"].mean()), 2)
    row["gross_daily_decl"]  = round(float(L1.groupby("date")["fwd"].mean().mean()), 2)
    row["daymean_of_events_decl"] = round(float(L1["dmv"].mean()), 2)
    row["daymean_daily_decl"] = round(float(L1.groupby("date")["dmv"].mean().mean()), 2)
    out[f"p={p}"] = row
    print(f"p={p:<5} n={row['n']:>8} n_L1={row['n_L1']:>7} | sans decl: evt {row['mean_events_nodecl']:>7} / jour {row['mean_daily_nodecl']:>7}"
          f" | avec decl: evt {row['mean_events_decl']:>7} / jour {row['mean_daily_decl']:>7}"
          f" | brut_jour {row['gross_daily_decl']:>7} moyennejour_jour {row['daymean_daily_decl']:>7}", flush=True)
json.dump(out, open(OUT+"/artifact_isolation.json","w"), indent=1, default=str)
print("\necrit:", OUT+"/artifact_isolation.json")
