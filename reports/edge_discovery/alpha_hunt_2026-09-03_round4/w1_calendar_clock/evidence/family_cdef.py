"""Families C (weekend), D (month/quarter end), E (clock x event), F (clock as
meta-conditioner) — pre-registered in PREREGISTRATION.md §6 but not yet run when the first
pass was interrupted. Run here so the pre-registration is honoured in full, including the
families whose verdict was pre-declared adverse (C and D: <= 1 episode/week by construction,
so UNCONFIRMABLE_IN_HORIZON unless the effect is enormous — see PREREG §6).

Every conditioning test is ARM-vs-ARM on the same population (briefing §1.3). Nothing is
judged on "arm A is positive".
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread
from gate import run_gate, auto_verdict, family_maxt, block_bootstrap_ci
from clock_lib import load_hourly
OUT = os.path.dirname(os.path.abspath(__file__))
CASCADE = "/home/qbee/futur/data/events/liq_cascade_dataset.parquet"

h, px_at, ent = load_hourly(SCRATCH, con, eligibility)
h = h.sort_values(["symbol", "hour_end"]).reset_index(drop=True)
# cumulative residual index per symbol -> any window sum = cum(t) - cum(t - W)
h["cum"] = h.groupby("symbol")["resid_logret_hour"].cumsum()
cum = h[["symbol", "hour_end", "cum"]]


def window_resid(times_df, hours, tcol="T"):
    """Residual return over (T-hours, T], PIT by construction."""
    a = times_df.merge(cum.rename(columns={"hour_end": tcol, "cum": "cum_end"}), on=["symbol", tcol], how="inner")
    a["_t0"] = a[tcol] - pd.Timedelta(hours=hours)
    a = a.merge(cum.rename(columns={"hour_end": "_t0", "cum": "cum_start"}), on=["symbol", "_t0"], how="inner")
    a["sig"] = a["cum_end"] - a["cum_start"]
    return a.drop(columns=["cum_end", "cum_start", "_t0"])


results = []


def gate_push(sp, spcol, tscol, name, hypo, n1=None, legs=2, extra=None, nboot=2000):
    o = sp[[tscol, spcol]].rename(columns={tscol: "ts", spcol: "ret_bps"})
    r = run_gate(o, name, hypo, n_ind_L1=n1, cost_legs=legs, n_boot=nboot, extra=extra)
    results.append(r)
    return r


def unpaired_arm_diff(day_a, day_b, tag):
    """Difference of two arms measured on day-level means. Paired where the same day carries
    both arms, otherwise a two-sample difference with week-block bootstrap on each side."""
    j = pd.concat([day_a.rename("a"), day_b.rename("b")], axis=1)
    both = j.dropna()
    out = {"comparison": tag, "n_days_a": int(day_a.notna().sum()), "n_days_b": int(day_b.notna().sum())}
    if len(both) >= 30:
        v = (both["a"] - both["b"]).to_numpy()
        ci, _ = block_bootstrap_ci(v, n_boot=3000)
        out.update(paired=True, n_paired_days=int(len(v)), diff_bps=round(float(v.mean()), 2),
                   t=round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 3),
                   ci95=[round(ci[0], 2), round(ci[1], 2)])
    else:
        a, b = day_a.dropna().to_numpy(), day_b.dropna().to_numpy()
        if len(a) < 5 or len(b) < 5:
            out.update(paired=False, diff_bps=None, t=None, note="too few days")
            return out
        se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        out.update(paired=False, diff_bps=round(float(a.mean() - b.mean()), 2),
                   t=round(float((a.mean() - b.mean()) / se), 3),
                   ci95=[round(float(a.mean() - b.mean() - 1.96 * se), 2),
                         round(float(a.mean() - b.mean() + 1.96 * se), 2)])
    return out


arm_tests = []

# ============================ FAMILY C — WEEKEND CLOCK ================================
# C1: rank on the trailing week's residual observed at Fri 21:00 UTC; hold Sat 00:00 -> Mon 00:00.
fri = h[(h["hour_end"].dt.dayofweek == 4) & (h["hour_end"].dt.hour == 21)][["symbol", "hour_end"]].rename(
    columns={"hour_end": "T"})
c1 = window_resid(fri, 168)
c1["T_entry"] = c1["T"] + pd.Timedelta(hours=3)      # Sat 00:00
c1["T_exit"] = c1["T"] + pd.Timedelta(hours=51)      # Mon 00:00
c1 = c1.merge(ent.rename(columns={"T": "T_entry", "p5": "p_entry"}), on=["symbol", "T_entry"], how="inner")
c1 = c1.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="inner")
c1 = c1[(c1["p_entry"] > 0) & (c1["p_exit"] > 0)].copy()
c1["ret"] = np.log(c1["p_exit"] / c1["p_entry"])
sp, n1 = xs_spread(c1, "T", "sig", ["ret"], n_buckets=5, min_xs=20)
print("C1 events", len(sp))
gate_push(sp, "ret_spread", "T", "C1_friday_to_monday_xs_reversal",
          "H_C1: reversion => spread(losers-winners) > 0", n1)

# C3: weekend drift Sat 00:00 -> Sun 20:00, hold Sun 20:05 -> Mon 02:00.
sun = h[(h["hour_end"].dt.dayofweek == 6) & (h["hour_end"].dt.hour == 20)][["symbol", "hour_end"]].rename(
    columns={"hour_end": "T"})
c3 = window_resid(sun, 44)                            # Sat 00:00 -> Sun 20:00
c3["T_exit"] = c3["T"] + pd.Timedelta(hours=6)        # Mon 02:00
c3 = c3.merge(ent.rename(columns={"T": "T", "p5": "p_entry"}), on=["symbol", "T"], how="inner")
c3 = c3.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="inner")
c3 = c3[(c3["p_entry"] > 0) & (c3["p_exit"] > 0)].copy()
c3["ret"] = np.log(c3["p_exit"] / c3["p_entry"])
sp3, n3 = xs_spread(c3, "T", "sig", ["ret"], n_buckets=5, min_xs=20)
print("C3 events", len(sp3))
gate_push(sp3, "ret_spread", "T", "C3_sunday_evening_gap_into_monday",
          "H_C3: continuation into Monday liquidity => spread(losers-winners) < 0", n3)

# ============ FAMILY D/F — DAILY XS MECHANISM SPLIT BY CALENDAR ARM ===================
# One common mechanism: signal = residual over the 24h ending 00:00 UTC, entry 00:05,
# exit next 00:00. Then the SAME mechanism is split by calendar arm and judged arm-vs-arm.
mid = h[h["hour_end"].dt.hour == 0][["symbol", "hour_end"]].rename(columns={"hour_end": "T"})
dd = window_resid(mid, 24)
dd["T_exit"] = dd["T"] + pd.Timedelta(hours=24)
dd = dd.merge(ent.rename(columns={"T": "T", "p5": "p_entry"}), on=["symbol", "T"], how="inner")
dd = dd.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="inner")
dd = dd[(dd["p_entry"] > 0) & (dd["p_exit"] > 0)].copy()
dd["ret"] = np.log(dd["p_exit"] / dd["p_entry"])
spd, nd = xs_spread(dd, "T", "sig", ["ret"], n_buckets=5, min_xs=20)
spd["day"] = spd["T"].dt.floor("D")
print("daily XS events", len(spd))
gate_push(spd, "ret_spread", "T", "DF_daily_xs_reversal_BASELINE",
          "baseline for every calendar-arm split below", nd)

dser = spd.set_index("day")["ret_spread"]
mo = spd["T"].dt.month
dom = spd["T"].dt.day
dim = spd["T"].dt.days_in_month
ARMS = {
    "month_end_last2d": (dom > dim - 2).to_numpy(),
    "quarter_expiry_week": ((mo.isin([3, 6, 9, 12])) & (dom >= 22) & (dom <= 28)).to_numpy(),
    "weekend_sat_sun": spd["T"].dt.dayofweek.isin([5, 6]).to_numpy(),
    "monday": (spd["T"].dt.dayofweek == 0).to_numpy(),
}
for nm, m in ARMS.items():
    a = dser[m]
    b = dser[~m]
    arm_tests.append(dict(unpaired_arm_diff(a, b, f"D/F: daily XS reversal, {nm} minus rest"),
                          family="D/F", n_in_arm=int(m.sum())))
    if m.sum() >= 30:
        gate_push(spd[m], "ret_spread", "T", f"DF_daily_xs_{nm}",
                  "level of the arm (judge on the arm-vs-arm row, not this)", None,
                  extra={"arm_only": True})

# ============================ FAMILY E — CLOCK x EVENT ================================
c = con()
cas = c.execute(f"""SELECT event_time, symbol, hour_utc, dow, fwd_1h, fwd_4h, fwd_8h,
                           n_events_sym_24h, is_long_cascade
                    FROM read_parquet('{CASCADE}')""").df()
cas["event_time"] = pd.to_datetime(cas["event_time"], utc=True)
cas["day"] = cas["event_time"].dt.floor("D")
cas["sess"] = pd.cut(cas["hour_utc"], [-1, 6, 12, 20, 23], labels=["ASIA", "EU", "US", "LATE"])
cas["is_weekend"] = cas["event_time"].dt.dayofweek.isin([5, 6])
cas["repeat3"] = cas["n_events_sym_24h"] >= 3
cas["first"] = cas["n_events_sym_24h"] <= 1
for col in ["fwd_1h", "fwd_4h", "fwd_8h"]:
    cas[col + "_bps"] = cas[col] * 1e4
print("cascades", len(cas), "| date span", cas["event_time"].min(), cas["event_time"].max())

# E1 cascade payoff by session, ARM-vs-ARM (never "session X is positive")
for hz in ["fwd_4h", "fwd_8h"]:
    dayser = {s: cas[cas["sess"] == s].groupby("day")[hz + "_bps"].mean() for s in ["ASIA", "EU", "US", "LATE"]}
    for a, b in [("ASIA", "US"), ("ASIA", "EU"), ("LATE", "EU"), ("US", "EU")]:
        arm_tests.append(dict(unpaired_arm_diff(dayser[a], dayser[b],
                        f"E1: cascade {hz} payoff, session {a} minus {b}"), family="E1"))
    for s in ["ASIA", "EU", "US", "LATE"]:
        sub = cas[cas["sess"] == s]
        if len(sub) < 200:
            continue
        gate_push(sub.assign(x=sub[hz + "_bps"]), "x", "event_time", f"E1_cascade_{hz}_{s}",
                  "level of one session arm (judge on the arm-vs-arm rows)", None, legs=1,
                  extra={"arm_only": True})

# E2 repeat-cascade effect (already known: 1st negative, 3rd+ positive) x weekend, diff-in-diff
for hz in ["fwd_4h", "fwd_8h"]:
    for wk in (False, True):
        s = cas[cas["is_weekend"] == wk]
        a = s[s["repeat3"]].groupby("day")[hz + "_bps"].mean()
        b = s[s["first"]].groupby("day")[hz + "_bps"].mean()
        arm_tests.append(dict(unpaired_arm_diff(a, b,
                        f"E2: cascade {hz}, repeat>=3 minus first, {'weekend' if wk else 'weekday'}"),
                        family="E2"))
    # difference-in-differences: does the weekend change the repeat effect?
    def rep_eff(mask):
        s = cas[mask]
        return (s[s["repeat3"]].groupby("day")[hz + "_bps"].mean(),
                s[s["first"]].groupby("day")[hz + "_bps"].mean())
    aw, bw = rep_eff(cas["is_weekend"])
    ad, bd = rep_eff(~cas["is_weekend"])
    de_w = aw.mean() - bw.mean(); de_d = ad.mean() - bd.mean()
    se = np.sqrt(aw.var(ddof=1) / len(aw) + bw.var(ddof=1) / len(bw)
                 + ad.var(ddof=1) / len(ad) + bd.var(ddof=1) / len(bd))
    arm_tests.append(dict(comparison=f"E2 DiD: cascade {hz} repeat effect, weekend minus weekday",
                          family="E2", paired=False, diff_bps=round(float(de_w - de_d), 2),
                          t=round(float((de_w - de_d) / se), 3),
                          n_days_a=int(len(aw)), n_days_b=int(len(ad))))
    # repeat>=3 arm as a standalone mechanism, split weekend/weekday
    for wk, lab in ((False, "weekday"), (True, "weekend")):
        s = cas[(cas["is_weekend"] == wk) & cas["repeat3"]]
        if len(s) < 100:
            continue
        gate_push(s.assign(x=s[hz + "_bps"]), "x", "event_time",
                  f"E2_cascade_{hz}_repeat3plus_{lab}", "repeat-cascade arm by weekend/weekday",
                  None, legs=1, extra={"arm_only": True})

crit = family_maxt(results, n_boot=800)
print("\nC/D/E/F family max-|t| 95% crit:", round(crit, 3), "over", len(results), "cells")
for r in results:
    v, why = auto_verdict(r, family_maxt_crit=crit)
    r["verdict"], r["verdict_reason"], r["family_maxt_crit"] = v, why, round(crit, 3)
    r.pop("day_series", None)
json.dump({"mechanisms": results, "arm_vs_arm": arm_tests, "family_maxt_crit": round(crit, 3)},
          open(f"{OUT}/results_family_cdef.json", "w"), indent=1, default=str)

pd.set_option("display.width", 260); pd.set_option("display.max_rows", 120)
print("\n--- ARM vs ARM (the actual conditioning claims) ---")
print(pd.DataFrame(arm_tests).to_string(index=False))
print("\n--- mechanisms ---")
cols = ["mechanism", "n_raw", "n_independent_L2", "n_independent_L3", "gross_bps", "net_bps",
        "net_bps_2leg", "t_stat_declustered", "t_stat_naive_WRONG", "clustering_inflation_factor",
        "IR_day", "event_rate_per_week_last6m", "eta_forward_confirmation_years", "verdict"]
print(pd.DataFrame(results)[cols].to_string(index=False))
