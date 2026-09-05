#!/usr/bin/env python
"""W2 -- audit of the t-7d placebo control, because the placebo is what kills every Track A
mechanism and a contaminated placebo would over-correct.

Risk: HL TWAP flow is autocorrelated.  If the same coin was already being TWAPed (in the same
direction) 7 days before the event, the "placebo" window contains signal, and
signal - placebo subtracts the effect from itself.

Measured here:
  contamination_rate  share of events whose t-7d placebo window overlaps other TWAP flow on
                      the SAME coin (any user) / the SAME user+coin
  clean-subset gate   the placebo-adjusted edge restricted to events whose placebo window is
                      free of same-coin TWAP flow -> an uncontaminated estimate

Re-executable: .venv/bin/python evidence/run_placebo_audit.py
"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
OUT = os.path.dirname(os.path.abspath(__file__))
d = pd.read_parquet(f"{SC}/events.parquet",
                    columns=["usr", "coin", "sym", "si", "ic", "day", "year", "side", "dir",
                             "mins", "ro", "ntl_planned", "qvol24h", "size_ratio",
                             "mn1440_lag15", "plcm7_mn1440", "plcp7_mn1440"])
d["S1440"] = d["dir"]*d["mn1440_lag15"]
d["P1440"] = d["dir"]*d["plcm7_mn1440"]
d["A1440"] = d.S1440 - d.P1440

BARS_7D = 7*288
# placebo window = [ic - 7d + 3, ic - 7d + 3 + 288)  (24h from the placebo entry bar)
lo = d.ic.values - BARS_7D + 3
hi = lo + 288
# --- is there any other TWAP on the same coin whose [ic, ic+dur] intersects that window?
dur = np.clip((d.mins.values*60000/300000).astype(np.int64), 1, 2016)
st = d.ic.values; en = d.ic.values + 1 + dur
order = np.lexsort((st, d.si.values))
si_s, st_s, en_s = d.si.values[order], st[order], en[order]
usr_s = d.usr.values[order]

# per-symbol sorted scan
res_any = np.zeros(len(d), bool); res_usr = np.zeros(len(d), bool)
bnd = np.r_[0, np.flatnonzero(np.diff(si_s)) + 1, len(si_s)]
sym_slice = {si_s[bnd[i]]: (bnd[i], bnd[i+1]) for i in range(len(bnd)-1)}
usr_v = d.usr.values
for i in range(len(d)):
    sl = sym_slice.get(d.si.values[i])
    if sl is None:
        continue
    a, b = sl
    j0 = np.searchsorted(st_s[a:b], hi[i], side="left") + a         # starts before window end
    seg = slice(a, j0)
    m = en_s[seg] > lo[i]                                            # ends after window start
    if m.any():
        res_any[i] = True
        res_usr[i] = bool((usr_s[seg][m] == usr_v[i]).any())
    if i % 50000 == 0:
        print("  scan", i, flush=True)

d["plc_contaminated_anyuser"] = res_any
d["plc_contaminated_sameuser"] = res_usr
rep = {
  "n_events": int(len(d)),
  "contamination_rate_same_coin_any_user": round(float(res_any.mean()), 4),
  "contamination_rate_same_coin_same_user": round(float(res_usr.mean()), 4),
}
clean = d[~d.plc_contaminated_anyuser]
rep["n_events_clean_placebo"] = int(len(clean))
out = []
for nm, sub in (("ALL events", d), ("CLEAN placebo window only", clean)):
    for col in ("S1440", "P1440", "A1440"):
        g = gate(sub, col, f"{nm} :: {col}")
        out.append({"pop": nm, "col": col, "gross_bps": g["gross_bps"],
                    "t_L3": g["t_stat_declustered_L3day"], "ci95": g["bootstrap_ci95"],
                    "n_raw": g["n_raw"], "n_L3": g["n_independent_L3_day"]})
rep["gates"] = out
d[["usr", "coin", "day", "ic", "si",
   "plc_contaminated_anyuser", "plc_contaminated_sameuser"]].to_parquet(
       f"{SC}/plc_flags.parquet", index=False)

# ---- full round-4 gate on the UNCONTAMINATED subset.
# DECLARED REFIT: this subset was defined AFTER seeing that the all-events placebo was
# contaminated.  It is therefore reported with an explicit train/test split and labelled
# REFIT; it is not a preregistered mechanism.
full = []
for col, nm in (("S1440", "T1 HLTWAP_CLEANPLACEBOSUBSET_h24h_raw [REFIT]"),
                ("A1440", "T1 HLTWAP_CLEANPLACEBOSUBSET_h24h_PLACEBOADJ [REFIT]")):
    g = gate(clean, col, nm)
    g["train_bps"] = round(float(clean.loc[clean.day < "2025-09-01", col].mean()), 2)
    g["test_bps"] = round(float(clean.loc[clean.day >= "2025-09-01", col].mean()), 2)
    g["median_ntl_planned_usd"] = int(np.nanmedian(clean.ntl_planned))
    cap = clean.qvol24h.values*0.005
    cap = cap[np.isfinite(cap) & (cap > 0)]
    g["capacity_usd_estimate"] = int(np.median(cap))
    g["refit"] = True
    g["track"] = "A_binance_executed"
    full.append(g)
rep["clean_subset_full_gate"] = full
json.dump(rep, open(f"{OUT}/placebo_audit.json", "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in rep.items() if k != "gates"}, indent=1))
print(pd.DataFrame(full)[["mechanism", "n_raw", "n_independent_L1_user_coin_day",
      "n_independent_L3_day", "gross_bps", "net_bps", "net_bps_stress28",
      "t_stat_declustered_L3day", "bootstrap_ci95", "year_by_year", "ex_best_year",
      "train_bps", "test_bps", "eta_forward_confirmation_years",
      "capacity_usd_estimate"]].to_string(index=False))
