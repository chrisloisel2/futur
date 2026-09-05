#!/usr/bin/env python
"""W2 -- robustness on HLTWAP_COINQUIET_1WK_AGO, the only Track A population left standing.

The briefing's decisive field is eta_forward_confirmation.  Here the L3 episode rate is already
saturated (an event every calendar day, 7/week), so the ONLY lever on the ETA is the day-level
dispersion.  Three daily aggregation schemes are compared, all PIT:

  event_weighted   the gate default: daily mean weighted by the number of events that day
  coin_equal       each coin contributes once per day, then equal-weight across coins
  coin_capped_5    coin_equal, capped at the 5 largest-notional coins of the day

Plus: per-coin concentration (ex-best-coin), entry-lag sensitivity, and a liquidity-tercile
split (does the edge only live in the illiquid tail?).

Re-executable: .venv/bin/python evidence/run_coinquiet_robustness.py
"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate

SC = os.environ.get("W2_SCRATCH",
     "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w2")
OUT = os.path.dirname(os.path.abspath(__file__))
LAGB, W7 = 3, 2016
cols = ["usr", "coin", "sym", "si", "ic", "day", "year", "side", "dir", "mins", "ro",
        "ntl_planned", "qvol24h"] + [f"mn1440_lag{l}" for l in (0, 5, 15, 30, 60)]
d = pd.read_parquet(f"{SC}/events.parquet", columns=cols)
for l in (0, 5, 15, 30, 60):
    d[f"S{l}"] = d["dir"]*d[f"mn1440_lag{l}"]

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
COL = "S15"
print("trigger n =", len(sub))

rep = {"trigger": "HLTWAP_COINQUIET_1WK_AGO", "col": COL, "n_events": int(len(sub))}


def eta_from_daily(series_by_day, edge_bps):
    s = series_by_day.dropna()
    sd = float(s.std(ddof=1)); mu = float(s.mean())
    n_req = (1.96+0.84)**2 * sd**2 / (0.5*edge_bps)**2
    rate = 7.0                              # L3 rate is saturated: >=1 episode every day
    return {"daily_mean_bps": round(mu, 2), "daily_sd_bps": round(sd, 1),
            "n_required_days": int(n_req), "eta_years": round(n_req/rate*7/365.25, 2),
            "n_days_observed": int(len(s))}


g = sub.groupby("day")[COL]
ew = (g.sum()/g.size())
ce = sub.groupby(["day", "coin"])[COL].mean().groupby("day").mean()
top5 = (sub.assign(_n=sub.ntl_planned).sort_values("_n", ascending=False)
        .groupby(["day", "coin"], as_index=False).first()
        .sort_values("_n", ascending=False).groupby("day").head(5)
        .groupby("day")[COL].mean())
edge = float(sub[COL].mean())
rep["eta_by_daily_aggregation"] = {
    "event_weighted": eta_from_daily(ew, edge),
    "coin_equal": eta_from_daily(ce, float(ce.mean())),
    "coin_capped_5": eta_from_daily(top5, float(top5.mean())),
}
rep["eta_target_note"] = ("briefing threshold is 3 years; a scheme reaches it only if "
                          "n_required_days < 1096")

# ---- per-coin concentration
pc = sub.groupby("coin")[COL].agg(["mean", "size"]).sort_values("size", ascending=False)
best = pc[pc["size"] >= 100]["mean"].idxmax()
rep["top_coins_by_n"] = {str(k): [round(float(v["mean"]), 2), int(v["size"])]
                         for k, v in pc.head(12).iterrows()}
rep["ex_best_coin"] = {"coin_dropped": str(best),
                       "edge_bps": round(float(sub[sub.coin != best][COL].mean()), 2)}
n_coins = int(sub.coin.nunique())
rep["n_coins"] = n_coins
rep["top1_coin_share_of_events"] = round(float(pc["size"].iloc[0]/len(sub)), 4)

# ---- entry-lag sensitivity
rep["entry_lag_sensitivity_bps"] = {l: round(float(sub[f"S{l}"].mean()), 2)
                                    for l in (0, 5, 15, 30, 60)}

# ---- liquidity terciles (is it only the illiquid tail?)
q = sub.qvol24h.quantile([1/3, 2/3]).values
lab = np.where(sub.qvol24h <= q[0], "low", np.where(sub.qvol24h <= q[1], "mid", "high"))
sub["liq"] = lab
rep["by_liquidity_tercile"] = {}
for k, gg in sub.groupby("liq"):
    gt = gate(gg, COL, f"COINQUIET_{k}")
    rep["by_liquidity_tercile"][k] = {
        "gross_bps": gt["gross_bps"], "net_bps": gt["net_bps"],
        "net_bps_stress28": gt["net_bps_stress28"],
        "t_L3": gt["t_stat_declustered_L3day"], "n_raw": gt["n_raw"],
        "median_qvol24h_usd": int(np.nanmedian(gg.qvol24h)),
        "capacity_usd_estimate": int(np.median(gg.qvol24h*0.005)),
        "eta_years": gt["eta_forward_confirmation_years"]}

json.dump(rep, open(f"{OUT}/coinquiet_robustness.json", "w"), indent=1, default=str)
print(json.dumps(rep, indent=1))
