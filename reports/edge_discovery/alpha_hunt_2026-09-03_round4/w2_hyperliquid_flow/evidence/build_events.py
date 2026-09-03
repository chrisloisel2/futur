#!/usr/bin/env python
"""W2 — step 2: join TWAP activations to the Binance 5m panel and compute PIT forward returns.

Every price used is strictly at or after event_time + LAG. Terminal TWAP fields
(executedNtl / final status / end_ms) are carried for description only and are never
used to select or weight an event.
"""
import os, json, numpy as np, pandas as pd, duckdb

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
Z = np.load(f"{SC}/panel.npz", allow_pickle=True)
OPEN, CLOSE, QVOL = Z["OPEN"], Z["CLOSE"], Z["QVOL"]
MKT, T0, STEP, NB = Z["mkt_idx"], int(Z["grid_t0"]), int(Z["step"]), int(Z["nb"])
SYMS = list(Z["symbols"]); SIDX = {s: i for i, s in enumerate(SYMS)}
CQ = np.cumsum(QVOL.astype(np.float64), axis=1)

LAGS = [0, 5, 15, 30, 60]                       # minutes
HOR  = [15, 30, 60, 120, 240, 720, 1440]        # minutes

cmap = json.load(open(f"{SC}/coin_map.json"))
con = duckdb.connect()
ep = con.execute(f"select * from read_parquet('{SC}/twap_ep.parquet')").df()
ep = ep[ep.coin.isin(cmap)].copy()
ep["sym"] = ep.coin.map(cmap)
ep = ep[(ep.create_ms >= T0 + 300*STEP) & (ep.create_ms < T0 + (NB-300)*STEP)].copy()
ep["si"] = ep.sym.map(SIDX).astype(np.int32)
ep["ic"] = ((ep.create_ms.values - T0)//STEP).astype(np.int64)
ep["dir"] = np.where(ep.side.values == "B", 1.0, -1.0)
print("events in panel window:", len(ep))

si, ic = ep.si.values, ep.ic.values
px_at_create = CLOSE[si, ic-1]
ep["ntl_planned"] = ep.sz.values * px_at_create
ep["qvol24h"] = CQ[si, ic-1] - CQ[si, np.maximum(ic-1-288, 0)]
ep["size_ratio"] = ep.ntl_planned / np.maximum(ep.qvol24h, 1.0)


def fwd(entry_i, si, nbars):
    """log(close[entry_i+nbars] / open[entry_i]) and matching market log return."""
    j = entry_i + nbars
    ok = (entry_i > 0) & (j < NB)
    j = np.clip(j, 0, NB-1); e = np.clip(entry_i, 1, NB-1)
    po = OPEN[si, e].astype(np.float64)
    pc = CLOSE[si, j].astype(np.float64)
    # tolerate a missing bar: walk exit back up to 3 bars
    for k in range(1, 4):
        bad = ~np.isfinite(pc)
        if not bad.any(): break
        pc[bad] = CLOSE[si[bad], np.maximum(j[bad]-k, 0)].astype(np.float64)
    with np.errstate(all="ignore"):
        r = np.log(pc/po)
    m = MKT[j] - MKT[e-1]
    good = ok & np.isfinite(r) & (np.abs(r) < 1.0)
    r = np.where(good, r, np.nan); m = np.where(good, m, np.nan)
    return r, m


out = ep[["usr", "coin", "sym", "create_ms", "side", "sz", "mins", "ro", "rnd",
          "fin_exec_ntl", "final_st", "dir", "ntl_planned", "qvol24h", "size_ratio",
          "si", "ic"]].copy()
out["day"] = pd.to_datetime(out.create_ms, unit="ms", utc=True).dt.strftime("%Y-%m-%d")
out["year"] = out.day.str[:4]

for lag in LAGS:
    ei = ic + int(np.ceil(lag*60000/STEP))
    ei = np.where((ei*STEP + T0) < (ep.create_ms.values + lag*60000), ei+1, ei)
    out[f"ei_{lag}"] = ei
    for h in HOR:
        r, m = fwd(ei, si, h//5)
        out[f"r{h}_lag{lag}"] = (r*1e4).astype(np.float32)
        out[f"mn{h}_lag{lag}"] = ((r-m)*1e4).astype(np.float32)
    # horizon = the TWAP's own programmed duration, and the equal-length window after it
    nb_dur = np.clip((ep.mins.values*60000/STEP).astype(np.int64), 1, 1440//5*3)
    r, m = fwd(ei, si, nb_dur)
    out[f"rDUR_lag{lag}"] = (r*1e4).astype(np.float32)
    out[f"mnDUR_lag{lag}"] = ((r-m)*1e4).astype(np.float32)
    r2, m2 = fwd(ei+nb_dur, si, nb_dur)
    out[f"rPOST_lag{lag}"] = (r2*1e4).astype(np.float32)
    out[f"mnPOST_lag{lag}"] = ((r2-m2)*1e4).astype(np.float32)

# placebo controls: same symbol, same clock time, +/- 7 days.
# They measure the persistent symbol x direction drift (idiosyncratic momentum) that a
# beta=1 market-neutral adjustment does NOT remove.  The honest edge is signal - placebo.
pcols = {}
for off, tag in ((-7, "m7"), (+7, "p7")):
    pl = ic + int(off*24*3600*1000)//STEP
    for h in (60, 240, 720, 1440):
        r, m = fwd(pl + 3, si, h//5)
        pcols[f"plc{tag}_mn{h}"] = ((r-m)*1e4).astype(np.float32)
    nb_dur = np.clip((ep.mins.values*60000/STEP).astype(np.int64), 1, 1440//5*3)
    r, m = fwd(pl + 3, si, nb_dur)
    pcols[f"plc{tag}_mnDUR"] = ((r-m)*1e4).astype(np.float32)
out = pd.concat([out, pd.DataFrame(pcols, index=out.index)], axis=1)

out.to_parquet(f"{SC}/events.parquet", index=False)
print("written", len(out), os.path.getsize(f"{SC}/events.parquet")/1e6, "MB")
print(out[["mn60_lag15", "mnDUR_lag15", "mnPOST_lag15"]].describe().to_string())
