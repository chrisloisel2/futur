#!/usr/bin/env python3
"""W9 — RE-EXECUTION de H1-H4 avec le CONTROLE CORRIGE (meme barre horaire, pas meme jour).

Le controle du briefing (§1.3, « moyenne inconditionnelle du meme horizon sur la meme
population ») a ete implemente en v1 comme une moyenne par JOUR CALENDAIRE. Le test placebo
(control_level_test.py) montre que ce niveau laisse passer ~+80 bps de facteur marche des que
les evenements se concentrent sur certaines heures. Le controle correct est la moyenne
cross-sectionnelle a la MEME BARRE HORAIRE.
Chaque mecanisme est double d'un PLACEBO (permutation du signal entre symboles a instant egal).
Sortie : evidence/RERUN_CORRECTED.json
"""
import os, json, numpy as np, pandas as pd
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
DISC = pd.Timestamp("2026-01-01", tz="UTC"); OOS_END = pd.Timestamp("2026-06-29", tz="UTC")
COST, COST28 = 14.0, 28.0
SWITCH = {"BTCUSDT":"2026-01-01","ADAUSDT":"2026-05-20","AVAXUSDT":"2026-05-20","BNBUSDT":"2026-05-20",
          "ETHUSDT":"2026-05-20","LINKUSDT":"2026-05-20","SOLUSDT":"2026-05-20",
          "DOGEUSDT":"2026-05-24","XRPUSDT":"2026-05-24","DOTUSDT":"2026-06-28"}

def decl(sym, ts):
    keep = np.zeros(len(sym), bool); last = {}
    for k in range(len(sym)):
        s = sym[k]; t = ts[k]
        if s not in last or (t-last[s])/np.timedelta64(1,"h") >= 24:
            keep[k] = True; last[s] = t
    return keep

def blockboot(vals, blocks, n=2000, seed=20260905):
    rng = np.random.default_rng(seed)
    ub, inv = np.unique(blocks, return_inverse=True); nb = len(ub)
    bs = np.bincount(inv, weights=vals, minlength=nb); bc = np.bincount(inv, minlength=nb).astype(float)
    pk = rng.integers(0, nb, size=(n, nb))
    return np.percentile(bs[pk].sum(1)/bc[pk].sum(1), [2.5, 97.5])

df = pd.read_parquet(OUT+"/panel.parquet")
df["dt"] = pd.to_datetime(df["datetime"], utc=True)
df = df.sort_values(["symbol","dt"]).reset_index(drop=True)
g = df.groupby("symbol", sort=False)
for H in (4, 8, 24):
    df[f"fwd{H}"] = (g["close"].shift(-H)/df["close"]-1.0)*1e4
df["date"] = df["dt"].dt.floor("D"); df["week"] = df["dt"].dt.tz_localize(None).dt.to_period("W").astype(str)
df["year"] = df["dt"].dt.year
bad = np.zeros(len(df), bool)
for s, t0 in SWITCH.items():
    bad |= (df["symbol"].values == s) & (df["dt"].values >= np.datetime64(pd.Timestamp(t0, tz="UTC")))
df["bad"] = bad
cp = lambda s: s.rolling(500, min_periods=100).rank(pct=True)
df["uw"] = g["upper_wick_range"].transform(cp); df["lw"] = g["lower_wick_range"].transform(cp)
df["er"] = g["efficiency_ratio_20"].transform(cp)
df["ret8"] = (df["close"]/g["close"].shift(8)-1.0)*1e4
V, M, VP = df["volume_percentile_20"], df["minmax_norm_close_20"], df["volatility_percentile_20"]
DEFS = [("H1a_compression_breakout_long", (VP<=0.10)&(M>=0.80), +1),
        ("H1b_compression_breakdown_short",(VP<=0.10)&(M<=0.20), -1),
        ("H2a_upper_wick_exhaustion_short",(df.uw>=0.95)&(V>=0.95), -1),
        ("H2b_lower_wick_exhaustion_long", (df.lw>=0.95)&(V>=0.95), +1),
        ("H3a_momentum8h_trending",        (df.ret8>0)&(df.er>=0.80), +1),
        ("H3b_momentum8h_choppy",          (df.ret8>0)&(df.er<=0.20), +1),
        ("H4a_unconfirmed_range_high_short",(M>=0.95)&(V<=0.30), -1),
        ("H4b_confirmed_range_high_long",  (M>=0.95)&(V>=0.70), +1)]
