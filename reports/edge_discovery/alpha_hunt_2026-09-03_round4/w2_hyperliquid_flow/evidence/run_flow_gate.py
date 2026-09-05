#!/usr/bin/env python
"""W2 -- step 5: T4 (aggregated scheduled-flow imbalance), LEAKY v1 vs PIT-CLEAN v2.

The point of this script is to make the leak *reproducible* and to measure exactly how much
of the v1 result it created.

  LEAKY v1  signal(t) = sum of the scheduled net TWAP flow over the FORWARD window [t, t+H).
            At decision time t this is unknowable: most of that notional belongs to TWAPs that
            have not been created yet.  It is the classic "future order book" leak.
  CLEAN v2  signal(t) = trailing 1h mean of the net TWAP flow *already known* at t
            (a TWAP is known only from bar ic+LAG onward, and only inside its own programmed
            [creation, creation+minutes] window).  Every bar read is <= t.

Both signals are evaluated on the same rows, same horizon, same cost model, same declustering.
Re-executable: .venv/bin/python evidence/run_flow_gate.py
"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
OUT = os.path.dirname(os.path.abspath(__file__))

Z = np.load(f"{SC}/panel.npz", allow_pickle=True)
OPEN, CLOSE, QVOL = Z["OPEN"], Z["CLOSE"], Z["QVOL"]
MKT, T0, STEP, NB = Z["mkt_idx"], int(Z["grid_t0"]), int(Z["step"]), int(Z["nb"])
SYMS = list(Z["symbols"]); NS = len(SYMS)
CQ = np.cumsum(QVOL.astype(np.float64), axis=1)

ev = pd.read_parquet(f"{SC}/events.parquet",
                     columns=["si", "ic", "mins", "ntl_planned", "dir"])
si = ev.si.values.astype(np.int64); ic = ev.ic.values.astype(np.int64)
dur = np.clip((ev.mins.values*60000/STEP).astype(np.int64), 1, 2016)
rate = np.nan_to_num(ev.ntl_planned.values)/dur          # USD per 5-min bar
d = ev["dir"].values

H_BARS = 48                    # 4 h holding, NON-OVERLAPPING grid -> one real round trip
W7 = 2016
GRID = np.arange(W7+300, NB-W7-300-H_BARS, H_BARS)       # 4h decision grid, no overlap
LAG_BARS = 3                   # 15 min detection latency (preregistered principal lag)


def flow_matrix(lag):
    """F[s,b] = net signed scheduled TWAP rate (USD / 5min) at bar b, known from ic+lag."""
    F = np.zeros((NS, NB), np.float32)
    st = ic+lag; en = ic+1+dur
    for k in range(int((en-st).max())):
        m = (st+k) < en
        if not m.any(): break
        idx = st[m]+k; ok = (idx >= 0) & (idx < NB)
        np.add.at(F, (si[m][ok], idx[ok]), (rate[m]*d[m])[ok].astype(np.float32))
    return F


F = flow_matrix(LAG_BARS)
CF = np.cumsum(F.astype(np.float64), axis=1)
del F

q24 = CQ[:, GRID] - CQ[:, np.maximum(GRID-288, 0)]                # [NS, nG] trailing 24h quote vol
# --- CLEAN v2: trailing 1h mean of known flow, everything <= t
sig_clean = (CF[:, GRID] - CF[:, GRID-12])/12.0
# --- LEAKY v1: forward sum over [t, t+H) of the same matrix  (uses orders not yet created)
sig_leak = (CF[:, GRID+H_BARS] - CF[:, GRID])/float(H_BARS)
del CF

imb_clean = sig_clean*float(H_BARS)/np.maximum(q24, 1.0)
imb_leak = sig_leak*float(H_BARS)/np.maximum(q24, 1.0)

# --- forward market-neutral return, open(t) -> close(t+H)
with np.errstate(all="ignore"):
    rr = np.log(CLOSE[:, GRID+H_BARS].astype(np.float64)/OPEN[:, GRID].astype(np.float64))
mn = (rr - (MKT[GRID+H_BARS]-MKT[GRID-1])[None, :])*1e4
mn = np.where(np.isfinite(mn) & (np.abs(mn) < 15000), mn, np.nan)

# activity mask: at least one TWAP actually running and known at t
act = np.abs(sig_clean) > 0
valid = np.isfinite(mn) & np.isfinite(OPEN[:, GRID]) & (q24 > 0)

ts = T0 + GRID*STEP
day = pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m-%d")
rows = []
for j in range(len(GRID)):
    k = np.where(valid[:, j] & act[:, j])[0]
    if len(k) < 8:
        continue
    rows.append(pd.DataFrame({"si": k, "gj": j, "day": day[j],
                              "mn": mn[k, j], "imb_clean": imb_clean[k, j],
                              "imb_leak": imb_leak[k, j], "q24": q24[k, j]}))
P = pd.concat(rows, ignore_index=True)
P["sym"] = [SYMS[i] for i in P.si.values]
P["year"] = P.day.str[:4]
print("flow decision panel:", P.shape, P.day.min(), "->", P.day.max(),
      "| symbols/bar median:", int(P.groupby("gj").size().median()))


def ls_spread(P, col, q=0.2):
    """Cross-sectional long/short at each decision bar; returns one row per bar."""
    out = []
    for gj, g in P.groupby("gj"):
        if len(g) < 8:
            continue
        lo, hi = g[col].quantile([q, 1-q])
        L = g[g[col] >= hi]; S = g[g[col] <= lo]
        if not len(L) or not len(S):
            continue
        out.append({"gj": gj, "day": g.day.iloc[0], "year": g.year.iloc[0],
                    "spread_bps": float(L.mn.mean()-S.mn.mean()),
                    "long_bps": float(L.mn.mean()), "short_bps": float(S.mn.mean()),
                    "n_leg": min(len(L), len(S)),
                    "cap_usd": float(0.005*np.median(np.r_[L.q24.values, S.q24.values])
                                     * H_BARS/288.0)})
    return pd.DataFrame(out)


def ic_table(P, col):
    """day-averaged Spearman IC + sign stability on the two halves of TEST."""
    r = P.groupby("gj").apply(lambda g: g[col].corr(g.mn, method="spearman")
                              if len(g) >= 8 else np.nan)
    r = r.dropna()
    gj2day = P.groupby("gj").day.first()
    s = pd.DataFrame({"ic": r.values, "day": gj2day.loc[r.index].values})
    dm = s.groupby("day").ic.mean()
    t = float(dm.mean()/(dm.std(ddof=1)/np.sqrt(len(dm))))
    te = dm[dm.index >= "2025-09-01"]
    h = len(te)//2
    return {"ic_mean": round(float(dm.mean()), 5), "ic_t_day": round(t, 2),
            "n_days": int(len(dm)),
            "ic_test_h1": round(float(te.iloc[:h].mean()), 5) if h else None,
            "ic_test_h2": round(float(te.iloc[h:].mean()), 5) if h else None}


res, extra = [], {}
for tag, col in (("LEAKY_v1", "imb_leak"), ("PIT_CLEAN_v2", "imb_clean")):
    S = ls_spread(P, col)
    S["usr"] = S["sym"] = "LS"                      # gate() decluster keys: L1=L2=bar-day, L3=day
    S["coin"] = S.day
    S = S.rename(columns={"day": "day"})
    S["usr"] = S.gj.astype(str)
    S["coin"] = "LS"
    g = gate(S, "spread_bps", f"T4_FLOW_IMBALANCE_XS_LS_4h_{tag}")
    g["mechanism_family"] = "T4"
    g["long_leg_bps"] = round(float(S.long_bps.mean()), 2)
    g["short_leg_bps"] = round(float(S.short_bps.mean()), 2)
    g["capacity_usd_estimate"] = int(np.median(S.cap_usd))
    g["n_rebalances"] = int(len(S))
    g.update({("ic_"+k): v for k, v in ic_table(P, col).items()})
    g["train_bps"] = round(float(S.loc[S.day < "2025-09-01", "spread_bps"].mean()), 2)
    g["test_bps"] = round(float(S.loc[S.day >= "2025-09-01", "spread_bps"].mean()), 2)
    res.append(g)
    extra[tag] = {"n_rows_panel": int(len(P))}

# --- how much of the leaky signal is literally unknowable at t?
known = np.abs(sig_clean)*H_BARS
future = np.abs(sig_leak)*H_BARS
m = valid & act
frac = float(np.nanmedian(known[m]/np.maximum(future[m], 1e-9)))
leak_diag = {
  "median_ratio_known_over_forward_notional": round(frac, 4),
  "interpretation": ("at a 4h horizon the median share of the v1 forward-window scheduled "
                     "notional that is actually observable at decision time is "
                     f"{frac:.1%}; the remaining share belongs to TWAPs not yet created."),
}
print(json.dumps(leak_diag, indent=1))
json.dump({"mechanisms": res, "leak_diagnostic": leak_diag, "meta": extra},
          open(f"{OUT}/flow_gate_results.json", "w"), indent=1, default=str)
print(pd.DataFrame(res)[["mechanism", "n_raw", "n_independent_L3_day", "gross_bps", "net_bps",
                         "net_bps_stress28", "t_stat_declustered_L3day", "bootstrap_ci95",
                         "ic_ic_mean", "ic_ic_t_day", "ic_ic_test_h1", "ic_ic_test_h2",
                         "train_bps", "test_bps",
                         "eta_forward_confirmation_years"]].to_string(index=False))
