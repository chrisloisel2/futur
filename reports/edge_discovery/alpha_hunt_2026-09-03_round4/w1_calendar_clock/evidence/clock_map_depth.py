"""CLOCK MAP, depth pass — push the one real finding of this axis to the full §2 gate.

clock_map_v2.py established that the SIGN of 6h cross-sectional autocorrelation depends on
the UTC entry hour: reversion in the Asia hours (h00-h06), continuation in the EU/US hours
(h13-h18), flat at the handovers. The contrast survives a family-wise max-t over 24 arms and
is measured arm-vs-arm on paired calendar days, never against zero.

This pass answers the only question left: can it be TRADED?
  1. holding-period x concentration sweep on the two blocks;
  2. a combined daily strategy (reversion arm + continuation arm), with the hours CHOSEN ON
     TRAIN 2020-2023 and measured on TEST 2024-2026, so the selection is not a refit;
  3. the complete gate incl. eta_forward_confirmation.

COST ACCOUNTING (briefing §8.9, turnover-based). One EPISODE = one 2-leg dollar-neutral
round trip = 28bps base / 56bps stress. The combined strategy runs TWO episodes per day
(they do not overlap: the Asia leg is closed before the EU/US leg opens), so it is fed to the
gate as two observations per day. run_gate averages within the calendar day, so gross_bps is
per episode and the 28/56 columns stay literally correct; event_rate stays daily.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread
from gate import run_gate, auto_verdict, family_maxt, block_bootstrap_ci
from clock_lib import resid_roll_hours, assert_roll_ok, load_hourly
OUT = os.path.dirname(os.path.abspath(__file__))
SIG_H = 6
TRAIN_END = pd.Timestamp("2024-01-01", tz="UTC")

h, px_at, ent = load_hourly(SCRATCH, con, eligibility)
h = resid_roll_hours(h, SIG_H); assert_roll_ok(h, SIG_H)
sig = h[["symbol", "hour_end", f"resid{SIG_H}"]].dropna().rename(columns={"hour_end": "H"})
sig["hb"] = sig["H"].dt.hour
print("signal rows", len(sig))

results = []


def arm(hb, hold, nb, gap=0):
    """gap=0 -> enter at H+5m (one 5m implementation bar, PREREG §1).
    gap>=1 -> enter `gap` hours after H. The exit is held fixed at H+hold, so a bid-ask
    bounce (which lives in the first print after the signal window closes) must SHRINK,
    while a real flow effect need not. This is the same control applied to Family B."""
    d = sig[sig["hb"] == hb].copy()
    if gap == 0:
        d = d.merge(ent.rename(columns={"T": "H", "p5": "p_entry"}), on=["symbol", "H"], how="inner")
    else:
        d["T_ent"] = d["H"] + pd.Timedelta(hours=gap)
        d = d.merge(px_at.rename(columns={"T": "T_ent", "p": "p_entry"}), on=["symbol", "T_ent"], how="inner")
    d = d[d["p_entry"] > 0]
    d["T_exit"] = d["H"] + pd.Timedelta(hours=hold)
    d = d.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="inner")
    d = d[d["p_exit"] > 0]
    if len(d) == 0:
        return None, None
    d["ret_next"] = np.log(d["p_exit"] / d["p_entry"])
    sp, n1 = xs_spread(d, "H", f"resid{SIG_H}", ["ret_next"], n_buckets=nb, min_xs=20)
    if len(sp) < 200:
        return None, None
    return sp, n1


# ================ 1. holding-period x concentration sweep on the two blocks ===========
sweep = []
for hb in (2, 4, 14, 15):
    for hold in (4, 8, 12):
        for nb in (5, 10, 20):
            for gap in (0, 1):
                if gap >= hold:
                    continue
                sp, n1 = arm(hb, hold, nb, gap)
                if sp is None:
                    continue
                o = sp[["H", "ret_next_spread"]].rename(columns={"H": "ts", "ret_next_spread": "ret_bps"})
                r = run_gate(o, f"CLK_h{hb:02d}_hold{hold}h_q{nb}_gap{gap}h", "clock arm, swept",
                             n_ind_L1=n1, cost_legs=2, n_boot=1200,
                             extra={"hour": hb, "hold_hours": hold, "buckets": nb, "entry_gap_hours": gap})
                tr = sp[sp["H"] < TRAIN_END]["ret_next_spread"]
                te = sp[sp["H"] >= TRAIN_END]["ret_next_spread"].to_numpy()
                sgn = float(np.sign(tr.mean()) or 1.0)
                r["test_gross_bps_signfrozen"] = round(float(sgn * te.mean()), 2)
                r["test_t_signfrozen"] = round(float(sgn * te.mean() / (te.std(ddof=1) / np.sqrt(len(te)))), 3)
                results.append(r)
                sweep.append(dict(hour=hb, hold=hold, q=nb, gap=gap, gross=r["gross_bps"],
                                  t=r["t_stat_declustered"], net_2leg=r["net_bps_2leg"],
                                  test_bps=r["test_gross_bps_signfrozen"], test_t=r["test_t_signfrozen"],
                                  IR=r["IR_day"], eta_y=r["eta_forward_confirmation_years"]))
print("\n--- holding x concentration x entry-gap sweep (2-leg base cost = 28bps) ---")
print(pd.DataFrame(sweep).to_string(index=False))

# bounce control, headline form: the same 24-hour profile entered 1h after the signal window
prof = []
for hb in range(24):
    row = {"hour": hb}
    for gap in (0, 1):
        sp, _ = arm(hb, 8, 5, gap)
        if sp is None:
            row[f"gap{gap}h_bps"] = None; continue
        v = sp["ret_next_spread"].to_numpy()
        row[f"gap{gap}h_bps"] = round(float(v.mean()), 2)
        row[f"gap{gap}h_t"] = round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2)
    prof.append(row)
dfp = pd.DataFrame(prof)
dfp["retention"] = (dfp["gap1h_bps"] / dfp["gap0h_bps"]).round(2)
print("\n--- 24h profile, entry at H+5m vs entry at H+1h (bounce control) ---")
print(dfp.to_string(index=False))

# ================ 2. combined daily clock strategy, hours picked ON TRAIN ============
HOLD, NB = 8, 5
series = {}
for hb in range(24):
    sp, n1 = arm(hb, HOLD, NB)
    if sp is not None:
        series[hb] = sp.set_index(sp["H"].dt.floor("D"))["ret_next_spread"].groupby(level=0).mean()
allser = pd.concat([s.rename(k) for k, s in series.items()], axis=1)
train = allser[allser.index < TRAIN_END]
h_rev = int(train.mean().idxmax())      # best reversion hour ON TRAIN ONLY
h_con = int(train.mean().idxmin())      # best continuation hour ON TRAIN ONLY
print(f"\nhours selected on TRAIN 2020-2023 only: reversion h{h_rev:02d} "
      f"({train.mean()[h_rev]:.2f} bps), continuation h{h_con:02d} ({train.mean()[h_con]:.2f} bps)")

def combined(idx_filter=None, label=""):
    a = series[h_rev].rename("a")
    b = (-series[h_con]).rename("b")       # sign frozen on TRAIN: short the continuation spread
    j = pd.concat([a, b], axis=1).dropna()
    if idx_filter is not None:
        j = j[idx_filter(j.index)]
    obs = pd.concat([
        pd.DataFrame({"ts": j.index + pd.Timedelta(hours=h_rev), "ret_bps": j["a"].to_numpy()}),
        pd.DataFrame({"ts": j.index + pd.Timedelta(hours=h_con), "ret_bps": j["b"].to_numpy()}),
    ], ignore_index=True)
    r = run_gate(obs, f"CLK_COMBINED_h{h_rev:02d}rev_h{h_con:02d}con{label}",
                 "two non-overlapping 2-leg episodes per day; hours chosen on TRAIN only",
                 n_ind_L1=None, cost_legs=2, n_boot=3000,
                 extra={"episodes_per_day": 2, "hour_reversion": h_rev, "hour_continuation": h_con,
                        "selection_period": "TRAIN 2020-2023", "hold_hours": HOLD, "buckets": NB})
    results.append(r)
    return r

r_full = combined(None, "_FULL")
r_test = combined(lambda ix: ix >= TRAIN_END, "_TEST2024_26")
print("\n--- combined daily clock strategy ---")
print(pd.DataFrame([{k: v for k, v in r.items() if k in
                     ("mechanism", "n_raw", "n_independent_L2", "gross_bps", "net_bps_2leg",
                      "net_bps_2leg_stress56", "t_stat_declustered", "IR_day", "sharpe_ann_equiv",
                      "ex_best_year_gross_bps", "n_required_independent_days",
                      "event_rate_per_week_last6m", "eta_forward_confirmation_years")}
                    for r in (r_full, r_test)]).to_string(index=False))

# concentration on the combined strategy
for nb in (10, 20):
    s2 = {}
    for hb in (h_rev, h_con):
        sp, _ = arm(hb, HOLD, nb)
        s2[hb] = sp.set_index(sp["H"].dt.floor("D"))["ret_next_spread"].groupby(level=0).mean()
    j = pd.concat([s2[h_rev].rename("a"), (-s2[h_con]).rename("b")], axis=1).dropna()
    obs = pd.concat([pd.DataFrame({"ts": j.index + pd.Timedelta(hours=h_rev), "ret_bps": j["a"].to_numpy()}),
                     pd.DataFrame({"ts": j.index + pd.Timedelta(hours=h_con), "ret_bps": j["b"].to_numpy()})],
                    ignore_index=True)
    results.append(run_gate(obs, f"CLK_COMBINED_q{nb}", "combined strategy, concentration sweep",
                            cost_legs=2, n_boot=1500,
                            extra={"episodes_per_day": 2, "buckets": nb, "hold_hours": HOLD}))

# placebo on the combined construction: shuffle the day labels of the continuation arm
rng = np.random.default_rng(31337)
a = series[h_rev].rename("a"); b = (-series[h_con]).rename("b")
j = pd.concat([a, b], axis=1).dropna()
jb = j["b"].to_numpy().copy(); rng.shuffle(jb)
obs = pd.concat([pd.DataFrame({"ts": j.index + pd.Timedelta(hours=h_rev), "ret_bps": j["a"].to_numpy()}),
                 pd.DataFrame({"ts": j.index + pd.Timedelta(hours=h_con), "ret_bps": jb})], ignore_index=True)
results.append(run_gate(obs, "CLK_COMBINED_PLACEBO_shuffled_continuation_arm",
                        "placebo: destroys the day pairing, must leave the mean unchanged but "
                        "inflate the day-level variance", cost_legs=2, n_boot=1500))

crit = family_maxt(results, n_boot=800)
print("\nclock-depth family max-|t| 95% crit:", round(crit, 3), "over", len(results), "cells")
for r in results:
    v, why = auto_verdict(r, family_maxt_crit=crit)
    r["verdict"], r["verdict_reason"], r["family_maxt_crit"] = v, why, round(crit, 3)
    r.pop("day_series", None)
json.dump({"mechanisms": results, "sweep": sweep, "bounce_control_profile": prof,
           "selected_hours_on_train": {"reversion": h_rev, "continuation": h_con},
           "family_maxt_crit": round(crit, 3)},
          open(f"{OUT}/results_clock_map_depth.json", "w"), indent=1, default=str)
cols = ["mechanism", "n_independent_L2", "gross_bps", "net_bps_2leg", "net_bps_2leg_stress56",
        "t_stat_declustered", "IR_day", "ex_best_year_gross_bps",
        "n_required_independent_days", "event_rate_per_week_last6m",
        "eta_forward_confirmation_years", "verdict"]
pd.set_option("display.width", 260); pd.set_option("display.max_rows", 100)
print(pd.DataFrame(results)[cols].to_string(index=False))