rng = np.random.default_rng(31337)
res = []
for period in ("discovery","oos"):
    per = (df["dt"] < DISC) if period=="discovery" else ((df["dt"]>=DISC)&(df["dt"]<OOS_END)&~df["bad"])
    for H in (4, 8, 24):
        ok = per & df[f"fwd{H}"].notna()
        pop = df[ok].copy()
        pop["ctrl_hour"] = pop[f"fwd{H}"] - pop["dt"].map(pop.groupby("dt")[f"fwd{H}"].mean()).values
        pop["ctrl_day"]  = pop[f"fwd{H}"] - pop["date"].map(pop.groupby("date")[f"fwd{H}"].mean()).values
        for nm, mask, side in DEFS:
            m = mask.reindex(pop.index).fillna(False).values
            for arm in ("signal","placebo"):
                sel = m if arm=="signal" else rng.permutation(m)
                d = pop[sel].sort_values("dt")
                if len(d) < 500: continue
                L1 = d[decl(d["symbol"].values, d["dt"].values)]
                L2h = L1.groupby("date")["ctrl_hour"].mean()*side
                L2d = L1.groupby("date")["ctrl_day"].mean()*side
                L2r = L1.groupby("date")[f"fwd{H}"].mean()*side
                n2 = len(L2h)
                if n2 < 100: continue
                muh, sdh = float(L2h.mean()), float(L2h.std(ddof=1))
                th = muh/(sdh/np.sqrt(n2)) if sdh else 0
                wk = L1.groupby("date")["week"].first().reindex(L2h.index).values
                ci = blockboot(L2h.values, wk)
                gross = float(L2r.mean())
                yb = {int(y): round(float(gg.groupby("date")["ctrl_hour"].mean().mean()*side),2)
                      for y, gg in L1.groupby("year") if gg.groupby("date").ngroups>=5}
                exb = None
                if yb:
                    bst = max(yb, key=yb.get); rest=[v for k,v in yb.items() if k!=bst]
                    exb = round(float(np.mean(rest)),2) if rest else None
                res.append(dict(mechanism=nm, arm=arm, period=period, horizon_h=H, side=side,
                    n_raw=int(len(d)), n_L1=int(len(L1)), n_L2=n2, n_L3=int(L1.week.nunique()),
                    gross_bps=round(gross,2), net_bps=round(gross-COST,2), net_bps_stress28=round(gross-COST28,2),
                    edge_ctrl_DAY_bps=round(float(L2d.mean()),2),
                    edge_ctrl_HOUR_bps=round(muh,2), t_ctrl_HOUR=round(float(th),2),
                    ci95_ctrl_HOUR=[round(float(ci[0]),2), round(float(ci[1]),2)],
                    ex_best_year_ctrl_HOUR=exb, year_by_year_ctrl_HOUR=yb))
                if arm=="signal":
                    print(f"{period:9s} H={H:2d} {nm:34s} ctrlJOUR {res[-1]['edge_ctrl_DAY_bps']:>8} -> "
                          f"ctrlHEURE {muh:>7.2f} (t={th:>6.2f}) net={res[-1]['net_bps']:>7}", flush=True)
                else:
                    print(f"{'':9s}      {'  ^ placebo':34s} ctrlJOUR {res[-1]['edge_ctrl_DAY_bps']:>8} -> "
                          f"ctrlHEURE {muh:>7.2f} (t={th:>6.2f})", flush=True)
json.dump(res, open(OUT+"/rerun_corrected.json","w"), indent=1, default=str)
print("\necrit:", OUT+"/rerun_corrected.json")
