#!/usr/bin/env python3
"""W9 Phase 2 — moteur de gate (declustering 3 niveaux, bootstrap par blocs, ETA forward).
Toutes les conditions sont evaluees a la barre t avec de l'information <= t. Sortie t+H."""
import numpy as np, pandas as pd, os, json
OUT=os.environ.get("W9_OUT","/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
RNG=np.random.default_rng(20260903)
COST=14.0; COST_STRESS=28.0
HORIZONS=[1,4,8,24]
DISCOVERY_END=pd.Timestamp("2026-01-01",tz="UTC")

def load():
    df=pd.read_parquet(OUT+"/panel.parquet")
    df["datetime"]=pd.to_datetime(df["datetime"],utc=True)
    df=df.sort_values(["symbol","datetime"]).reset_index(drop=True)
    g=df.groupby("symbol",sort=False)["close"]
    for H in HORIZONS:
        df[f"fwd{H}"]=(g.shift(-H)/df["close"]-1.0)*1e4      # bps, forward-only
    df["date"]=df["datetime"].dt.floor("D")
    df["week"]=df["datetime"].dt.to_period("W").astype(str)
    df["year"]=df["datetime"].dt.year
    return df

def causal_pct(s, win=500):
    """rang percentile roulant CAUSAL (inclut t, jamais t+1)."""
    return s.rolling(win, min_periods=100).rank(pct=True)

def decluster(sub):
    """L1 : un evenement max par symbole par fenetre glissante de 24h (greedy chronologique)."""
    keep=[]
    for sym, g in sub.groupby("symbol", sort=False):
        last=None
        for i, t in zip(g.index.values, g["datetime"].values):
            if last is None or (t-last)/np.timedelta64(1,"h") >= 24:
                keep.append(i); last=t
    return sub.loc[keep]

def block_boot(vals, blocks, n=2000):
    """block-bootstrap : on retire des BLOCS (semaines) entiers, pas des observations."""
    ub=pd.unique(blocks); idx={b:np.where(blocks==b)[0] for b in ub}
    out=np.empty(n)
    for i in range(n):
        pick=RNG.choice(ub, size=len(ub), replace=True)
        v=np.concatenate([vals[idx[b]] for b in pick])
        out[i]=v.mean()
    return np.percentile(out,[2.5,97.5])

def evaluate(name, df, mask, side, H, note=""):
    d=df[mask & df[f"fwd{H}"].notna() & (df["datetime"]<DISCOVERY_END)].copy()
    pop=df[df[f"fwd{H}"].notna() & (df["datetime"]<DISCOVERY_END)]
    if len(d)<50:
        return dict(mechanism=name, horizon_h=H, verdict="DATA_LIMITED", n_raw=int(len(d)), note="N<50 "+note)
    d["ret"]=d[f"fwd{H}"]*side
    # controle 1 : moyenne inconditionnelle meme population
    ctrl_uncond=(pop[f"fwd{H}"]*side).mean()
    # controle 2 (plus strict) : rendement moyen du meme jour calendaire, tous symboles
    daymean=pop.groupby("date")[f"fwd{H}"].mean()*side
    d["ret_dm"]=d["ret"]-d["date"].map(daymean).values
    n_raw=len(d)
    L1=decluster(d)
    L2=L1.groupby("date")["ret_dm"].mean()
    L2raw=L1.groupby("date")["ret"].mean()
    L3=L1.groupby("week")["ret_dm"].mean()
    n1,n2,n3=len(L1),len(L2),len(L3)
    if n2<20:
        return dict(mechanism=name, horizon_h=H, verdict="DATA_LIMITED", n_raw=n_raw,
                    n_independent_L1=n1,n_independent_L2=n2,n_independent_L3=n3, note="N_L2<20 "+note)
    mu=float(L2.mean()); sd=float(L2.std(ddof=1))
    t=mu/(sd/np.sqrt(n2)) if sd>0 else 0.0
    gross=float(L2raw.mean())
    net=gross-COST; net28=gross-COST_STRESS
    wk=L1.groupby("date")["week"].first().reindex(L2.index).values
    ci=block_boot(L2.values, wk)
    yby={}
    for y,g in L1.groupby("year"):
        gl2=g.groupby("date")["ret_dm"].mean()
        if len(gl2)>=5: yby[int(y)]=dict(n_L2=int(len(gl2)), edge_bps=round(float(gl2.mean()),2))
    if yby:
        best=max(yby, key=lambda k: yby[k]["edge_bps"])
        rest=[k for k in yby if k!=best]
        wsum=sum(yby[k]["n_L2"] for k in rest)
        ex_best=round(sum(yby[k]["edge_bps"]*yby[k]["n_L2"] for k in rest)/wsum,2) if wsum else None
    else: best, ex_best = None, None
    # n_required : power 80%, alpha 5%, edge haircute 50%
    n_req=int(np.ceil((1.96+0.84)**2 * sd**2 / (0.5*abs(mu))**2)) if mu!=0 else 10**9
    # event_rate : episodes L2 par semaine sur les 6 derniers mois de la periode de decouverte
    cut=DISCOVERY_END-pd.Timedelta(days=182)
    recent=L1[L1["datetime"]>=cut]
    n_recent=recent.groupby("date").ngroups if len(recent) else 0
    rate=n_recent/26.0
    eta_w=n_req/rate if rate>0 else float("inf")
    eta_y=eta_w/52.0
    # verdict (seuils du PREREGISTRATION)
    if abs(t)<2.0: v="WEAK"
    elif net<=0: v="DEAD"
    elif net28<=0: v="COST_FRAGILE"
    elif ex_best is not None and ex_best<=0: v="REGIME_DEPENDENT"
    elif eta_y>=3.0: v="UNCONFIRMABLE_IN_HORIZON"
    elif t>=2.5 and ci[0]>0 and net28>0: v="VALIDATED_FOR_FORWARD"
    else: v="PROMISING_NEEDS_VALIDATION"
    return dict(mechanism=name, horizon_h=H, side=int(side), n_raw=int(n_raw),
        n_independent_L1=int(n1), n_independent_L2=int(n2), n_independent_L3=int(n3),
        gross_bps=round(gross,2), net_bps=round(net,2), net_bps_stress28=round(net28,2),
        edge_vs_control_bps=round(mu,2), control_uncond_bps=round(float(ctrl_uncond),2),
        t_stat_declustered=round(float(t),2),
        bootstrap_ci95=[round(float(ci[0]),2),round(float(ci[1]),2)],
        year_by_year=yby, best_year=best, ex_best_year=ex_best,
        n_required=int(n_req), event_rate_per_week=round(rate,2),
        eta_forward_days=round(eta_w*7,1) if np.isfinite(eta_w) else None,
        eta_forward_years=round(eta_y,2) if np.isfinite(eta_y) else None,
        verdict=v, note=note)
