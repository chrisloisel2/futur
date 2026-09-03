"""Family A — FUNDING CLOCK (settlements 00:00/08:00/16:00 UTC).

Signal is ALWAYS the last SETTLED funding rate (backward-looking, PIT-safe) or the
contemporaneous basis. The upcoming settlement's rate is not in the panel and is not used.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread, MIN_XS
from gate import run_gate, auto_verdict, family_maxt

OUT = os.path.dirname(os.path.abspath(__file__))

c = con()
ev = c.execute(f"SELECT * FROM read_parquet('{SCRATCH}/funding_events.parquet')").df()
ev["F"] = pd.to_datetime(ev["F"], utc=True)
ev["d"] = ev["F"].dt.floor("D")
print("raw events", len(ev))

el = eligibility()
ev = ev.merge(el, on=["symbol", "d"], how="left")
ev = ev[ev["eligible"].fillna(False)]
print("eligible events", len(ev), "days", ev['d'].nunique(), "symbols", ev['symbol'].nunique())

# ---- window returns (log), PIT per PREREG §1 ----
def lr(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(b / a)

ev["ret_pre"]      = lr(ev["p_entry_pre"],   ev["p_exit_pre"])    # [F-55m, F]
ev["ret_post"]     = lr(ev["p_entry_post"],  ev["p_exit_post"])   # [F+5m, F+60m]
ev["ret_postb"]    = lr(ev["p_entry_postb"], ev["p_exit_post"])   # [F+10m, F+60m]
# straddle holds ACROSS the settlement -> the funding cashflow is real and is added.
# a long position pays funding_rate when funding_rate>0.  Long low-funding / short
# high-funding therefore RECEIVES (fr_high - fr_low) >= 0 mechanically; that carry is
# accounted for explicitly at the spread level below, not here.
ev["ret_strad"]    = lr(ev["p_entry_pre"],   ev["p_exit_post"])   # [F-55m, F+60m]

for col in ["p_sig", "p_entry_pre", "p_exit_pre", "p_entry_post", "p_entry_postb", "p_exit_post"]:
    ev = ev[ev[col] > 0]
print("events with all prices", len(ev))

RET_COLS = ["ret_pre", "ret_post", "ret_postb", "ret_strad"]
results = []

def add(obs_df, retcol, name, hypo, n_L1, notes="", extra=None):
    o = obs_df[["F", retcol + "_spread"]].rename(columns={"F": "ts", retcol + "_spread": "ret_bps"})
    r = run_gate(o, name, hypo, n_ind_L1=n_L1, cost_legs=2, notes=notes, extra=extra)
    results.append(r)
    return r

# =============================== A1 / A2 / A3 =================================
sp, nL1 = xs_spread(ev, "F", "fr_prev", RET_COLS)
print("A1 events with valid spread", len(sp))
add(sp, "ret_pre",   "A1_pre_settlement_drift_funding_rank",
    "H_A1: spread(low-funding - high-funding) > 0 in [F-55m, F]", nL1)
add(sp, "ret_post",  "A2_post_settlement_reversion_funding_rank",
    "H_A2: spread < 0 in [F+5m, F+60m]", nL1)
add(sp, "ret_postb", "A2b_post_settlement_reversion_10m_lag",
    "H_A2b: same as A2 with an extra bar of lag", nL1)

# straddle: add the explicit funding carry received by the spread
carry = ev.copy()
carry["_r"] = carry.groupby("F")["fr_prev"].rank(method="first", pct=True)
lo = carry[carry["_r"] <= .2].groupby("F")["fr_settled"].mean()
hi = carry[carry["_r"] >  .8].groupby("F")["fr_settled"].mean()
carry_bps = ((hi - lo) * 1e4).rename("carry_bps")   # received by long-low/short-high
sp2 = sp.set_index("F").join(carry_bps).reset_index()
sp2["ret_strad_spread_wcarry"] = sp2["ret_strad_spread"] + sp2["carry_bps"].fillna(0)
o = sp2[["F", "ret_strad_spread_wcarry"]].rename(columns={"F": "ts", "ret_strad_spread_wcarry": "ret_bps"})
results.append(run_gate(o, "A3_straddle_with_explicit_funding_carry",
    "H_A3: no independent prediction; A1+A2+carry", n_ind_L1=nL1, cost_legs=2,
    notes="funding cashflow of the settlement added explicitly (spread receives fr_high-fr_low)"))
# and the pure carry component alone, for decomposition
oc = sp2[["F", "carry_bps"]].rename(columns={"F": "ts", "carry_bps": "ret_bps"})
results.append(run_gate(oc, "A3c_pure_funding_carry_component",
    "decomposition only: the mechanical cashflow, no price move", n_ind_L1=nL1, cost_legs=2,
    notes="not a standalone mechanism; shows how much of A3 is cashflow vs price"))

# =============================== A4 magnitude ==================================
hot = ev[ev["fr_pct90"] >= 0.90]
sp_h, nL1h = xs_spread(hot, "F", "fr_prev", RET_COLS, n_buckets=5, min_xs=10)
add(sp_h, "ret_pre",  "A4_pre_extreme_funding_pct90",
    "H_A4: |effect| larger than A1 on the same clock", nL1h,
    notes="restricted to symbols whose |funding| is in its own 90d top decile; min_xs relaxed to 10")
add(sp_h, "ret_post", "A4b_post_extreme_funding_pct90",
    "H_A4: |effect| larger than A2 on the same clock", nL1h, notes="min_xs relaxed to 10")

# =============================== A5 settlement hour ============================
for h in (0, 8, 16):
    sub = ev[ev["settle_hour"] == h]
    s, n1 = xs_spread(sub, "F", "fr_prev", RET_COLS)
    add(s, "ret_pre",  f"A5_pre_settle_hour_{h:02d}",  f"H_A5: hour {h:02d} differs from the others (arm-vs-arm)", n1)
    add(s, "ret_post", f"A5_post_settle_hour_{h:02d}", f"H_A5: hour {h:02d} differs from the others (arm-vs-arm)", n1)

# =============================== A6 basis signal ===============================
sp_b, nL1b = xs_spread(ev, "F", "basis_sig", RET_COLS)
add(sp_b, "ret_pre",  "A6_pre_basis_rank",  "H_A6: same sign as A1, stronger", nL1b)
add(sp_b, "ret_post", "A6b_post_basis_rank", "H_A6: same sign as A2, stronger", nL1b)

# =============================== robustness: winsorised ========================
sp_w, nL1w = xs_spread(ev, "F", "fr_prev", RET_COLS, winsor=0.10)
add(sp_w, "ret_pre",  "A1w_pre_winsor10",  "robustness of A1 (±10% winsor)", nL1w)
add(sp_w, "ret_post", "A2w_post_winsor10", "robustness of A2 (±10% winsor)", nL1w)

# =============================== family max-t ==================================
core = [r for r in results if not r["mechanism"].startswith(("A1w", "A2w", "A3c"))]
crit = family_maxt(core)
print("Family A max-|t| 95% critical value:", round(crit, 3))
for r in results:
    v, why = auto_verdict(r, family_maxt_crit=crit)
    r["verdict"] = v
    r["verdict_reason"] = why
    r["family_maxt_crit"] = round(crit, 3)
    ds = r.pop("day_series", None)

with open(f"{OUT}/results_family_a.json", "w") as f:
    json.dump(results, f, indent=1, default=str)

cols = ["mechanism", "n_raw", "n_independent_L2", "gross_bps", "net_bps_2leg",
        "t_stat_declustered", "t_stat_naive_WRONG", "clustering_inflation_factor",
        "IR_day", "eta_forward_confirmation_years", "verdict"]
df = pd.DataFrame(results)[cols]
pd.set_option("display.width", 220)
print(df.to_string(index=False))
