#!/usr/bin/env python3
"""W6 round-4 PHASE 3: assemble RESULTS.json and the report tables.

Primary sort key is `eta_forward_confirmation_years` ASCENDING (worker mandate),
never net_bps.

Also derives, from the stored gate fields (no re-fit, no new data pass):
  * `sharpe_net_ann`  = net_bps_daymean / sd_daymean_bps * sqrt(365 * day_coverage)
  * `eta_at_zero_cost_years` : the ETA the mechanism WOULD have if execution were
    free. This isolates "is there any signal at all" from "is it payable".
  * `max_sustainable_cost_bps` = gross_bps  (the cost at which net hits zero)
  * `cost_bps_for_1y_confirm`  = gross_bps_daymean - net_bps_min_for_1y_confirm
    i.e. the round-trip cost the project would have to achieve for this
    mechanism to become confirmable inside one calendar year.
"""
import json, os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.abspath(os.path.join(HERE, "..", "results"))
K_POWER = 7.848932285603855
HAIRCUT = 0.5


def n_required(effect, sd):
    if effect is None or sd is None or not np.isfinite(effect) or not np.isfinite(sd) \
       or effect <= 0 or sd <= 0:
        return np.inf
    return K_POWER * (sd / (HAIRCUT * effect)) ** 2


def main():
    cells = json.load(open(os.path.join(RES, "MECHANISMS.json")))
    rows = []
    for c in cells:
        r = dict(c)
        gd = c.get("gross_bps_daymean"); sd = c.get("sd_daymean_bps")
        cov = c.get("day_coverage_recent6m") or 0.0
        nd = c.get("net_bps_daymean")
        if gd is not None and sd:
            r["eta_at_zero_cost_years"] = float(n_required(gd, sd) / max(cov, 1e-9) / 365.25)
            r["cost_bps_for_1y_confirm"] = float(gd - c["net_bps_min_for_1y_confirm"])
            r["sharpe_net_ann"] = float(nd / sd * np.sqrt(365.0 * cov)) if nd is not None else None
            r["sharpe_gross_ann"] = float(gd / sd * np.sqrt(365.0 * cov))
        else:
            r["eta_at_zero_cost_years"] = None
            r["cost_bps_for_1y_confirm"] = None
            r["sharpe_net_ann"] = None
            r["sharpe_gross_ann"] = None
        r["max_sustainable_cost_bps"] = c.get("gross_bps")
        rows.append(r)

    df = pd.DataFrame(rows)
    df["eta_sort"] = df["eta_forward_confirmation_years"].fillna(np.inf)
    df = df.sort_values(["eta_sort", "tier", "mechanism_id"], kind="stable")

    with open(os.path.join(RES, "RESULTS_CELLS.json"), "w") as f:
        json.dump(df.drop(columns=["eta_sort"]).to_dict("records"), f, indent=1, default=float)

    # ---------- compact table, ETA ascending ----------
    cols = ["mechanism_id", "family", "horizon_h", "tier", "verdict", "n_raw",
            "n_independent_L1", "n_independent_L2_days", "n_independent_L3_weeks",
            "gross_bps", "net_bps", "net_bps_stress28", "t_stat_declustered",
            "event_rate_L1_per_week_recent6m", "eta_forward_confirmation_years",
            "eta_at_zero_cost_years", "sharpe_net_ann", "sharpe_gross_ann",
            "net_bps_min_for_1y_confirm", "cost_bps_for_1y_confirm",
            "capacity_usd_estimate", "ex_best_year_net_bps", "best_year"]
    cols = [c for c in cols if c in df.columns]
    t = df[cols].copy()
    t.to_csv(os.path.join(RES, "GRID_BY_ETA.csv"), index=False)
    print(f"wrote GRID_BY_ETA.csv  ({len(t)} cells)")

    print("\n=== verdict counts (all tiers) ===")
    print(df.verdict.value_counts().to_string())
    print("\n=== verdict counts, T_LIQ (primary tier) ===")
    print(df[df.tier == "T_LIQ"].verdict.value_counts().to_string())

    print("\n=== 25 lowest-ETA cells, all tiers ===")
    show = ["mechanism_id", "horizon_h", "tier", "verdict", "n_independent_L1",
            "gross_bps", "net_bps", "net_bps_stress28", "t_stat_declustered",
            "event_rate_L1_per_week_recent6m", "eta_forward_confirmation_years",
            "eta_at_zero_cost_years", "sharpe_net_ann"]
    pd.set_option("display.width", 260); pd.set_option("display.max_columns", 40)
    print(t[show].head(25).to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    print("\n=== best 20 by ZERO-COST ETA (is there any signal at all?) ===")
    z = df.sort_values(df["eta_at_zero_cost_years"].fillna(np.inf).name if False else "eta_at_zero_cost_years")
    print(z[show].head(20).to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    # ---------- family break-even summary (T_LIQ) ----------
    liq = df[(df.tier == "T_LIQ") & (~df.n_raw.isna()) & (df.get("insufficient") != True)]
    fam = liq.groupby(["family", "horizon_h"]).agg(
        n_cells=("mechanism_id", "size"),
        median_rate_L1_wk=("event_rate_L1_per_week_recent6m", "median"),
        median_n_L1=("n_independent_L1", "median"),
        median_days=("n_independent_L2_days", "median"),
        best_gross_bps=("gross_bps", "max"),
        median_sd_daymean=("sd_daymean_bps", "median"),
        net_bps_min_1y=("net_bps_min_for_1y_confirm", "median"),
        net_bps_min_2y=("net_bps_min_for_2y_confirm", "median"),
        median_capacity_usd=("capacity_usd_estimate", "median"),
    ).reset_index()
    fam["gross_bps_needed_1y"] = fam.net_bps_min_1y + 14.0
    fam["gross_shortfall_1y"] = fam.best_gross_bps - fam.gross_bps_needed_1y
    fam.to_csv(os.path.join(RES, "FAMILY_BREAKEVEN_T_LIQ.csv"), index=False)
    print("\n=== FAMILY BREAK-EVEN (T_LIQ): net bps needed to confirm in <1y ===")
    print(fam.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    # ---------- the ETA <-> Sharpe identity ----------
    print("\n=== ETA / Sharpe identity (exact, from the preregistered formula) ===")
    for y in (0.5, 1, 2, 3, 5, 10, 17):
        ir = np.sqrt(4 * K_POWER / (365.0 * y))
        print(f"  confirmable in {y:>4} y  <=>  annualised NET Sharpe >= {ir*np.sqrt(365):.2f}")


if __name__ == "__main__":
    main()
