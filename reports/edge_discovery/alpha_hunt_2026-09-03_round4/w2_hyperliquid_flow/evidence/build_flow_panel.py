#!/usr/bin/env python
"""W2 — step 3 (v2, PIT-corrected).

(a) trailing (past) market-neutral returns on the event table -> momentum control
(b) KNOWN-SCHEDULED-FLOW panel.

PIT bug found and fixed in v1: summing the scheduled-flow matrix FORWARD over [t, t+H)
counts TWAPs that are created *inside* that window, i.e. it uses knowledge of orders that
do not exist yet at decision time t.  v2 only ever reads the flow matrix at bars <= t.

signal(t) = trailing 1-hour mean of the *currently known* net signed TWAP flow rate
            (USD per 5-min bar), where a TWAP is "known" only from bar ic + LAG onward
            (LAG = detection latency in 5-min bars) and only inside its own programmed
            [creation, creation+minutes] window.
Every bar read is <= t.  Outcome = market-neutral Binance return from open(t) to close(t+H).
"""
import os, sys, numpy as np, pandas as pd

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
Z = np.load(f"{SC}/panel.npz", allow_pickle=True)
OPEN, CLOSE, QVOL = Z["OPEN"], Z["CLOSE"], Z["QVOL"]
MKT, T0, STEP, NB = Z["mkt_idx"], int(Z["grid_t0"]), int(Z["step"]), int(Z["nb"])
SYMS = list(Z["symbols"]); NS = len(SYMS)
CQ = np.cumsum(QVOL.astype(np.float64), axis=1)
W7 = 2016                      # 7 days in 5-min bars
LAG_BARS = [1, 3, 6, 12]       # 5 / 15 / 30 / 60 min detection latency

ev = pd.read_parquet(f"{SC}/events.parquet")
si = ev.si.values.astype(np.int64); ic = ev.ic.values.astype(np.int64)

if "trail_mn1440" not in ev.columns:
    for h in (240, 1440, 10080):
        nb = h//5
        a = np.clip(ic-1-nb, 0, NB-1); b = np.clip(ic-1, 0, NB-1)
        with np.errstate(all="ignore"):
            r = np.log(CLOSE[si, b].astype(np.float64)/CLOSE[si, a].astype(np.float64))
        v = np.where(np.isfinite(r) & (np.abs(r) < 1.5), (r-(MKT[b]-MKT[a]))*1e4, np.nan)
        ev[f"trail_mn{h}"] = v.astype(np.float32)
    ev.to_parquet(f"{SC}/events.parquet", index=False)
    print("trailing returns added")

dur = np.clip((ev.mins.values*60000/STEP).astype(np.int64), 1, 2016)
rate = np.nan_to_num(ev.ntl_planned.values)/dur          # USD per 5-min bar
d = ev["dir"].values


def flow_matrices(lag):
    """FLOW[s,b] = net signed known TWAP rate at bar b; AFL = absolute. A TWAP is known only
    from bar ic+lag and only until its programmed end ic+1+dur."""
    F = np.zeros((NS, NB), np.float32); A = np.zeros((NS, NB), np.float32)
    st = ic+lag; en = ic+1+dur
    for k in range(int((en-st).max())):
        m = (st+k) < en
        if not m.any(): break
        idx = st[m]+k; ok = (idx >= 0) & (idx < NB)
        np.add.at(F, (si[m][ok], idx[ok]), (rate[m]*d[m])[ok].astype(np.float32))
        np.add.at(A, (si[m][ok], idx[ok]), rate[m][ok].astype(np.float32))
    return F, A


GRID = np.arange(W7+300, NB-W7-300, 12)          # hourly decision grid
frames = {}
for lag in LAG_BARS:
    F, A = flow_matrices(lag)
    # trailing 1h mean of the known rate: bars t-11..t  (all <= t)
    CF = np.cumsum(F.astype(np.float64), axis=1)
    CA = np.cumsum(A.astype(np.float64), axis=1)
    sig = (CF[:, GRID] - CF[:, GRID-12])/12.0
    asig = (CA[:, GRID] - CA[:, GRID-12])/12.0
    frames[lag] = (sig, asig)
    print("lag", lag, "built", flush=True)
    del F, A, CF, CA

rows = []
for s in range(NS):
    q24 = CQ[s, GRID] - CQ[s, np.maximum(GRID-288, 0)]
    keep = (frames[3][1][s] > 0) & (q24 > 0) & np.isfinite(OPEN[s, GRID])
    if not keep.any(): continue
    g = GRID[keep]

    def mnret(a, b):
        a = np.clip(a, 1, NB-1); b = np.clip(b, 1, NB-1)
        with np.errstate(all="ignore"):
            rr = np.log(CLOSE[s, b].astype(np.float64)/CLOSE[s, a].astype(np.float64))
        vv = (rr-(MKT[b]-MKT[a]))*1e4
        return np.where(np.isfinite(vv) & (np.abs(vv) < 15000), vv, np.nan).astype(np.float32)

    def mnret_open(b):
        with np.errstate(all="ignore"):
            rr = np.log(CLOSE[s, np.clip(b, 1, NB-1)].astype(np.float64)/OPEN[s, g].astype(np.float64))
        vv = (rr-(MKT[np.clip(b, 1, NB-1)]-MKT[g-1]))*1e4
        return np.where(np.isfinite(vv) & (np.abs(vv) < 15000), vv, np.nan).astype(np.float32)

    dd = {"si": s, "bar": g, "qvol24h": q24[keep],
          "mn60": mnret_open(g+12), "mn240": mnret_open(g+48), "mn720": mnret_open(g+144),
          "trail240": mnret(g-48, g), "trail1440": mnret(g-288, g),
          "plcm7_mn240": mnret(g-W7, g-W7+48), "plcp7_mn240": mnret(g+W7, g+W7+48),
          "adv_usd_5m": (q24[keep]/288.0)}
    for lag in LAG_BARS:
        dd[f"flow_l{lag}"] = frames[lag][0][s][keep]
        dd[f"aflow_l{lag}"] = frames[lag][1][s][keep]
    rows.append(pd.DataFrame(dd))
    if s % 25 == 0: print("  sym", s, flush=True)

P = pd.concat(rows, ignore_index=True)
P["sym"] = [SYMS[i] for i in P.si.values]
P["ts"] = T0 + P.bar.values*STEP
P["day"] = pd.to_datetime(P.ts, unit="ms", utc=True).dt.strftime("%Y-%m-%d")
P["year"] = P.day.str[:4]
for lag in LAG_BARS:
    P[f"imb_l{lag}"] = P[f"flow_l{lag}"]*48.0/np.maximum(P.qvol24h, 1.0)
P.to_parquet(f"{SC}/flow_panel.parquet", index=False)
print("flow panel", len(P), os.path.getsize(f"{SC}/flow_panel.parquet")/1e6, "MB")
