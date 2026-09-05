#!/usr/bin/env python3
"""W6 round-4: emit the markdown tables used verbatim in REPORT.md."""
import json, os
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.abspath(os.path.join(HERE, "..", "results"))
OUT = os.path.join(RES, "TABLES.md")


def md(df, floatfmt="{:.2f}"):
    d = df.copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda x: "" if pd.isna(x) else (floatfmt.format(x) if abs(x) < 1e6 else f"{x:.3g}"))
        else:
            d[c] = d[c].astype(str)
    head = "| " + " | ".join(d.columns) + " |"
    sep = "|" + "|".join(["---"] * len(d.columns)) + "|"
    body = "\n".join("| " + " | ".join(r) + " |" for r in d.values)
    return "\n".join([head, sep, body])


buf = []
inv = pd.DataFrame(json.load(open(os.path.join(RES, "INVENTORY.json"))))

# ---- T1 : the inventory, sorted by independent episode rate DESC
t1 = inv[["family", "n_raw", "n_independent_L1_sym24h", "n_independent_L2_days",
          "n_independent_L3_weeks", "rate_L1_per_week", "rate_L1_per_week_recent6m",
          "decluster_survival_L1", "day_coverage_recent6m", "n_symbols"]].copy()
t1.columns = ["family", "N_raw", "N_indep_L1", "N_indep_L2_days", "N_indep_L3_weeks",
              "L1/week (full)", "L1/week (last 6m)", "L1 survival", "day cov 6m", "symbols"]
t1 = t1.sort_values("L1/week (last 6m)", ascending=False)
buf.append("### T1 — INDEPENDENT-EPISODE-RATE INVENTORY (T_LIQ triggers; sorted by L1 rate)\n")
buf.append(md(t1))

# ---- T2 : break-even net bps per family (from the scored grid, T_LIQ)
fb = pd.read_csv(os.path.join(RES, "FAMILY_BREAKEVEN_T_LIQ.csv"))
t2 = fb[["family", "horizon_h", "median_rate_L1_wk", "median_n_L1", "median_days",
         "median_sd_daymean", "net_bps_min_1y", "net_bps_min_2y", "best_gross_bps",
         "gross_bps_needed_1y", "gross_shortfall_1y", "median_capacity_usd"]].copy()
t2 = t2.sort_values("net_bps_min_1y")
t2.columns = ["family", "h", "L1/wk (6m)", "N_L1", "N_days", "sd_daymean",
              "NET BPS MIN <1y", "net min <2y", "best gross obs", "gross needed <1y",
              "shortfall", "capacity $"]
buf.append("\n\n### T2 — MINIMUM NET BPS FOR <1-YEAR CONFIRMABILITY, per family (T_LIQ)\n")
buf.append(md(t2))

# ---- T2b : same, from the trigger inventory (independent of any edge estimate)
t2b = inv[["family", "sd_daymean_bps_h1", "net_bps_min_1y_h1", "sd_daymean_bps_h4",
           "net_bps_min_1y_h4", "net_bps_min_2y_h4", "sd_daymean_bps_h12",
           "net_bps_min_1y_h12", "capacity_usd_estimate_h4"]].copy()
t2b = t2b.dropna(subset=["net_bps_min_1y_h4"]).sort_values("net_bps_min_1y_h4")
t2b.columns = ["trigger family", "sd_day h1", "net_min_1y h1", "sd_day h4", "net_min_1y h4",
               "net_min_2y h4", "sd_day h12", "net_min_1y h12", "capacity $ (h4)"]
buf.append("\n\n### T2b — same break-even, computed from the TRIGGER INVENTORY alone "
           "(no edge estimate involved)\n")
buf.append(md(t2b))

# ---- T3 : the grid, ETA ascending
c = pd.DataFrame(json.load(open(os.path.join(RES, "RESULTS_CELLS.json"))))
c["eta_sort"] = c["eta_forward_confirmation_years"].fillna(np.inf)
c = c.sort_values(["eta_sort", "tier", "mechanism_id"])
t3 = c[["mechanism_id", "horizon_h", "tier", "n_raw", "n_independent_L1",
        "n_independent_L2_days", "n_independent_L3_weeks", "gross_bps", "net_bps",
        "net_bps_stress28", "t_stat_declustered", "bootstrap_ci95_net_bps",
        "ex_best_year_net_bps", "event_rate_L1_per_week_recent6m",
        "eta_forward_confirmation_years", "eta_at_zero_cost_years", "sharpe_net_ann",
        "capacity_usd_estimate", "verdict"]].copy()
t3["bootstrap_ci95_net_bps"] = t3.bootstrap_ci95_net_bps.map(
    lambda v: "" if not isinstance(v, list) or v[0] is None else f"[{v[0]:.0f},{v[1]:.0f}]")
t3.columns = ["mechanism", "h", "tier", "N_raw", "N_L1", "N_days", "N_weeks", "gross",
              "net14", "net28", "t_day", "CI95 net", "ex-best-yr", "L1/wk", "ETA (y)",
              "ETA0 (y)", "Sharpe", "capacity $", "verdict"]
buf.append("\n\n### T3a — TOP 30 CELLS BY ETA ASCENDING (primary sort key)\n")
buf.append(md(t3.head(30)))
buf.append("\n\n### T3b — ALL 261 CELLS, ETA ASCENDING\n")
buf.append(md(t3))

# ---- T4 : breadth efficiency
d1 = pd.read_csv(os.path.join(RES, "D1_BREADTH_EFFICIENCY.csv"))
t4 = d1.groupby(["family", "horizon_h"]).agg(
    raw_per_day=("raw_episodes_per_day", "median"),
    k_eff=("k_eff_independent_per_day", "median"),
    redundancy=("redundancy_factor", "median"),
    sd_episode=("sd_episode_bps", "median"),
    sd_daymean=("sd_daymean_bps", "median"),
    net_min_1y=("net_bps_min_1y", "median")).reset_index()
t4.columns = ["family", "h", "raw eps/day", "k_eff/day", "redundancy", "sd_episode",
              "sd_daymean", "net_min_1y"]
buf.append("\n\n### T4 — BREADTH EFFICIENCY: raw episodes/day vs EFFECTIVE independent episodes/day\n")
buf.append(md(t4))

# ---- T5 : cost wall
liq = c[(c.tier == "T_LIQ") & (c.insufficient != True)]
t5 = liq.groupby(["family", "horizon_h"]).apply(lambda d: pd.Series({
    "best_gross_bps": d.gross_bps.max(),
    "best_cell": d.loc[d.gross_bps.idxmax(), "mechanism_id"],
    "max_abs_t_day": d.t_stat_declustered.abs().max(),
    "cost_bps": float(d.cost_bps_applied.median()),
})).reset_index()
t5["gross / cost"] = t5.best_gross_bps / t5.cost_bps
t5.columns = ["family", "h", "best gross bps", "best cell", "max |t_day|", "cost bps", "gross/cost"]
buf.append("\n\n### T5 — THE COST WALL (T_LIQ): best GROSS edge per family vs the round trip\n")
buf.append(md(t5))

open(OUT, "w").write("\n".join(buf) + "\n")
print("wrote", OUT, os.path.getsize(OUT), "bytes")
