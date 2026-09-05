#!/usr/bin/env python
"""W2 -- HLTWAP_COINQUIET_1WK_AGO, the only Track A population that survives its own control.

The placebo audit found that the ~10% of events whose t-7d control window is free of same-coin
TWAP flow carry a much larger raw edge than the full sample.  run_firsttouch_gate.py ruled out
the obvious explanation (it is NOT "first touch after a quiet period": QUIET_{24h,72h,7d}
measured just before t are all dead, t < 1).  What the subset actually selects is:

  the coin had NO known HL TWAP flow during [t-7d, t-6d), but has flow now
  -> an ATTENTION-ONSET population (HL metaorder flow arrived on this coin within the last week)

That condition reads only bars strictly in the past, so it is PIT-computable live.

DECLARED REFIT: not in PREREGISTRATION.md; found while auditing the placebo control.
Reported with a strict chronological train/test split and labelled REFIT throughout.

Re-executable: .venv/bin/python evidence/run_quietweekago_gate.py
"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
OUT = os.path.dirname(os.path.abspath(__file__))
LAGB, W7 = 3, 2016
d = pd.read_parquet(f"{SC}/events.parquet",
                    columns=["usr", "coin", "sym", "si", "ic", "day", "year", "side", "dir",
                             "mins", "ro", "ntl_planned", "qvol24h", "size_ratio",
                             "mn60_lag15", "mn240_lag15", "mn720_lag15", "mn1440_lag15",
                             "plcm7_mn240", "plcm7_mn1440"])
for h in ("60", "240", "720", "1440"):
    d[f"S{h}"] = d["dir"]*d[f"mn{h}_lag15"]
d["A1440"] = d.S1440 - d["dir"]*d.plcm7_mn1440
d["A240"] = d.S240 - d["dir"]*d.plcm7_mn240

dur = np.clip((d.mins.values*60000/300000).astype(np.int64), 1, 2016)
st = d.ic.values + LAGB; en = d.ic.values + 1 + dur; si = d.si.values
order = np.lexsort((st, si))
si_s, st_s, en_s = si[order], st[order], en[order]
bnd = np.r_[0, np.flatnonzero(np.diff(si_s)) + 1, len(si_s)]
sym_slice = {si_s[bnd[i]]: (bnd[i], bnd[i+1]) for i in range(len(bnd)-1)}

lo = d.ic.values - W7 + LAGB          # [t-7d, t-6d): strictly past, PIT
hi = lo + 288
quiet = np.zeros(len(d), bool)
for i in range(len(d)):
    a, b = sym_slice[si[i]]
    j0 = np.searchsorted(st_s[a:b], hi[i], side="left") + a
    quiet[i] = not bool((en_s[a:j0] > lo[i]).any())
    if i % 50000 == 0:
        print("  scan", i, flush=True)
d["quiet_wk_ago"] = quiet
sub = d[quiet]
print(f"trigger fires on {len(sub)}/{len(d)} = {len(sub)/len(d):.1%} of TWAPs")

HM = {"S60": 60, "S240": 240, "S720": 720, "S1440": 1440, "A240": 240, "A1440": 1440}
res = []
for col in ("S60", "S240", "S720", "S1440", "A240", "A1440"):
    g = gate(sub, col, f"HLTWAP_COINQUIET_1WK_AGO_{col} [REFIT]")
    g["train_bps"] = round(float(sub.loc[sub.day < "2025-09-01", col].mean()), 2)
    g["test_bps"] = round(float(sub.loc[sub.day >= "2025-09-01", col].mean()), 2)
    g["median_ntl_planned_usd"] = int(np.nanmedian(sub.ntl_planned))
    cap = sub.qvol24h.values*(HM[col]/1440.0)*0.005
    cap = cap[np.isfinite(cap) & (cap > 0)]
    g["capacity_usd_estimate"] = int(np.median(cap))
    g["trigger_share_of_all_twap"] = round(len(sub)/len(d), 4)
    g["refit"] = True; g["track"] = "A_binance_executed"
    res.append(g)
# control: the complementary population, same columns (arm A - arm B, briefing 1.3)
comp = d[~quiet]
for col in ("S240", "S1440"):
    da = sub.groupby("day")[col].mean(); db = comp.groupby("day")[col].mean()
    j = pd.concat([da, db], axis=1, join="inner"); j.columns = ["a", "b"]
    diff = (j.a-j.b).dropna().values
    res.append({"mechanism": f"CONTRAST quiet_wk_ago - rest_{col}",
                "arm_a_bps": round(float(sub[col].mean()), 2),
                "arm_b_bps": round(float(comp[col].mean()), 2),
                "spread_bps": round(float(sub[col].mean()-comp[col].mean()), 2),
                "n_days_paired": int(len(diff)),
                "spread_t_daypaired": round(float(diff.mean()/(diff.std(ddof=1)/np.sqrt(len(diff)))), 2)})
json.dump({"mechanisms": res, "trigger_n": int(len(sub)), "trigger_share": len(sub)/len(d)},
          open(f"{OUT}/quietweekago_gate_results.json", "w"), indent=1, default=str)
pd.set_option("display.width", 260)
g = pd.DataFrame([r for r in res if "gross_bps" in r])
print(g[["mechanism", "n_raw", "n_independent_L1_user_coin_day", "n_independent_L3_day",
         "gross_bps", "net_bps", "net_bps_stress28", "t_stat_declustered_L3day",
         "bootstrap_ci95", "year_by_year", "ex_best_year", "train_bps", "test_bps",
         "eta_forward_confirmation_years", "capacity_usd_estimate"]].to_string(index=False))
print(pd.DataFrame([r for r in res if "spread_bps" in r]).to_string(index=False))
