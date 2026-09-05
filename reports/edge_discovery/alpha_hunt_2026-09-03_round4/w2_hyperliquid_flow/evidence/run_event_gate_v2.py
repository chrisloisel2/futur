#!/usr/bin/env python
"""W2 -- step 6 (final): the round-4 gate over the preregistered TWAP event mechanisms.

Supersedes run_event_gate.py.  Three things are added or fixed here:

 (1) capacity_usd_estimate (preregistration section 6): 0.5% of the Binance quote volume
     traded over the holding window, median over episodes, PIT (trailing 24h volume scaled
     to the horizon).
 (2) the t-7d PLACEBO control is promoted to first-class.  plc = same symbol, same clock
     time, same direction, 7 days earlier.  It measures the persistent symbol x direction
     drift that a beta=1 market-neutral adjustment does NOT remove.  signal - placebo is
     the honest event edge, and it is reported for every live candidate.
 (3) BUG FIX vs run_event_gate.py: "HLTWAP_ALL_h24h_momentum_residualised" reported
     gross = -0.00 bps.  That is an ARTEFACT: OLS residuals are mean-zero by construction,
     so the number carried no information.  Replaced by the regression coefficients
     (intercept = the edge net of trailing momentum, beta = the momentum loading) and by a
     within-quintile-of-trailing-momentum decomposition.

Re-executable: .venv/bin/python evidence/run_event_gate_v2.py
"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate, contrast

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
OUT = os.path.dirname(os.path.abspath(__file__))
d = pd.read_parquet(f"{SC}/events.parquet")
LAG = 15
for h in ["60", "240", "720", "1440", "DUR", "POST"]:
    d[f"S{h}"] = d["dir"]*d[f"mn{h}_lag{LAG}"]
for h in ["240", "1440", "DUR"]:
    d[f"P{h}"] = d["dir"]*d[f"plcm7_mn{h}"]
    d[f"Q{h}"] = d["dir"]*d[f"plcp7_mn{h}"]
    d[f"A{h}"] = d[f"S{h}"] - d[f"P{h}"]
    d[f"B{h}"] = d[f"S{h}"] - 0.5*(d[f"P{h}"]+d[f"Q{h}"])   # two-sided placebo (+/-7d)
for h in ["240", "1440"]:
    d[f"T{h}"] = d["dir"]*d[f"trail_mn{h}"]

HOR_MIN = {"S60": 60, "S240": 240, "S720": 720, "S1440": 1440, "SDUR": None, "SPOST": None,
           "A240": 240, "A1440": 1440, "ADUR": None, "B1440": 1440, "B240": 240}


def cap_usd(sub, col):
    """0.5% of Binance quote volume over the holding window (trailing-24h volume, PIT)."""
    hm = HOR_MIN.get(col)
    if hm is None:
        hm = float(np.nanmedian(sub.mins.clip(5, 4320)))
    v = sub.qvol24h.values*(hm/1440.0)*0.005
    v = v[np.isfinite(v) & (v > 0)]
    return int(np.median(v)) if len(v) else None


TRAIN, TEST = d.day < "2025-09-01", d.day >= "2025-09-01"
res, con = [], []
sr = d.size_ratio.values
thr_tr = np.nanquantile(d.loc[TRAIN, "size_ratio"], [0.90, 0.99])
ALL = d.index == d.index

MECHS = [
    ("T1 HLTWAP_ALL_h4h",              "S240",  ALL),
    ("T1 HLTWAP_ALL_h24h",             "S1440", ALL),
    ("T1 HLTWAP_ALL_hDUR",             "SDUR",  ALL),
    ("T1 HLTWAP_ALL_h4h_PLACEBOADJ",   "A240",  ALL),
    ("T1 HLTWAP_ALL_h24h_PLACEBOADJ",  "A1440", ALL),
    ("T1 HLTWAP_ALL_h24h_PLACEBOADJ2SIDED", "B1440", ALL),
    ("T1 HLTWAP_ALL_hDUR_PLACEBOADJ",  "ADUR",  ALL),
    ("T1 HLTWAP_BUYONLY_h24h",         "S1440", (d.side == "B").values),
    ("T1 HLTWAP_BUYONLY_h24h_PLACEBOADJ", "A1440", (d.side == "B").values),
    ("T5 HLTWAP_NONREDUCEONLY_h24h",   "S1440", (d.ro == "false").values),
    ("T5 HLTWAP_NONREDUCEONLY_h24h_PLACEBOADJ", "A1440", (d.ro == "false").values),
    ("T5 HLTWAP_REDUCEONLY_h24h",      "S1440", (d.ro == "true").values),
    ("T7 HLTWAP_DUR_GE180_h24h",       "S1440", (d.mins >= 180).values),
    ("T7 HLTWAP_DUR_LE15_h24h",        "S1440", (d.mins <= 15).values),
    ("T2 HLTWAP_POSTEND_REVERSION",    "SPOST", ALL),
    (f"T3 HLTWAP_SIZERATIO_TOP10PCT_h24h(thr={thr_tr[0]:.2e},TRAIN)", "S1440", sr >= thr_tr[0]),
    (f"T3 HLTWAP_SIZERATIO_TOP1PCT_h24h(thr={thr_tr[1]:.2e},TRAIN)",  "S1440", sr >= thr_tr[1]),
    ("T3 HLTWAP_SIZERATIO_TOP1PCT_h24h_PLACEBOADJ", "A1440", sr >= thr_tr[1]),
    ("T3 HLTWAP_NTL_GE1M_h24h",        "S1440", (d.ntl_planned >= 1e6).values),
]
for name, col, mask in MECHS:
    sub = d[np.asarray(mask)]
    r = gate(sub, col, name)
    r["train_bps"] = round(float(sub.loc[sub.day < "2025-09-01", col].mean()), 2)
    r["test_bps"] = round(float(sub.loc[sub.day >= "2025-09-01", col].mean()), 2)
    r["median_ntl_planned_usd"] = int(np.nanmedian(sub.ntl_planned))
    r["capacity_usd_estimate"] = cap_usd(sub, col)
    r["track"] = "A_binance_executed"
    res.append(r)

# ---- T6 informed-user cohort: users scored on TRAIN only, evaluated on TEST only
sc = d[TRAIN].groupby("usr")["S1440"].agg(["mean", "size"])
sc = sc[sc["size"] >= 20]
top = set(sc[sc["mean"] >= sc["mean"].quantile(0.80)].index)
bot = set(sc[sc["mean"] <= sc["mean"].quantile(0.20)].index)
te = d[TEST & d.usr.isin(sc.index)]
tet = te[te.usr.isin(top)]
for col, tag in (("S1440", ""), ("A1440", "_PLACEBOADJ"), ("B1440", "_PLACEBOADJ2SIDED")):
    r = gate(tet, col, f"T6 informed-user cohort (TRAIN-scored) @24h TEST-only{tag}",
             extra={"n_users_cohort": len(top), "n_users_scored": len(sc)})
    h1 = tet[tet.day < "2026-03-01"]; h2 = tet[tet.day >= "2026-03-01"]
    r["test_half1_bps"] = round(float(h1[col].mean()), 2)
    r["test_half2_bps"] = round(float(h2[col].mean()), 2)
    r["capacity_usd_estimate"] = cap_usd(tet, col)
    r["median_ntl_planned_usd"] = int(np.nanmedian(tet.ntl_planned))
    r["track"] = "A_binance_executed"
    res.append(r)

con.append(contrast(d, "S1440", (d.ro == "false"), (d.ro == "true"), "T5 nonRO - RO @24h"))
con.append(contrast(d, "A1440", (d.ro == "false"), (d.ro == "true"), "T5 nonRO - RO @24h PLACEBOADJ"))
con.append(contrast(d, "S1440", (d.mins <= 15), (d.mins >= 240), "T7 short - long TWAP @24h"))
con.append(contrast(d, "S1440", (d.rnd == "true"), (d.rnd == "false"), "randomize true - false @24h"))
con.append(contrast(d, "S1440", pd.Series(sr >= thr_tr[1], index=d.index),
                    pd.Series(sr < thr_tr[0], index=d.index), "T3 sizeRatio top1% - bottom90% @24h"))
con.append(contrast(te, "S1440", te.usr.isin(top), te.usr.isin(bot), "T6 top - bottom cohort @24h TEST"))
con.append(contrast(te, "A1440", te.usr.isin(top), te.usr.isin(bot),
                    "T6 top - bottom cohort @24h TEST PLACEBOADJ"))
# the decisive T1 contrast: event window vs the same symbol/direction 7 days earlier
con.append({"mechanism": "T1 event window - t-7d placebo (same symbol, same direction) @24h",
            "arm_a_bps": round(float(d.S1440.mean()), 2),
            "arm_b_bps": round(float(d.P1440.mean()), 2),
            "spread_bps": round(float(d.A1440.mean()), 2),
            "n_a": int(d.S1440.notna().sum()), "n_b": int(d.P1440.notna().sum()),
            "n_days_paired": int(d.day.nunique()),
            "spread_t_daypaired": round(float(gate(d, "A1440", "x")["t_stat_declustered_L3day"]), 2)})

# ---- momentum control, done correctly (the v1 "residualised" row was an OLS artefact)
ok = np.isfinite(d.S1440) & np.isfinite(d.T1440)
x = d.loc[ok, "T1440"].values; y = d.loc[ok, "S1440"].values
b = np.polyfit(x, y, 1)
qs = pd.qcut(d.loc[ok, "T1440"], 5, labels=False, duplicates="drop")
by_q = d.loc[ok].assign(q=qs).groupby("q")["S1440"].agg(["mean", "size"])
mom = {"note": ("run_event_gate.py reported gross=-0.00 bps for the residualised series; that "
                "is mean-zero BY CONSTRUCTION (OLS residual) and carried no information. "
                "The informative quantities are the intercept and the slope."),
       "intercept_bps": round(float(b[1]), 2), "beta_on_signed_trailing_24h": round(float(b[0]), 4),
       "edge_by_trailing_momentum_quintile_bps": {int(k): [round(float(v["mean"]), 2), int(v["size"])]
                                                  for k, v in by_q.iterrows()},
       "conclusion": ("The LINEAR beta is ~0, but the quintile decomposition is strongly "
                      "non-linear: the signed 24h drift goes from -3.5 bps in Q2 of signed "
                      "trailing momentum to +34.6 bps in Q5. So OLS residualisation is simply "
                      "the wrong control here (heavy tails drive beta to 0 while the "
                      "conditional means fan out). The decisive control is the t-7d placebo, "
                      "not momentum residualisation.")}

lagtab = {lg: round(float(np.nanmean(d["dir"].values*d[f"mn1440_lag{lg}"].values)), 2)
          for lg in [0, 5, 15, 30, 60]}
lagtab_adj = {}
for lg in [0, 5, 15, 30, 60]:
    v = d["dir"].values*d[f"mn1440_lag{lg}"].values - d.P1440.values
    lagtab_adj[lg] = round(float(np.nanmean(v)), 2)

json.dump({"mechanisms": res, "contrasts": con, "momentum_control": mom,
           "entry_lag_sensitivity_h24h_bps": lagtab,
           "entry_lag_sensitivity_h24h_PLACEBOADJ_bps": lagtab_adj},
          open(f"{OUT}/event_gate_results_v2.json", "w"), indent=1, default=str)
cols = ["mechanism", "n_raw", "n_independent_L1_user_coin_day", "n_independent_L3_day",
        "gross_bps", "net_bps", "net_bps_stress28", "t_stat_declustered_L3day",
        "bootstrap_ci95", "train_bps", "test_bps", "ex_best_year",
        "eta_forward_confirmation_years", "capacity_usd_estimate"]
pd.set_option("display.width", 250)
print(pd.DataFrame(res)[cols].to_string(index=False))
print("\nCONTRASTS\n", pd.DataFrame(con).to_string(index=False))
print("\nmomentum:", json.dumps(mom, indent=1))
print("\nlag sens raw:", lagtab, "\nlag sens placebo-adj:", lagtab_adj)
