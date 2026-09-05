#!/usr/bin/env python3
"""W9 Phase 2 — TEST DE PLAUSIBILITE de l'effet « position dans le range ».

L'effet mesure ~+90 a +115 bps par episode, avec un t > 17 sur ~2 200 jours independants.
Sur un livre de ~80 positions/jour tenues 24 h, cela impliquerait un Sharpe annualise ~7,6 :
prima facie impossible pour un signal de reversion aussi simple. Ce script cherche l'erreur.

  T1 mediane vs moyenne          : l'effet est-il porte par la queue (non exploitable en taille) ?
  T2 horizon conscient des trous : shift(-24) est un decalage de 24 LIGNES ; si le panel a des
                                   trous horaires, il saute par-dessus. On recalcule en exigeant
                                   la barre a exactement t+24 h.
  T3 winsorisation               : effet apres ecretage des rendements a +/-3 sigma roulants.
  T4 profil du Sharpe            : moyenne/ecart-type journaliers L2 -> Sharpe implicite.
Sortie : evidence/PLAUSIBILITY.json
"""
import sys, os, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
DISC = pd.Timestamp("2026-01-01", tz="UTC")

def decl(sym, ts):
    keep = np.zeros(len(sym), bool); last = {}
    for k in range(len(sym)):
        s = sym[k]; t = ts[k]
        if s not in last or (t - last[s]) / np.timedelta64(1, "h") >= 24:
            keep[k] = True; last[s] = t
    return keep

def analyse(df, name):
    df = df.sort_values(["symbol", "dt"]).reset_index(drop=True)
    g = df.groupby("symbol", sort=False)
    c = df["close"]
    rmax = g["close"].transform(lambda s: s.rolling(20, min_periods=20).max())
    rmin = g["close"].transform(lambda s: s.rolling(20, min_periods=20).min())
    df["mm20"] = (c - rmin) / (rmax - rmin).replace(0, np.nan)
    df["fwd_rows"] = (g["close"].shift(-24) / c - 1.0) * 1e4            # decalage de 24 LIGNES
    # T2 : horizon conscient des trous — la barre a exactement t+24 h
    tgt = df[["symbol", "dt", "close"]].copy()
    tgt["dt"] = tgt["dt"] - pd.Timedelta(hours=24)
    tgt = tgt.rename(columns={"close": "close_t24"})
    df = df.merge(tgt, on=["symbol", "dt"], how="left")
    df["fwd_time"] = (df["close_t24"] / df["close"] - 1.0) * 1e4
    df["date"] = df["dt"].dt.floor("D")
    out = {}
    for period, m_per in (("discovery", df["dt"] < DISC), ("oos", df["dt"] >= DISC)):
        for rc in ("fwd_rows", "fwd_time"):
            pop = df[m_per & df["mm20"].notna() & df[rc].notna()]
            if len(pop) < 5000: continue
            dm = pop.groupby("date")[rc].mean()
            for nm, mask, side in (("bottom_long", pop["mm20"] <= 0.10, +1), ("top_short", pop["mm20"] >= 0.90, -1)):
                d = pop[mask].copy()
                d["ret"] = d[rc] * side
                d["ret_dm"] = d["ret"] - d["date"].map(dm).values * side
                d = d.sort_values("dt")
                L1 = d[decl(d["symbol"].values, d["dt"].values)]
                # T1 : mediane vs moyenne, au niveau EPISODE
                ep_mean = float(L1["ret_dm"].mean()); ep_med = float(L1["ret_dm"].median())
                # T3 : winsorisation a +/- 500 bps (ordre de grandeur de 1 sigma 24h) puis +/-1000
                w5 = float(L1["ret_dm"].clip(-500, 500).mean()); w10 = float(L1["ret_dm"].clip(-1000, 1000).mean())
                L2 = L1.groupby("date")["ret_dm"].mean()
                mu = float(L2.mean()); sd = float(L2.std(ddof=1)); n = len(L2)
                out[f"{name}|{period}|{rc}|{nm}"] = dict(
                    n_L1=int(len(L1)), n_L2=n, positions_per_day=round(len(L1)/max(n,1), 1),
                    episode_mean_bps=round(ep_mean, 2), episode_median_bps=round(ep_med, 2),
                    episode_frac_positive=round(float((L1["ret_dm"] > 0).mean()), 4),
                    mean_winsor_500bps=round(w5, 2), mean_winsor_1000bps=round(w10, 2),
                    daily_mean_bps=round(mu, 2), daily_std_bps=round(sd, 2),
                    daily_sharpe=round(mu/sd, 3) if sd else None,
                    annualised_sharpe=round(mu/sd*np.sqrt(365), 2) if sd else None,
                    t=round(mu/(sd/np.sqrt(n)), 2) if sd else None)
                print(" ", f"{name}|{period}|{rc}|{nm}", out[f"{name}|{period}|{rc}|{nm}"], flush=True)
    return out

res = {}
print("=== ENRICHED (frozen-50) ===", flush=True)
e = pd.read_parquet(OUT+"/panel.parquet", columns=["symbol","datetime","close"]).rename(columns={"datetime":"dt"})
e["dt"] = pd.to_datetime(e["dt"], utc=True)
res.update(analyse(e, "ENRICHED")); del e
print("\n=== PIT (data-v2, 312 symboles) ===", flush=True)
p = pd.read_parquet(OUT+"/pit_1h.parquet", columns=["symbol","dt","close"])
p["dt"] = pd.to_datetime(p["dt"], utc=True)
res.update(analyse(p, "PIT")); del p
json.dump(res, open(OUT+"/plausibility.json","w"), indent=1, default=str)
print("\necrit:", OUT+"/plausibility.json")
