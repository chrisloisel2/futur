#!/usr/bin/env python3
"""W6 round-4 PHASE 5: assemble the worker-level RESULTS.json deliverable."""
import json, os, math
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.abspath(os.path.join(HERE, "..", "results"))
WDIR = os.path.abspath(os.path.join(HERE, "..", ".."))
K = 7.848932285603855


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


cells = json.load(open(os.path.join(RES, "RESULTS_CELLS.json")))
inv = json.load(open(os.path.join(RES, "INVENTORY.json")))
d2 = json.load(open(os.path.join(RES, "D2_ORTHOGONALITY_M8.json")))
d1 = pd.read_csv(os.path.join(RES, "D1_BREADTH_EFFICIENCY.csv"))
fam = pd.read_csv(os.path.join(RES, "FAMILY_BREAKEVEN_T_LIQ.csv"))

df = pd.DataFrame(cells)
df["eta_sort"] = df["eta_forward_confirmation_years"].fillna(np.inf)
df = df.sort_values(["eta_sort", "tier", "mechanism_id"], kind="stable")

GATE = ["mechanism_id", "family", "horizon_h", "tier", "construct", "mirror_of",
        "n_raw", "n_independent_L1", "n_independent_L2_days", "n_independent_L3_weeks",
        "gross_bps", "net_bps", "net_bps_stress28", "net_bps_daymean",
        "cost_bps_applied", "cost_bps_stress_applied", "mean_turnover_per_rebalance",
        "net_bps_flat14", "net_bps_flat28",
        "sd_episode_L1_bps", "sd_daymean_bps", "sd_weekmean_bps",
        "t_stat_declustered", "t_stat_L1_episode", "t_stat_week", "t_stat_day_plain",
        "bootstrap_ci95_net_bps", "bootstrap_ci95_net_bps_block1",
        "year_by_year", "best_year", "ex_best_year_net_bps",
        "n_required_episode", "n_required_days", "n_required_weeks",
        "event_rate_L1_per_week_recent6m", "event_rate_L1_per_day_recent6m",
        "day_coverage_recent6m",
        "eta_forward_confirmation_days", "eta_forward_confirmation_years",
        "eta_stress28_years", "eta_L3week_years", "eta_at_zero_cost_years",
        "sharpe_net_ann", "sharpe_gross_ann",
        "net_bps_min_for_1y_confirm", "net_bps_min_for_2y_confirm",
        "net_bps_min_for_3y_confirm", "max_sustainable_cost_bps", "cost_bps_for_1y_confirm",
        "capacity_usd_estimate", "median_basket_size_per_leg", "n_symbols",
        "verdict", "verdict_reason", "insufficient"]

mech = [{k: c.get(k) for k in GATE if k in c or k in ("verdict", "verdict_reason")}
        for c in df.to_dict("records")]

