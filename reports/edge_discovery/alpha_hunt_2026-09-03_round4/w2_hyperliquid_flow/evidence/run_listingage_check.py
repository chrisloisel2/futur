#!/usr/bin/env python
"""W2 -- listing-age control on HLTWAP_COINQUIET_1WK_AGO.

Briefing s8.10 lists listing age as a known trap (the project's own ListingAgeGate uses 30
days). The COINQUIET trigger selects coins with no HL TWAP flow a week earlier, which is
exactly what a *recently listed* coin looks like -- so the edge could be a listing artefact
rather than an attention-onset effect.

Age is measured directly from the Binance 5-min panel: bars since the symbol's first finite
close. No external listing file needed, and it is the age that actually matters for execution.

Re-executable: .venv/bin/python evidence/run_listingage_check.py
"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
OUT = os.path.dirname(os.path.abspath(__file__))
Z = np.load(f"{SC}/panel.npz", allow_pickle=True)
CLOSE = Z["CLOSE"]; NB = int(Z["nb"])
first_bar = np.array([int(np.argmax(np.isfinite(CLOSE[i]))) if np.isfinite(CLOSE[i]).any() else NB
                      for i in range(CLOSE.shape[0])])

LAGB, W7 = 3, 2016
d = pd.read_parquet(f"{SC}/events.parquet",
                    columns=["usr", "coin", "sym", "si", "ic", "day", "year", "dir", "mins",
                             "ntl_planned", "qvol24h", "mn1440_lag15"])
d["S1440"] = d["dir"]*d["mn1440_lag15"]
d["age_days"] = (d.ic.values - first_bar[d.si.values])/288.0

dur = np.clip((d.mins.values*60000/300000).astype(np.int64), 1, 2016)
st = d.ic.values + LAGB; en = d.ic.values + 1 + dur; si = d.si.values
order = np.lexsort((st, si)); si_s, st_s, en_s = si[order], st[order], en[order]
bnd = np.r_[0, np.flatnonzero(np.diff(si_s)) + 1, len(si_s)]
sym_slice = {si_s[bnd[i]]: (bnd[i], bnd[i+1]) for i in range(len(bnd)-1)}
lo = d.ic.values - W7 + LAGB; hi = lo + 288
quiet = np.zeros(len(d), bool)
for i in range(len(d)):
    a, b = sym_slice[si[i]]
    j0 = np.searchsorted(st_s[a:b], hi[i], side="left") + a
    quiet[i] = not bool((en_s[a:j0] > lo[i]).any())
sub = d[quiet].copy()

rep = {"trigger_n": int(len(sub)),
       "age_days_quantiles": {str(q): round(float(sub.age_days.quantile(q)), 1)
                              for q in (0.01, 0.05, 0.10, 0.25, 0.50)},
       "share_age_lt_30d": round(float((sub.age_days < 30).mean()), 4),
       "share_age_lt_90d": round(float((sub.age_days < 90).mean()), 4),
       "baseline_share_age_lt_30d_all_twap": round(float((d.age_days < 30).mean()), 4)}
res = []
for name, m in (("all trigger events", np.ones(len(sub), bool)),
                ("age >= 30d (ListingAgeGate)", (sub.age_days >= 30).values),
                ("age >= 90d", (sub.age_days >= 90).values),
                ("age >= 180d", (sub.age_days >= 180).values)):
    s2 = sub[m]
    g = gate(s2, "S1440", f"COINQUIET_1WK_AGO_S1440 :: {name}")
    g["train_bps"] = round(float(s2.loc[s2.day < "2025-09-01", "S1440"].mean()), 2)
    g["test_bps"] = round(float(s2.loc[s2.day >= "2025-09-01", "S1440"].mean()), 2)
    g["median_age_days"] = round(float(s2.age_days.median()), 1)
    res.append(g)
rep["gates"] = res
json.dump(rep, open(f"{OUT}/listingage_check.json", "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in rep.items() if k != "gates"}, indent=1))
pd.set_option("display.width", 220)
print(pd.DataFrame(res)[["mechanism", "n_raw", "n_independent_L3_day", "median_age_days",
                         "gross_bps", "net_bps", "net_bps_stress28",
                         "t_stat_declustered_L3day", "bootstrap_ci95", "train_bps", "test_bps",
                         "eta_forward_confirmation_years"]].to_string(index=False))
