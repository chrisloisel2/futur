#!/usr/bin/env python
"""W2 -- the one Track A refinement worth defining properly.

The placebo audit showed the t-7d control is contaminated for 90% of events, and that on the
uncontaminated 10% the raw signed edge is ~2.5x larger.  That subset is really a
FIRST-TOUCH-AFTER-QUIET population: a TWAP arriving on a coin that had no known HL TWAP flow
for the previous K.  That is a clean, PIT-computable trigger, so it is gated here properly
instead of being left as an accident of the control design.

  QUIET_K : no OTHER TWAP on the same coin was KNOWN to be running at any bar of [t-K, t).
            "known" uses the same 15-min detection lag as everywhere else, so the trigger is
            computable live.

DECLARED REFIT: this trigger was not in PREREGISTRATION.md; it was found while auditing the
placebo.  It is reported with a strict train/test split and labelled REFIT throughout.

Re-executable: .venv/bin/python evidence/run_firsttouch_gate.py
"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
OUT = os.path.dirname(os.path.abspath(__file__))
LAGB = 3                                    # 15-min detection lag, in 5-min bars
d = pd.read_parquet(f"{SC}/events.parquet",
                    columns=["usr", "coin", "sym", "si", "ic", "day", "year", "side", "dir",
                             "mins", "ro", "ntl_planned", "qvol24h", "size_ratio",
                             "mn1440_lag15", "mn240_lag15", "plcm7_mn1440"])
d["S1440"] = d["dir"]*d["mn1440_lag15"]
d["S240"] = d["dir"]*d["mn240_lag15"]
d["A1440"] = d.S1440 - d["dir"]*d["plcm7_mn1440"]

dur = np.clip((d.mins.values*60000/300000).astype(np.int64), 1, 2016)
st = d.ic.values + LAGB
en = d.ic.values + 1 + dur
si = d.si.values
order = np.lexsort((st, si))
si_s, st_s, en_s = si[order], st[order], en[order]
bnd = np.r_[0, np.flatnonzero(np.diff(si_s)) + 1, len(si_s)]
sym_slice = {si_s[bnd[i]]: (bnd[i], bnd[i+1]) for i in range(len(bnd)-1)}

KS = {"24h": 288, "72h": 864, "7d": 2016}
flags = {k: np.zeros(len(d), bool) for k in KS}
t_end = d.ic.values                                     # window is [t-K, t): strictly past
for i in range(len(d)):
    a, b = sym_slice[si[i]]
    for k, K in KS.items():
        lo = t_end[i] - K
        j0 = np.searchsorted(st_s[a:b], t_end[i], side="left") + a   # started before t
        seg = slice(a, j0)
        m = (en_s[seg] > lo) & (st_s[seg] < t_end[i])
        # exclude the event itself (its own st == t + LAGB > t, so it is already excluded)
        flags[k][i] = not bool(m.any())
    if i % 50000 == 0:
        print("  scan", i, flush=True)

res = []
for k in KS:
    d[f"quiet_{k}"] = flags[k]
    sub = d[flags[k]]
    print(k, "n =", len(sub), f"({len(sub)/len(d):.1%})")
    for col, tag in (("S1440", "h24h_raw"), ("A1440", "h24h_PLACEBOADJ"), ("S240", "h4h_raw")):
        g = gate(sub, col, f"HLTWAP_QUIET{k}_FIRSTTOUCH_{tag} [REFIT]")
        if g.get("verdict") == "DATA_LIMITED":
            res.append(g); continue
        g["train_bps"] = round(float(sub.loc[sub.day < "2025-09-01", col].mean()), 2)
        g["test_bps"] = round(float(sub.loc[sub.day >= "2025-09-01", col].mean()), 2)
        g["median_ntl_planned_usd"] = int(np.nanmedian(sub.ntl_planned))
        hm = 1440 if "24h" in tag else 240
        cap = sub.qvol24h.values*(hm/1440.0)*0.005
        cap = cap[np.isfinite(cap) & (cap > 0)]
        g["capacity_usd_estimate"] = int(np.median(cap))
        g["trigger_share_of_all_twap"] = round(float(len(sub)/len(d)), 4)
        g["refit"] = True
        g["track"] = "A_binance_executed"
        res.append(g)

json.dump({"mechanisms": res, "quiet_definitions_bars": KS,
           "detection_lag_bars": LAGB},
          open(f"{OUT}/firsttouch_gate_results.json", "w"), indent=1, default=str)
pd.set_option("display.width", 250)
print(pd.DataFrame(res)[["mechanism", "n_raw", "n_independent_L1_user_coin_day",
                         "n_independent_L3_day", "gross_bps", "net_bps", "net_bps_stress28",
                         "t_stat_declustered_L3day", "bootstrap_ci95", "ex_best_year",
                         "train_bps", "test_bps", "event_rate_indep_per_week",
                         "eta_forward_confirmation_years",
                         "capacity_usd_estimate"]].to_string(index=False))
