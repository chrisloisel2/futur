#!/usr/bin/env python
"""W2 — step 4: run the round-4 gate over the preregistered TWAP event mechanisms."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate, contrast

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
d = pd.read_parquet(f"{SC}/events.parquet")
LAG = 15
for h in ["60", "240", "720", "1440", "DUR", "POST"]:
    d[f"S{h}"] = d["dir"]*d[f"mn{h}_lag{LAG}"]
    d[f"R{h}"] = d["dir"]*d[f"r{h}_lag{LAG}"]
for h in ["240", "1440", "DUR"]:
    d[f"P{h}"] = d["dir"]*d[f"plcm7_mn{h}"]
    d[f"A{h}"] = d[f"S{h}"] - d[f"P{h}"]          # placebo-adjusted (t-7d control variate)
for h in ["240", "1440"]:
    d[f"T{h}"] = d["dir"]*d[f"trail_mn{h}"]        # signed trailing momentum at event time

TRAIN, TEST = d.day < "2025-09-01", d.day >= "2025-09-01"
res, con = [], []

MECHS = [
    ("HLTWAP_ALL_h4h",        "S240",  d.index == d.index),
    ("HLTWAP_ALL_h24h",       "S1440", d.index == d.index),
    ("HLTWAP_ALL_hDUR",       "SDUR",  d.index == d.index),
    ("HLTWAP_ALL_h4h_plcadj", "A240",  d.index == d.index),
    ("HLTWAP_ALL_h24h_plcadj","A1440", d.index == d.index),
    ("HLTWAP_BUYONLY_h24h",   "S1440", (d.side == "B").values),
    ("HLTWAP_NONREDUCEONLY_h24h", "S1440", (d.ro == "false").values),
    ("HLTWAP_REDUCEONLY_h24h",    "S1440", (d.ro == "true").values),
    ("HLTWAP_DUR_GE180_h24h", "S1440", (d.mins >= 180).values),
    ("HLTWAP_DUR_LE15_h24h",  "S1440", (d.mins <= 15).values),
    ("HLTWAP_POSTEND_REVERSION", "SPOST", d.index == d.index),
]
sr = d.size_ratio.values
thr_tr = np.nanquantile(d.loc[TRAIN, "size_ratio"], [0.90, 0.99])   # thresholds from TRAIN only
MECHS += [
    (f"HLTWAP_SIZERATIO_TOP10PCT_h24h(thr={thr_tr[0]:.2e},TRAINset)", "S1440", sr >= thr_tr[0]),
    (f"HLTWAP_SIZERATIO_TOP1PCT_h24h(thr={thr_tr[1]:.2e},TRAINset)", "S1440", sr >= thr_tr[1]),
    (f"HLTWAP_SIZERATIO_TOP1PCT_h4h", "S240", sr >= thr_tr[1]),
    ("HLTWAP_NTL_GE1M_h24h", "S1440", (d.ntl_planned >= 1e6).values),
]

for name, col, mask in MECHS:
    m = np.asarray(mask)
    sub = d[m]
    r = gate(sub, col, name)
    r["train_bps"] = round(float(sub.loc[sub.day < "2025-09-01", col].mean()), 2)
    r["test_bps"] = round(float(sub.loc[sub.day >= "2025-09-01", col].mean()), 2)
    r["median_ntl_planned_usd"] = int(np.nanmedian(sub.ntl_planned))
    res.append(r)

# ---- arm contrasts (briefing 1.3): never "arm A > 0", always A - B on the same population
con.append(contrast(d, "S1440", (d.ro == "false"), (d.ro == "true"), "T5 nonReduceOnly - reduceOnly @24h"))
con.append(contrast(d, "S1440", (d.mins <= 15), (d.mins >= 240), "T7 short TWAP - long TWAP @24h"))
con.append(contrast(d, "S1440", (d.rnd == "true"), (d.rnd == "false"), "randomize=true - false @24h"))
con.append(contrast(d, "S1440", pd.Series(sr >= thr_tr[1], index=d.index),
                    pd.Series(sr < thr_tr[0], index=d.index), "T3 sizeRatio top1% - bottom90% @24h"))

# ---- T6 informed-user cohort: score users on TRAIN, evaluate on TEST (strict chronology)
sc = d[TRAIN].groupby("usr")["S1440"].agg(["mean", "size"])
sc = sc[sc["size"] >= 20]
top = set(sc[sc["mean"] >= sc["mean"].quantile(0.80)].index)
bot = set(sc[sc["mean"] <= sc["mean"].quantile(0.20)].index)
te = d[TEST & d.usr.isin(sc.index)]
res.append(gate(te[te.usr.isin(top)], "S1440", "T6 informed-user cohort (TRAIN-scored) @24h TEST-only",
                extra={"n_users_cohort": len(top), "n_users_scored": len(sc)}))
con.append(contrast(te, "S1440", te.usr.isin(top), te.usr.isin(bot),
                    "T6 top-user - bottom-user cohort @24h (TEST only)"))

# ---- momentum control: does the event add anything on top of the coin's own trailing drift?
ok = np.isfinite(d.S1440) & np.isfinite(d.T1440)
x = d.loc[ok, "T1440"].values; y = d.loc[ok, "S1440"].values
b = np.polyfit(x, y, 1)
resid = y - np.polyval(b, x)
dm = d.loc[ok].assign(RESID=resid)
res.append(gate(dm, "RESID", "HLTWAP_ALL_h24h_momentum_residualised",
                extra={"beta_on_trailing_signed_24h": round(float(b[0]), 4),
                       "intercept_bps": round(float(b[1]), 2)}))

# ---- entry-lag sensitivity on the headline construction
lagtab = {}
for lg in [0, 5, 15, 30, 60]:
    v = d["dir"].values*d[f"mn1440_lag{lg}"].values
    lagtab[lg] = round(float(np.nanmean(v)), 2)

json.dump({"mechanisms": res, "contrasts": con, "entry_lag_sensitivity_h24h_bps": lagtab},
          open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "event_gate_results.json"), "w"),
          indent=1, default=str)
print(pd.DataFrame(res)[["mechanism", "n_raw", "n_independent_L3_day", "gross_bps", "net_bps",
                         "net_bps_stress28", "t_stat_declustered_L3day", "bootstrap_ci95",
                         "train_bps", "test_bps", "ex_best_year",
                         "eta_forward_confirmation_years"]].to_string(index=False))
print("\nCONTRASTS"); print(pd.DataFrame(con).to_string(index=False))
print("\nentry-lag sensitivity (signed mn bps @24h):", lagtab)
