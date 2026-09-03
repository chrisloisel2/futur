"""Family A, depth pass: does ANY window/concentration/liquidity/hour combination lift the
funding-clock footprint from ~2-4bps to something that clears the 28bps 2-leg cost?

This is the 'push each non-dead mechanism to the full gate' step. It is explicitly a sweep,
so the family max-t correction is applied across the whole sweep, not per cell.
"""
import json, sys, os, itertools
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread
from gate import run_gate, auto_verdict, family_maxt
OUT = os.path.dirname(os.path.abspath(__file__))

c = con()
ev = c.execute(f"SELECT * FROM read_parquet('{SCRATCH}/funding_events.parquet')").df()
ev["F"] = pd.to_datetime(ev["F"], utc=True); ev["d"] = ev["F"].dt.floor("D")
ev = ev.merge(eligibility(), on=["symbol", "d"], how="left")
ev = ev[ev["eligible"].fillna(False)]

def lr(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(b / a)

W = {                                   # window name -> (entry price col, exit price col)
    "pre55":   ("p_entry_pre",   "p_exit_pre"),     # [F-55m, F]
    "pre15":   ("p_entry_pre15", "p_exit_pre"),     # [F-15m, F]   tight pre
    "post15":  ("p_entry_post",  "p_exit_post20"),  # [F+5m, F+20m] tight post
    "post60":  ("p_entry_post",  "p_exit_post"),    # [F+5m, F+60m]
    "post120": ("p_entry_post",  "p_exit_post120"), # [F+5m, F+120m]
}
for k, (a, b) in W.items():
    ev["r_" + k] = lr(ev[a], ev[b])
RET = ["r_" + k for k in W]
ev = ev[(ev[["p_entry_pre","p_exit_pre","p_entry_pre15","p_entry_post","p_exit_post","p_exit_post20","p_exit_post120"]] > 0).all(axis=1)]
print("events", len(ev), "days", ev["d"].nunique())

results = []
def push(sub, rank, nb, tag, minxs=20):
    sp, n1 = xs_spread(sub, "F", rank, RET, n_buckets=nb, min_xs=minxs)
    if len(sp) == 0: return
    for k in W:
        col = f"r_{k}_spread"
        if col not in sp: continue
        o = sp[["F", col]].rename(columns={"F": "ts", col: "ret_bps"})
        r = run_gate(o, f"{tag}|{rank}|q{nb}|{k}", "", n_ind_L1=n1, cost_legs=2, n_boot=1500)
        results.append(r)

# 1) concentration sweep (quintile -> decile -> ventile) on the full universe
for nb in (5, 10, 20):
    push(ev, "fr_prev", nb, "ALL")
    push(ev, "basis_sig", nb, "ALL")

# 2) settlement-hour x concentration (00 UTC was the strongest arm)
for h in (0, 8, 16):
    push(ev[ev["settle_hour"] == h], "basis_sig", 10, f"H{h:02d}")

# 3) liquidity tier: is the footprint bigger where the book is thinner?
q = ev.groupby("F")["dv_med30_prev"].transform(lambda s: s.rank(pct=True))
push(ev[q <= 0.33], "basis_sig", 10, "LIQ_LOW", minxs=15)
push(ev[q >= 0.67], "basis_sig", 10, "LIQ_HIGH", minxs=15)

# 4) the triple amplifier: thin liquidity x 00 UTC x decile
push(ev[(q <= 0.33) & (ev["settle_hour"] == 0)], "basis_sig", 10, "LIQLOW_H00", minxs=15)

# 5) era split: has the 2025-26 arbitrage killed even the residual footprint?
for lo, hi, tag in [(2020, 2022, "ERA_2020_22"), (2023, 2024, "ERA_2023_24"), (2025, 2027, "ERA_2025_26")]:
    push(ev[(ev["F"].dt.year >= lo) & (ev["F"].dt.year <= hi)], "basis_sig", 10, tag)

crit = family_maxt(results, n_boot=1000)
print("depth-sweep family max-|t| 95% crit:", round(crit, 3), " (n_cells =", len(results), ")")
for r in results:
    v, why = auto_verdict(r, family_maxt_crit=crit)
    r["verdict"], r["verdict_reason"], r["family_maxt_crit"] = v, why, round(crit, 3)
    r.pop("day_series", None)
with open(f"{OUT}/results_family_a_depth.json", "w") as f:
    json.dump(results, f, indent=1, default=str)

df = pd.DataFrame(results)[["mechanism","n_raw","n_independent_L2","gross_bps","net_bps_2leg",
                            "t_stat_declustered","IR_day","eta_forward_confirmation_years","verdict"]]
df = df.sort_values("gross_bps", key=abs, ascending=False)
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
print(df.head(30).to_string(index=False))
print("\nMAX |gross_bps| anywhere in the sweep:", round(df["gross_bps"].abs().max(), 2),
      "bps  vs 2-leg base cost 28bps")
print("cells clearing 28bps gross:", int((df["gross_bps"].abs() > 28).sum()), "/", len(df))
