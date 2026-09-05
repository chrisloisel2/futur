#!/usr/bin/env python
"""W2 -- T4 forensics: isolate the leak, and test the STRONGEST PIT-legal form of T4.

Three signals on exactly the same rows / horizon / cost model:

  v1_LEAKY   forward sum over [t, t+H) of the WHOLE scheduled-flow matrix.
             Includes TWAPs created after t -> not knowable at t.  This is the bug.
  v2b_CLEAN_RESIDUAL  forward sum over [t, t+H) restricted to TWAPs already created at t
             (their remaining programmed schedule).  This is exactly what the
             preregistration T4 describes and it IS PIT-legal.
  v2a_CLEAN_TRAILING  trailing 1h mean of the currently-known flow rate (most conservative).

Also reports the share of the v1 forward-window notional that is literally unknowable at t.
Re-executable: .venv/bin/python evidence/run_flow_leak_diag.py
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

ev = pd.read_parquet(f"{SC}/events.parquet", columns=["si", "ic", "mins", "ntl_planned", "dir"])
si = ev.si.values.astype(np.int64); ic = ev.ic.values.astype(np.int64)
dur = np.clip((ev.mins.values*60000/STEP).astype(np.int64), 1, 2016)
rate = np.nan_to_num(ev.ntl_planned.values)/dur
dsign = ev["dir"].values
LAG = 3                                     # 15 min detection latency
H = 48                                      # 4h horizon, non-overlapping grid
W7 = 2016
GRID = np.arange(W7+300, NB-W7-300-H, H)
st = ic+LAG; en = ic+1+dur                  # [known-from, programmed-end) in bars

F = np.zeros((NS, NB), np.float32); A = np.zeros((NS, NB), np.float32)
for k in range(int((en-st).max())):
    m = (st+k) < en
    if not m.any(): break
    idx = st[m]+k; ok = (idx >= 0) & (idx < NB)
    np.add.at(F, (si[m][ok], idx[ok]), (rate[m]*dsign[m])[ok].astype(np.float32))
    np.add.at(A, (si[m][ok], idx[ok]), rate[m][ok].astype(np.float32))
CF = np.cumsum(F.astype(np.float64), axis=1); CA = np.cumsum(A.astype(np.float64), axis=1)
del F, A

fwd_signed = CF[:, GRID+H] - CF[:, GRID]        # v1 numerator (LEAKY)
fwd_abs = CA[:, GRID+H] - CA[:, GRID]
trail_signed = (CF[:, GRID] - CF[:, GRID-12])*H/12.0    # v2a, rescaled to H bars
del CF, CA

# ---- split the forward window into "already created at t" vs "created after t"
# each event's start falls in exactly one grid interval (grid spacing == H)
j = np.searchsorted(GRID, st, side="right")-1
new_signed = np.zeros((NS, len(GRID))); new_abs = np.zeros((NS, len(GRID)))
# (en > st) drops TWAPs shorter than the detection lag: they never become "known" and
# contribute nothing to F/A either -- without this guard the overlap goes negative.
ok = ((j >= 0) & (j < len(GRID)) & (en > st)
      & (st > GRID[np.clip(j, 0, len(GRID)-1)]))
jj = j[ok]
overlap = np.clip(np.minimum(GRID[jj]+H, en[ok]) - st[ok], 0, None)
np.add.at(new_signed, (si[ok], jj), rate[ok]*dsign[ok]*overlap)
np.add.at(new_abs, (si[ok], jj), rate[ok]*overlap)
known_signed = fwd_signed - new_signed                  # v2b (PIT-legal residual schedule)

q24 = CQ[:, GRID] - CQ[:, np.maximum(GRID-288, 0)]
with np.errstate(all="ignore"):
    rr = np.log(CLOSE[:, GRID+H].astype(np.float64)/OPEN[:, GRID].astype(np.float64))
mn = (rr - (MKT[GRID+H]-MKT[GRID-1])[None, :])*1e4
mn = np.where(np.isfinite(mn) & (np.abs(mn) < 15000), mn, np.nan)

act = fwd_abs > 0
valid = np.isfinite(mn) & np.isfinite(OPEN[:, GRID]) & (q24 > 0)
m = valid & act
unknown_share = new_abs[m]/np.maximum(fwd_abs[m], 1e-9)
leak = {
  "horizon_bars": H, "horizon_minutes": H*5, "detection_lag_bars": LAG,
  "n_symbol_bar_obs": int(m.sum()),
  "share_of_forward_notional_not_yet_created_at_t": {
      "mean": round(float(np.nanmean(unknown_share)), 4),
      "median": round(float(np.nanmedian(unknown_share)), 4),
      "p25": round(float(np.nanquantile(unknown_share, .25)), 4),
      "p75": round(float(np.nanquantile(unknown_share, .75)), 4)},
  "corr_v1_leaky_vs_v2b_known": round(float(np.corrcoef(
      fwd_signed[m]/np.maximum(q24[m], 1), known_signed[m]/np.maximum(q24[m], 1))[0, 1]), 4),
}
print(json.dumps(leak, indent=1))

ts = T0 + GRID*STEP
day = pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m-%d")
rows = []
for jx in range(len(GRID)):
    k = np.where(m[:, jx])[0]
    if len(k) < 8: continue
    rows.append(pd.DataFrame({
        "si": k, "gj": jx, "day": day[jx], "mn": mn[k, jx], "q24": q24[k, jx],
        "v1_LEAKY": fwd_signed[k, jx]/np.maximum(q24[k, jx], 1),
        "v2b_CLEAN_RESIDUAL": known_signed[k, jx]/np.maximum(q24[k, jx], 1),
        "v2a_CLEAN_TRAILING": trail_signed[k, jx]/np.maximum(q24[k, jx], 1)}))
P = pd.concat(rows, ignore_index=True)
P["year"] = P.day.str[:4]
print("panel", P.shape, P.day.min(), P.day.max())


def run(col, name):
    out = []
    for gj, g in P.groupby("gj"):
        if len(g) < 8: continue
        lo, hi = g[col].quantile([0.2, 0.8])
        L, S = g[g[col] >= hi], g[g[col] <= lo]
        if not len(L) or not len(S): continue
        out.append({"gj": gj, "day": g.day.iloc[0], "year": g.year.iloc[0],
                    "spread_bps": float(L.mn.mean()-S.mn.mean()),
                    "long_bps": float(L.mn.mean()), "short_bps": float(S.mn.mean()),
                    "cap": float(0.005*np.median(np.r_[L.q24.values, S.q24.values])*H/288.0)})
    S = pd.DataFrame(out)
    S["usr"] = S.gj.astype(str); S["coin"] = "LS"
    r = gate(S, "spread_bps", name)
    ics = P.groupby("gj").apply(lambda g: g[col].corr(g.mn, method="spearman") if len(g) >= 8 else np.nan).dropna()
    d2 = P.groupby("gj").day.first()
    dm = pd.DataFrame({"ic": ics.values, "day": d2.loc[ics.index].values}).groupby("day").ic.mean()
    r["ic_mean"] = round(float(dm.mean()), 5)
    r["ic_t_day"] = round(float(dm.mean()/(dm.std(ddof=1)/np.sqrt(len(dm)))), 2)
    te = dm[dm.index >= "2025-09-01"]; h = len(te)//2
    r["ic_test_h1"], r["ic_test_h2"] = round(float(te.iloc[:h].mean()), 5), round(float(te.iloc[h:].mean()), 5)
    r["long_leg_bps"] = round(float(S.long_bps.mean()), 2)
    r["short_leg_bps"] = round(float(S.short_bps.mean()), 2)
    r["capacity_usd_estimate"] = int(np.median(S.cap))
    r["train_bps"] = round(float(S.loc[S.day < "2025-09-01", "spread_bps"].mean()), 2)
    r["test_bps"] = round(float(S.loc[S.day >= "2025-09-01", "spread_bps"].mean()), 2)
    r["n_rebalances"] = int(len(S))
    return r


res = [run(c, f"T4_FLOW_IMBALANCE_XS_LS_4h_{c}") for c in
       ("v1_LEAKY", "v2b_CLEAN_RESIDUAL", "v2a_CLEAN_TRAILING")]
json.dump({"leak_diagnostic": leak, "mechanisms": res},
          open(f"{OUT}/flow_leak_diagnostic.json", "w"), indent=1, default=str)
print(pd.DataFrame(res)[["mechanism", "n_raw", "n_independent_L3_day", "gross_bps", "net_bps",
                         "net_bps_stress28", "t_stat_declustered_L3day", "bootstrap_ci95",
                         "ic_mean", "ic_t_day", "ic_test_h1", "ic_test_h2", "train_bps",
                         "test_bps", "eta_forward_confirmation_years",
                         "capacity_usd_estimate"]].to_string(index=False))
