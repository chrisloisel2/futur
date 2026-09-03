"""Family B, depth pass.

Three things must be settled before the session-clock result means anything:
 1. The EU->US arm came out with the OPPOSITE sign to the pre-committed reversion
    hypothesis. Amendment 1 forbids re-labelling that as a discovery without a disjoint
    period. => TRAIN 2020-2023 fixes the sign per boundary, TEST 2024-2026 measures it.
 2. Cross-sectional reversal at a boundary is the classic bid-ask-bounce artifact. => a GAP
    variant enters 1h after the boundary instead of 5m after. Bounce dies, real flow lives.
 3. Is the clock actually doing anything, or is this the known reversal in a session hat?
    => formal ARM-vs-ARM difference on paired days with a week-block bootstrap.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread
from gate import run_gate, auto_verdict, family_maxt, block_bootstrap_ci
OUT = os.path.dirname(os.path.abspath(__file__))
RNG = np.random.default_rng(11)

c = con()
h = c.execute(f"""SELECT symbol, hour_end, close_at_hour_end, close_first5, dv_usd,
                         resid_logret_hour, n_bars
                  FROM read_parquet('{SCRATCH}/hourly.parquet')
                  WHERE close_at_hour_end IS NOT NULL AND n_bars >= 10""").df()
h["hour_end"] = pd.to_datetime(h["hour_end"], utc=True)
h["d"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.floor("D")
h["hb"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.hour
h = h.merge(eligibility(), on=["symbol", "d"], how="left")
h = h[h["eligible"].fillna(False)]

px = h[["symbol", "hour_end", "close_at_hour_end", "close_first5"]].copy()
px_at    = px[["symbol", "hour_end", "close_at_hour_end"]].rename(columns={"hour_end": "T", "close_at_hour_end": "p"})
px_entry = px[["symbol", "hour_end", "close_first5"]].copy()
px_entry["T"] = px_entry["hour_end"] - pd.Timedelta(hours=1)     # price at T+5m
px_entry = px_entry[["symbol", "T", "close_first5"]].rename(columns={"close_first5": "p5"})

SESS = [("ASIA", 0, 7), ("EU", 7, 13), ("US", 13, 21), ("LATE", 21, 24)]
h["sess"] = h["hb"].map(lambda x: next((n for n, a, b in SESS if a <= x < b), None))
need = {"ASIA": 7, "EU": 6, "US": 8, "LATE": 3}
sa = h.dropna(subset=["sess"]).groupby(["symbol", "d", "sess"], sort=False).agg(
    resid=("resid_logret_hour", "sum"), dv=("dv_usd", "sum"), nh=("hour_end", "size")).reset_index()
sa = sa[sa.apply(lambda r: r["nh"] >= need[r["sess"]], axis=1)]
SEND = {"ASIA": 7, "EU": 13, "US": 21, "LATE": 24}
NEXT = {"ASIA": "EU", "EU": "US", "US": "LATE", "LATE": "ASIA"}
NEND = {"ASIA": 13, "EU": 21, "US": 24, "LATE": 31}
sa["B_end"]  = sa["d"] + pd.to_timedelta(sa["sess"].map(SEND), unit="h")
sa["B_next"] = sa["d"] + pd.to_timedelta(sa["sess"].map(NEND), unit="h")

def build(entry_lag_h):
    """entry_lag_h = 0 -> enter at boundary+5m ; = 1 -> enter 1h after the boundary (gap)."""
    d = sa.copy()
    d["T_entry"] = d["B_end"] + pd.Timedelta(hours=entry_lag_h)
    if entry_lag_h == 0:
        d = d.merge(px_entry.rename(columns={"T": "T_entry", "p5": "p_entry"}), on=["symbol", "T_entry"], how="inner")
    else:
        d = d.merge(px_at.rename(columns={"T": "T_entry", "p": "p_entry"}), on=["symbol", "T_entry"], how="inner")
    d = d.merge(px_at.rename(columns={"T": "B_next", "p": "p_exit"}), on=["symbol", "B_next"], how="inner")
    d = d[(d["p_entry"] > 0) & (d["p_exit"] > 0)]
    d["ret_next"] = np.log(d["p_exit"] / d["p_entry"])
    return d

d0 = build(0)      # entry boundary + 5m
d1 = build(1)      # entry boundary + 1h  (bid-ask-bounce control)
print("rows", len(d0), len(d1))

TRAIN_END = pd.Timestamp("2024-01-01", tz="UTC")
results, arm_series = [], {}

def spread(sub, nb=5, minxs=20):
    sp, n1 = xs_spread(sub, "B_end", "resid", ["ret_next"], n_buckets=nb, min_xs=minxs)
    return sp, n1

# ---------- per-arm, per-entry-lag, TRAIN sign then TEST measurement -----------------
summary = []
for lag, dd in ((0, d0), (1, d1)):
    for s0 in ["ASIA", "EU", "US", "LATE"]:
        sub = dd[dd["sess"] == s0]
        sp, n1 = spread(sub)
        if len(sp) == 0: continue
        tr = sp[sp["B_end"] < TRAIN_END]; te = sp[sp["B_end"] >= TRAIN_END]
        if len(tr) < 100 or len(te) < 100: continue
        sign = np.sign(tr["ret_next_spread"].mean()) or 1.0
        o_full = sp[["B_end", "ret_next_spread"]].rename(columns={"B_end": "ts", "ret_next_spread": "ret_bps"})
        o_te = te[["B_end", "ret_next_spread"]].copy()
        o_te["ret_bps"] = o_te["ret_next_spread"] * sign
        o_te = o_te[["B_end", "ret_bps"]].rename(columns={"B_end": "ts"})
        nm = f"B2_{s0}_to_{NEXT[s0]}_lag{lag}h"
        r_full = run_gate(o_full, nm + "_FULL", "reversion => spread>0 (sign as observed)", n_ind_L1=n1, cost_legs=2, n_boot=1500)
        r_te = run_gate(o_te, nm + "_TEST_signfrozen",
                        f"sign {int(sign):+d} frozen on TRAIN 2020-2023, measured on TEST 2024-2026",
                        n_ind_L1=n1, cost_legs=2, n_boot=1500,
                        extra={"train_gross_bps": round(float(tr['ret_next_spread'].mean()), 2),
                               "train_sign": int(sign), "n_train_events": int(len(tr))})
        results += [r_full, r_te]
        if lag == 0:
            arm_series[s0] = sp.set_index("B_end")["ret_next_spread"]
        summary.append(dict(arm=s0, lag=lag, full_bps=r_full["gross_bps"], full_t=r_full["t_stat_declustered"],
                            train_bps=round(float(tr["ret_next_spread"].mean()), 2),
                            test_signed_bps=r_te["gross_bps"], test_t=r_te["t_stat_declustered"]))

print("\n--- TRAIN/TEST sign stability + bid-ask-bounce control (lag1h) ---")
print(pd.DataFrame(summary).to_string(index=False))

# ---------- ARM vs ARM (the actual clock claim), paired on days ----------------------
def paired_diff(a, b, na, nb_):
    j = pd.concat([arm_series[a].rename("a"), arm_series[b].rename("b")], axis=1).dropna()
    j.index = pd.to_datetime(j.index, utc=True)
    dayv = (j["a"] - j["b"]).groupby(j.index.floor("D")).mean()
    v = dayv.to_numpy()
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    ci, _ = block_bootstrap_ci(v, n_boot=4000)
    return dict(comparison=f"{na} minus {nb_}", diff_bps=round(float(v.mean()), 2),
                n_days=int(len(v)), t=round(float(t), 3), ci95=[round(ci[0], 2), round(ci[1], 2)])

arm_cmp = [paired_diff("LATE", "EU", "LATE->ASIA", "EU->US"),
           paired_diff("LATE", "US", "LATE->ASIA", "US->LATE"),
           paired_diff("ASIA", "EU", "ASIA->EU", "EU->US")]
print("\n--- ARM vs ARM (paired on the same calendar days; this is the clock claim) ---")
print(pd.DataFrame(arm_cmp).to_string(index=False))

# ---------- concentration + conditioning on the strongest arm ------------------------
late = d0[d0["sess"] == "LATE"]
for nb in (5, 10, 20):
    sp, n1 = spread(late, nb=nb)
    o = sp[["B_end", "ret_next_spread"]].rename(columns={"B_end": "ts", "ret_next_spread": "ret_bps"})
    results.append(run_gate(o, f"B2_LATE_to_ASIA_q{nb}", "concentration sweep", n_ind_L1=n1, cost_legs=2, n_boot=1500))
q = late.groupby("B_end")["dv"].transform(lambda s: s.rank(pct=True))
for tag, mask in (("LIQ_LOW", q <= 0.33), ("LIQ_HIGH", q >= 0.67)):
    sp, n1 = spread(late[mask], nb=5, minxs=15)
    if len(sp) < 100: continue
    o = sp[["B_end", "ret_next_spread"]].rename(columns={"B_end": "ts", "ret_next_spread": "ret_bps"})
    results.append(run_gate(o, f"B2_LATE_to_ASIA_{tag}", "liquidity conditioning", n_ind_L1=n1, cost_legs=2, n_boot=1500))

crit = family_maxt(results, n_boot=800)
print("\nFamily B depth max-|t| crit:", round(crit, 3), "over", len(results), "cells")
for r in results:
    v, why = auto_verdict(r, family_maxt_crit=crit)
    r["verdict"], r["verdict_reason"], r["family_maxt_crit"] = v, why, round(crit, 3)
    r.pop("day_series", None)
json.dump({"mechanisms": results, "arm_vs_arm": arm_cmp, "train_test_summary": summary},
          open(f"{OUT}/results_family_b_depth.json", "w"), indent=1, default=str)

cols = ["mechanism","n_independent_L2","gross_bps","net_bps_2leg","t_stat_declustered",
        "IR_day","ex_best_year_gross_bps","eta_forward_confirmation_years","verdict"]
pd.set_option("display.width", 240); pd.set_option("display.max_rows", 100)
print(pd.DataFrame(results)[cols].to_string(index=False))