scored = df[df.insufficient != True]
out = {
    "worker": "W6_HIGH_FREQUENCY_EPISODES",
    "round": "alpha_hunt_2026-09-03_round4",
    "report_written": "2026-09-05",
    "session_note": "run interrupted 2026-09-03 after the inventory; grid completed 2026-09-05",
    "axis": "attack the DENOMINATOR of ETA = n_required / event_rate (independent-episode rate), "
            "not the numerator (net_bps). Primary sort key of every table is ETA ascending.",
    "data": {
        "source_panel": "/home/qbee/futur-data-v2/data_v2/normalized/event_feature_panel/venue=binance",
        "grid": "5m PIT panel collapsed to hourly decision points (minute==0)",
        "rows_hourly_all": 8787448,
        "rows_used_T_ALL": 7059253, "rows_used_T_LIQ": 3860562, "rows_used_T_DEEP": 703885,
        "symbols": 307, "span": "2020-01-01 .. 2026-08-01",
        "pit_contract": "research_available_at = timestamp + 305s; entry at close of H+10min; "
                        "forward labels built by LEAD only; BTC/ETH excluded (hedge factors); "
                        "all returns are causal beta-hedged residuals",
        "pit_status": "PIT_VERIFIED (panel contract) — see REPORT.md sec.9 for the residual caveat"
    },
    "cost_convention": {
        "directional": "flat 14 bps round trip per episode (28 stress); no netting credit",
        "cross_sectional": "turnover-weighted: 7 bps one-way per unit gross notional traded, "
                           "cost = 7 * mean(sum_i |w_i(t)-w_i(t-h)|) on the non-overlapping schedule; "
                           "stress = 2x that. Measured turnover 1.14-1.82 -> 8.0-12.7 bps.",
        "flat14_also_reported": True
    },
    "headline_findings": [
        "IDENTITY: with the preregistered 50% haircut, 80% power, 5% two-sided, a mechanism is "
        "confirmable forward within N years IFF its annualised NET Sharpe on the traded series is "
        ">= 5.60/sqrt(N). ETA is a Sharpe statement, not an event-count statement.",
        "The independent-episode rate is NOT the project's binding constraint. Intraday families "
        "deliver 50-580 L1-independent episodes/week (vs ~1-10/week for the weekly alphas), and the "
        "hourly cross-sectional family needs only +3.05 net bps per rebalance to confirm in one year "
        "(vs +16 to +45 for single-symbol intraday, and the ~+100 bps the weekly alphas need).",
        "The binding constraint is the COST WALL. Only 18 of 258 scored cells produce a GROSS edge "
        "above the 14 bps round trip, and 13 of those 18 are the same volume-shock mechanism. "
        "Every high-frequency family shows a statistically overwhelming gross signal "
        "(day-clustered |t| = 3-9 on 1700-2300 days) that is 3-15x SMALLER than its execution cost.",
        "The one mechanism clearing 28 bps stress with a good episode rate "
        "(M8_VOLSHOCK_REVERSION_6x, T_ALL, +35.8 net28 bps, 74 indep episodes/week, positive every "
        "year 2020-2026) still has ETA 5.2-5.8 y -> UNCONFIRMABLE_IN_HORIZON; and diagnostic D2 shows "
        "its entire edge sits inside the project's already-known liquidation-cascade family.",
        "NO cell reaches VALIDATED_FOR_FORWARD. Best ETA in the whole 261-cell grid is 5.21 y."
    ],
    "eta_sharpe_identity": {
        "formula": "eta_years = 31.396 / (annualised_net_sharpe^2 / 365) / 365  <=>  "
                   "sharpe_required(N years) = 2*sqrt(K/365)*sqrt(365)/sqrt(N) = 5.60/sqrt(N)",
        "K_power": K, "haircut": 0.5, "alpha": 0.05, "power": 0.80,
        "sharpe_required_by_eta": {str(y): 5.5987 / math.sqrt(y) for y in (0.5, 1, 2, 3, 5, 10, 17)}
    },
    "verdict_counts_all_tiers": df.verdict.value_counts().to_dict(),
    "verdict_counts_T_LIQ": df[df.tier == "T_LIQ"].verdict.value_counts().to_dict(),
    "episode_rate_inventory": inv,
    "family_breakeven_T_LIQ": fam.to_dict("records"),
    "diagnostic_D1_breadth_efficiency": {
        "definition": "k_eff = (sd_episode_L1/sd_daymean)^2 = effective independent episodes per "
                      "calendar day; redundancy = raw_episodes_per_day / k_eff",
        "by_family": d1.groupby(["family", "horizon_h"]).agg(
            raw_per_day=("raw_episodes_per_day", "median"),
            k_eff=("k_eff_independent_per_day", "median"),
            redundancy=("redundancy_factor", "median"),
            sd_episode_bps=("sd_episode_bps", "median"),
            sd_daymean_bps=("sd_daymean_bps", "median"),
            net_bps_min_1y=("net_bps_min_1y", "median")).reset_index().to_dict("records")
    },
    "diagnostic_D2_orthogonality_M8_vs_known_cascade": d2,
    "mechanisms": mech,
}

with open(os.path.join(WDIR, "RESULTS.json"), "w") as f:
    json.dump(clean(out), f, indent=1)
print("wrote", os.path.join(WDIR, "RESULTS.json"),
      os.path.getsize(os.path.join(WDIR, "RESULTS.json")), "bytes;", len(mech), "mechanism cells")
