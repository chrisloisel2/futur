"""Family B, FINAL pass — corrected arm-vs-arm alignment + full §2 gate on the survivor.

Two things are fixed here relative to family_b_depth.py:

1. ARM-vs-ARM WAS BROKEN. `arm_series[s0] = sp.set_index("B_end")[...]` indexes each arm by
   its own boundary instant (07:00 / 13:00 / 21:00 / 00:00). Concatenating those on axis=1
   and calling dropna() leaves ZERO overlapping rows, which is why every comparison in
   results_family_b_depth.json reads `n_days: 0, diff_bps: NaN`. The clock claim was never
   actually tested. Fixed by indexing every arm on its ORIGINATING calendar day (the day
   whose session produced the signal; the LATE arm's boundary is d+24h, so its floor is d+1
   and must be shifted back).

2. ARMS WERE NOT HORIZON-MATCHED. The four session transitions hold for 6h / 8h / 3h / 7h.
   Comparing their raw bps confounds the clock with the holding period. Two corrections:
   a per-hour normalisation reported alongside every contrast, and — decisively — the
   horizon-matched contrast lives in clock_map_v2.py, where all 24 entry hours use an
   identical 6h signal and 8h hold.

Then the surviving arm (EU->US continuation) is pushed to the complete briefing §2 gate:
entry-gap sweep (bid-ask-bounce control), concentration sweep, liquidity conditioning,
era split, sign frozen on TRAIN 2020-2023 and measured on TEST 2024-2026, winsorised
robustness, placebo, and eta_forward_confirmation.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread
from gate import run_gate, auto_verdict, family_maxt, block_bootstrap_ci
from clock_lib import load_hourly
OUT = os.path.dirname(os.path.abspath(__file__))
RNG = np.random.default_rng(917)

h, px_at, ent = load_hourly(SCRATCH, con, eligibility)
SESS = [("ASIA", 0, 7), ("EU", 7, 13), ("US", 13, 21), ("LATE", 21, 24)]
h["sess"] = h["hb"].map(lambda x: next((n for n, a, b in SESS if a <= x < b), None))
need = {"ASIA": 7, "EU": 6, "US": 8, "LATE": 3}
SEND = {"ASIA": 7, "EU": 13, "US": 21, "LATE": 24}
NEXT = {"ASIA": "EU", "EU": "US", "US": "LATE", "LATE": "ASIA"}
NEND = {"ASIA": 13, "EU": 21, "US": 24, "LATE": 31}
HOLD_H = {"ASIA": 6, "EU": 8, "US": 3, "LATE": 7}       # hours held by each transition

sa = h.dropna(subset=["sess"]).groupby(["symbol", "d", "sess"], sort=False).agg(
    resid=("resid_logret_hour", "sum"), dv=("dv_usd", "sum"), nh=("hour_end", "size")).reset_index()
sa = sa[sa.apply(lambda r: r["nh"] >= need[r["sess"]], axis=1)]
sa["B_end"] = sa["d"] + pd.to_timedelta(sa["sess"].map(SEND), unit="h")
sa["B_next"] = sa["d"] + pd.to_timedelta(sa["sess"].map(NEND), unit="h")
print("session aggregates", len(sa), sa.groupby("sess").size().to_dict())


def build(entry_lag_h):
    """entry_lag_h=0 -> enter at boundary+5m ; >0 -> enter that many hours after the
    boundary. Exit is ALWAYS the next boundary, so a larger gap shortens the hold: a
    bid-ask-bounce artifact must SHRINK with the gap, a real flow effect need not."""
    d = sa.copy()
    d["T_entry"] = d["B_end"] + pd.Timedelta(hours=entry_lag_h)
    if entry_lag_h == 0:
        d = d.merge(ent.rename(columns={"T": "T_entry", "p5": "p_entry"}), on=["symbol", "T_entry"], how="inner")
    else:
        d = d.merge(px_at.rename(columns={"T": "T_entry", "p": "p_entry"}), on=["symbol", "T_entry"], how="inner")
    d = d.merge(px_at.rename(columns={"T": "B_next", "p": "p_exit"}), on=["symbol", "B_next"], how="inner")
    d = d[(d["p_entry"] > 0) & (d["p_exit"] > 0)].copy()
    d["ret_next"] = np.log(d["p_exit"] / d["p_entry"])
    return d


GAPS = [0, 1, 2, 3]
D = {g: build(g) for g in GAPS}
print("rows per gap", {g: len(v) for g, v in D.items()})

TRAIN_END = pd.Timestamp("2024-01-01", tz="UTC")
results, arm_series, summary = [], {}, []


def spread_of(sub, rank="resid", nb=5, minxs=20, winsor=None):
    sp, n1 = xs_spread(sub, "B_end", rank, ["ret_next"], n_buckets=nb, min_xs=minxs, winsor=winsor)
    return sp, n1


def gated(sp, name, hypo, n1, extra=None, nboot=1500):
    o = sp[["B_end", "ret_next_spread"]].rename(columns={"B_end": "ts", "ret_next_spread": "ret_bps"})
    r = run_gate(o, name, hypo, n_ind_L1=n1, cost_legs=2, n_boot=nboot, extra=extra)
    results.append(r)
    return r


# ================= 1. gap sweep x arm, with TRAIN-frozen sign on TEST ==================
for g in GAPS:
    for s0 in ["ASIA", "EU", "US", "LATE"]:
        sub = D[g][D[g]["sess"] == s0]
        sp, n1 = spread_of(sub)
        if len(sp) < 200:
            continue
        tr, te = sp[sp["B_end"] < TRAIN_END], sp[sp["B_end"] >= TRAIN_END]
        sign = float(np.sign(tr["ret_next_spread"].mean()) or 1.0)
        nm = f"B2_{s0}_to_{NEXT[s0]}_gap{g}h"
        hold = HOLD_H[s0] - g
        r = gated(sp, nm + "_FULL", "reversion => spread>0 (PREREG H_B2)", n1,
                  extra={"hold_hours": hold, "entry_gap_hours": g,
                         "gross_bps_per_hour_held": None})
        r["gross_bps_per_hour_held"] = round(r["gross_bps"] / hold, 3) if hold > 0 else None
        te2 = te.copy(); te2["ret_next_spread"] = te2["ret_next_spread"] * sign
        r_te = gated(te2, nm + "_TEST_signfrozen",
                     f"sign {sign:+.0f} frozen on TRAIN 2020-2023, measured on TEST 2024-2026", n1,
                     extra={"train_gross_bps": round(float(tr["ret_next_spread"].mean()), 2),
                            "train_sign": int(sign), "n_train_events": int(len(tr)),
                            "hold_hours": hold, "entry_gap_hours": g})
        if g == 0:
            pass
        summary.append(dict(arm=s0, gap_h=g, hold_h=hold, full_bps=r["gross_bps"],
                            bps_per_hour=r["gross_bps_per_hour_held"], full_t=r["t_stat_declustered"],
                            train_bps=round(float(tr["ret_next_spread"].mean()), 2),
                            test_signed_bps=r_te["gross_bps"], test_t=r_te["t_stat_declustered"]))

print("\n--- entry-gap sweep: bid-ask-bounce control (a bounce artifact SHRINKS with the gap) ---")
print(pd.DataFrame(summary).sort_values(["arm", "gap_h"]).to_string(index=False))

# ================= 2. ARM vs ARM, correctly aligned ===================================
# originating calendar day = the day whose session produced the signal.
# LATE's boundary is d+24h, so B_end.floor('D') == d+1 and must be shifted back one day.
for s0 in ["ASIA", "EU", "US", "LATE"]:
    sub = D[0][D[0]["sess"] == s0]
    sp, _ = spread_of(sub)
    day = sp["B_end"].dt.floor("D") - (pd.Timedelta(days=1) if s0 == "LATE" else pd.Timedelta(0))
    arm_series[s0] = sp.assign(day=day).groupby("day")["ret_next_spread"].mean()
    arm_series[s0 + "_perh"] = arm_series[s0] / HOLD_H[s0]

print("\narm series lengths (originating day index):",
      {k: len(v) for k, v in arm_series.items() if not k.endswith("_perh")})


def contrast(a, b, tag):
    j = pd.concat([arm_series[a].rename("a"), arm_series[b].rename("b")], axis=1).dropna()
    if len(j) < 30:
        return None
    v = (j["a"] - j["b"]).to_numpy()
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    ci, _ = block_bootstrap_ci(v, n_boot=4000)
    return dict(comparison=tag, diff_bps=round(float(v.mean()), 3), n_paired_days=int(len(v)),
                t=round(float(t), 3), ci95=[round(ci[0], 2), round(ci[1], 2)])


arm_cmp = []
for a, b in [("LATE", "EU"), ("LATE", "US"), ("ASIA", "EU"), ("US", "EU"), ("LATE", "ASIA")]:
    x = contrast(a, b, f"{a}->{NEXT[a]} minus {b}->{NEXT[b]} (raw bps)")
    if x: arm_cmp.append(x)
for a, b in [("LATE", "EU"), ("ASIA", "EU"), ("US", "EU")]:
    x = contrast(a + "_perh", b + "_perh", f"{a}->{NEXT[a]} minus {b}->{NEXT[b]} (bps per hour held)")
    if x: arm_cmp.append(x)
print("\n--- ARM vs ARM, paired on the ORIGINATING calendar day (this is the clock claim) ---")
print("    (family_b_depth.py reported n_days=0 / NaN here: the join key was each arm's own boundary)")
print(pd.DataFrame(arm_cmp).to_string(index=False))

# ================= 3. full gate on the survivor: EU -> US continuation ================
eu0 = D[0][D[0]["sess"] == "EU"]
for nb in (5, 10, 20):
    sp, n1 = spread_of(eu0, nb=nb)
    gated(sp, f"EUUS_concentration_q{nb}", "concentration sweep on the surviving arm", n1)
q = eu0.groupby("B_end")["dv"].transform(lambda s: s.rank(pct=True))
for tag, mask in (("LIQ_LOW", q <= 0.33), ("LIQ_MID", (q > 0.33) & (q < 0.67)), ("LIQ_HIGH", q >= 0.67)):
    sp, n1 = spread_of(eu0[mask], minxs=15)
    if len(sp) >= 200:
        gated(sp, f"EUUS_{tag}", "liquidity conditioning", n1)
for lo, hi, tag in [(2020, 2022, "ERA_2020_22"), (2023, 2024, "ERA_2023_24"), (2025, 2027, "ERA_2025_26")]:
    sub = eu0[(eu0["B_end"].dt.year >= lo) & (eu0["B_end"].dt.year <= hi)]
    sp, n1 = spread_of(sub)
    if len(sp) >= 200:
        gated(sp, f"EUUS_{tag}", "era split: is the effect still alive in 2025-26?", n1)
sp, n1 = spread_of(eu0, winsor=0.10)
gated(sp, "EUUS_winsor10", "robustness: +/-10% winsorised window returns (PREREG Amendment 1)", n1)
# placebo: same population, random ranking -> must be zero
pl = eu0.copy()
pl["rand"] = RNG.random(len(pl))
sp, n1 = spread_of(pl, rank="rand")
gated(sp, "EUUS_PLACEBO_random_rank", "placebo: must be indistinguishable from zero", n1)

crit = family_maxt(results, n_boot=800)
print("\nFamily B (final) max-|t| 95% critical value:", round(crit, 3), "over", len(results), "cells")
for r in results:
    v, why = auto_verdict(r, family_maxt_crit=crit)
    r["verdict"], r["verdict_reason"], r["family_maxt_crit"] = v, why, round(crit, 3)
    r.pop("day_series", None)
json.dump({"mechanisms": results, "arm_vs_arm_fixed": arm_cmp, "gap_sweep": summary,
           "family_maxt_crit": round(crit, 3)},
          open(f"{OUT}/results_family_b_final.json", "w"), indent=1, default=str)

cols = ["mechanism", "n_raw", "n_independent_L2", "n_independent_L3", "gross_bps", "net_bps_2leg",
        "net_bps_2leg_stress56", "t_stat_declustered", "IR_day", "ex_best_year_gross_bps",
        "n_required_independent_days", "event_rate_per_week_last6m",
        "eta_forward_confirmation_years", "verdict"]
pd.set_option("display.width", 260); pd.set_option("display.max_rows", 120)
print(pd.DataFrame(results)[cols].to_string(index=False))
