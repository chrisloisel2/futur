"""W8 - assemble RESULTS.json (machine-readable, one entry per mechanism, BRIEFING section-2
fields) from the four result JSONs produced by a2 / b2 / c1 / c2. Re-executable."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
A = json.load(open(os.path.join(HERE, "a2_track_a_results.json")))
B = json.load(open(os.path.join(HERE, "b2_track_b_results.json")))
C = json.load(open(os.path.join(HERE, "c1_track_c_results.json")))
D = json.load(open(os.path.join(HERE, "c2_longonly_results.json")))
P1 = json.load(open(os.path.join(HERE, "d1_placebo_results.json")))
P2 = json.load(open(os.path.join(HERE, "d2_hourbar_corrected_results.json")))
P3 = json.load(open(os.path.join(HERE, "d3_placebo_disjoint_results.json")))
E1 = json.load(open(os.path.join(HERE, "e1_selection_oos_results.json")))
E2 = json.load(open(os.path.join(HERE, "e2_nbars_robustness_results.json")))

MECH = []


def add(mid, track, family, desc, g, flags=None, extra=None):
    if g is None:
        MECH.append({"mechanism_id": mid, "track": track, "family": family,
                     "description": desc, "verdict": "DATA_LIMITED"})
        return
    eta = g.get("eta") or {}
    e = {
        "mechanism_id": mid, "track": track, "family": family, "description": desc,
        "n_raw": g.get("n_raw"),
        "n_independent_L1": g.get("n_independent_L1"),
        "n_independent_L2": g.get("n_independent_L2"),
        "n_independent_L3": g.get("n_independent_L3"),
        "gross_bps": g.get("gross_bps"),
        "net_bps": g.get("net_bps"),
        "net_bps_stress28": g.get("net_bps_stress28"),
        "excess_vs_population_bps": g.get("excess_vs_population_bps"),
        "mean_turnover": g.get("mean_turnover"),
        "cost_bps_from_turnover": g.get("cost_bps_from_turnover"),
        "t_stat_declustered": g.get("t_stat_declustered"),
        "t_stat_L3_month": g.get("t_stat_L3_month"),
        "bootstrap_ci95": g.get("bootstrap_ci95") or g.get("bootstrap_ci95_daily"),
        "year_by_year": g.get("year_by_year"),
        "ex_best_year": g.get("ex_best_year"),
        "sr_annualised": g.get("sr_annualised") or eta.get("sr_ann"),
        "n_required": g.get("n_required"),
        "event_rate": g.get("event_rate_episodes_per_week")
        or g.get("event_rate_tradingdays_per_week_last6m"),
        "eta_forward_confirmation_years": g.get("eta_forward_confirmation_years")
        or eta.get("eta_years"),
        "eta_forward_confirmation_days": g.get("eta_forward_confirmation_days")
        or eta.get("eta_days"),
        "verdict": g.get("verdict"),
        "flags": flags or [],
    }
    if extra:
        e.update(extra)
    # DISCIPLINE: a verdict may never rest on a post-hoc choice (REPORT.md section 9).
    # Any entry whose sleeve was selected after seeing results is capped below
    # VALIDATED_FOR_FORWARD, whatever the mechanical gate says.
    if (e["verdict"] == "VALIDATED_FOR_FORWARD"
            and any("REFIT" in f for f in e["flags"])):
        e["verdict_mechanical"] = "VALIDATED_FOR_FORWARD"
        e["verdict"] = "PROMISING_NEEDS_VALIDATION"
        e["verdict_override_reason"] = (
            "gate passed (ETA 2.92y) but the sleeve was chosen after seeing the Track A "
            "results; a verdict is not allowed to rest on a post-hoc selection. Needs an "
            "out-of-sample re-test on a disjoint period before it can be VALIDATED_FOR_FORWARD.")
    MECH.append(e)


# ---------------------------------------------------------------- TRACK A ---------------
for s, g in A["per_signal_gate"].items():
    add(f"A_COMPONENT::{s}", "A", "LIQ_CASCADE_EVENT_COMPONENT",
        f"single pre-event signal {s}, walk-forward sign, top-decile of causal threshold, "
        f"LONG 4h at event_time", g, ["COMPONENT_NOT_A_DELIVERABLE"])
for k, g in A["E2_composites"].items():
    add(f"A_E2::{k}", "A", "LIQ_CASCADE_EVENT_COMPOSITE",
        "equal-weighted composite of 25 pre-event signals "
        f"({'a-priori signs, ZERO free parameters' if 'APRIORI' in k else 'walk-forward signs'})",
        g, [] if "APRIORI" in k else ["WALKFORWARD_SIGN"])
for k, g in A.get("E4_weighted", {}).items():
    add(f"A_E4::{k}", "A", "LIQ_CASCADE_EVENT_COMPOSITE",
        "expanding-window weighted composite (weights strictly prior)", g,
        ["WALKFORWARD_WEIGHTS", "SELECTED_AMONG_6_VARIANTS"])
va = A["E3_vote"]
bestv = max(va.items(), key=lambda kv: (kv[1].get("t_stat_declustered") or -9))
add(f"A_E3::{bestv[0]}", "A", "LIQ_CASCADE_EVENT_COMPOSITE",
    "concordance vote, best K_min of the reported curve", bestv[1],
    ["CURVE_REPORTED_IN_EVIDENCE", "ARGMAX_OF_CURVE_IS_SELECTION"])
e6 = A["E6_walkforward_best_component"]
add("A_E6::WF_BEST_COMPONENT", "A", "BENCHMARK",
    "the DECISIVE benchmark: at each month, the component with the best trailing t-stat",
    {"n_raw": e6["n_episodes"], "n_independent_L1": e6["n_episodes"],
     "net_bps": e6["net_bps"], "t_stat_declustered": e6["t_declustered"],
     "eta": e6["eta"], "verdict": "DEAD"},
    ["BENCHMARK_FOR_E6"], {"distinct_components_picked": e6["distinct_components_picked"]})

# ---------------------------------------------------------------- TRACK B ---------------
for h in ["weekly", "daily"]:
    for s, g in B[f"{h}_per_signal"].items():
        add(f"B_COMPONENT::{s}::{h}", "B", "CROSS_SECTIONAL_COMPONENT",
            f"decile long/short on {s}, a-priori sign, {h} rebalance, cost on own turnover",
            g, ["COMPONENT_NOT_A_DELIVERABLE"],
            {"apriori_sign": g.get("apriori_sign"),
             "apriori_sign_contradicted": (g.get("net_bps", 0) < 0)})
    for k, g in B[f"{h}_E2_composites"].items():
        add(f"B_E2::{k}::{h}", "B", "CROSS_SECTIONAL_COMPOSITE",
            "equal-weighted composite of 14 cross-sectional signals", g,
            [] if "APRIORI" in k else ["WALKFORWARD_SIGN"])
    for k, g in B[f"{h}_E4_weighted"].items():
        add(f"B_E4::{k}::{h}", "B", "CROSS_SECTIONAL_COMPOSITE",
            "expanding-window weighted composite (weights strictly prior)", g,
            ["WALKFORWARD_WEIGHTS"])
    vb = B[f"{h}_E3_vote"]
    if vb:
        bv = max(vb.items(), key=lambda kv: (kv[1].get("t_stat_declustered") or -9))
        add(f"B_E3::{bv[0]}::{h}", "B", "CROSS_SECTIONAL_COMPOSITE",
            "concordance vote, best K_min of the reported curve", bv[1],
            ["CURVE_REPORTED_IN_EVIDENCE", "ARGMAX_OF_CURVE_IS_SELECTION"])
    w = B[f"{h}_E6"]["wf_best_component"]
    add(f"B_E6::WF_BEST_COMPONENT::{h}", "B", "BENCHMARK",
        "the DECISIVE benchmark: at each rebalance, the component with the best trailing t",
        {"n_raw": w["n"], "n_independent_L1": w["n"], "net_bps": w["net_bps"],
         "t_stat_declustered": w["t"], "eta": w["eta"],
         "verdict": "UNCONFIRMABLE_IN_HORIZON"}, ["BENCHMARK_FOR_E6"],
        {"distinct_components_picked": w["distinct_picks"]})

# ---------------------------------------------------------------- TRACK C ---------------
for s, g in C["C0_sleeves_on_common_window"].items():
    add(f"C0_SLEEVE::{s}", "C", "SLEEVE_ON_COMMON_DAILY_CALENDAR",
        "sleeve restated as a daily return-on-notional series on the Track-C common window",
        g, ["DAILY_CALENDAR_CONVENTION"], {"active_day_fraction": g.get("active_day_fraction")})
for k, rec in C["C2_pairs"].items():
    for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
        add(f"C2::{k}::{mode}", "C", "CROSS_BASIS_PAIR",
            f"cross-basis pair, {mode} weighting, measured rho={rec['rho']:.4f}",
            rec[mode], ["PARAMETER_FREE"] if mode == "EQUAL_CAPITAL" else ["WALKFORWARD_WEIGHTS"],
            {"measured_rho": rec["rho"],
             "best_single_sleeve_same_window": rec.get("best_single_sleeve_same_window"),
             "eta_division_factor_vs_best_single": rec.get("eta_division_factor_vs_best_single")})
for k, rec in C["C3_baskets"].items():
    for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
        fl = ["PARAMETER_FREE"] if mode == "EQUAL_CAPITAL" else ["WALKFORWARD_WEIGHTS"]
        if "SELECTED" in k:
            fl.append("SLEEVE_CHOICE_SELECTED_AFTER_SEEING_RESULTS_REFIT")
        add(f"C3::{k}::{mode}", "C", "CROSS_BASIS_BASKET",
            f"basket {rec['sleeves']}, {mode}", rec[mode], fl,
            {"pairwise_rho": rec["pairwise_rho"],
             "best_single_sleeve_same_window": rec.get("best_single_sleeve_same_window"),
             "eta_division_factor": rec.get(f"eta_division_factor_{mode}"),
             "sr_ann_predicted_equalSR_model": rec.get("sr_ann_predicted_equalSR_model")})
for s, g in D["long_only_legs"].items():
    add(f"C4_LONGLEG::{s}", "C", "LONG_LEG_ONLY",
        "LONG leg alone (top decile minus same-day eligible-universe mean) - project rule 11",
        g, ["LONG_LEG_REPORTED_SEPARATELY"])
for k, rec in D["C4_longonly_baskets"].items():
    for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
        fl = ["LONG_ONLY_POLICY_COMPLIANT"]
        fl.append("PARAMETER_FREE" if mode == "EQUAL_CAPITAL" else "WALKFORWARD_WEIGHTS")
        if "BESTOFBREED" in k:
            fl.append("SLEEVE_CHOICE_SELECTED_AFTER_SEEING_RESULTS_REFIT")
        add(f"C4::{k}::{mode}", "C", "CROSS_BASIS_BASKET_LONGONLY",
            f"long-only basket {rec['sleeves']}, {mode}", rec[mode], fl,
            {"pairwise_rho": rec["pairwise_rho"],
             "best_single_sleeve_same_window": rec.get("best_single_sleeve_same_window"),
             "eta_division_factor": rec.get(f"eta_division_factor_{mode}")})

for k, rec in P2["corrected_baskets"].items():
    for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
        fl = ["HOURBAR_CONTROLLED", "W9_BIAS_AUDIT"]
        fl.append("PARAMETER_FREE" if mode == "EQUAL_CAPITAL" else "WALKFORWARD_WEIGHTS")
        if "REFIT" in k:
            fl.append("SLEEVE_CHOICE_SELECTED_AFTER_SEEING_RESULTS_REFIT")
        add(f"D2::{k}::{mode}", "C", "CROSS_BASIS_BASKET_HOURBAR_CONTROLLED",
            f"basket {rec['sleeves']}, {mode}, Track A episodes demeaned by their own "
            f"(hour_utc x month) cell", rec[mode], fl,
            {"pairwise_rho": rec["pairwise_rho"],
             "best_single_sleeve_same_window": rec.get("best_single_sleeve_same_window"),
             "eta_division_factor": rec.get(f"eta_division_factor_{mode}")})

add("E1::BESTOFBREED_ruleS::EVAL::INVVOL_WF", "C", "CROSS_BASIS_BASKET_OUT_OF_SAMPLE",
    "BESTOFBREED rebuilt with the sleeve RULE-S picked on TRAIN, measured on the disjoint "
    "EVAL window 2025-03-01..2026-06-26 only",
    E1["C3_baskets_on_EVAL"]["BESTOFBREED_ruleS"]["INVVOL_WF"],
    ["OUT_OF_SAMPLE", "PREREGISTERED_ADDENDUM_2026-09-05b", "WALKFORWARD_WEIGHTS"])
add("E1::B_LEGS_ONLY::EVAL::INVVOL_WF", "C", "CROSS_BASIS_BASKET_OUT_OF_SAMPLE",
    "the two Track B long legs alone on the same EVAL window (no Track A sleeve)",
    E1["C3_baskets_on_EVAL"]["B_LEGS_ONLY"]["INVVOL_WF"], ["OUT_OF_SAMPLE"])
add("E1::SELECTED_SLEEVE::EVAL", "A", "LIQ_CASCADE_EVENT_COMPOSITE_OUT_OF_SAMPLE",
    "the Track A sleeve RULE-S selected on TRAIN, measured on EVAL alone",
    E1["C1_selected_sleeve_on_EVAL"], ["OUT_OF_SAMPLE"])
for nb in ("nb250", "nb280"):
    add(f"E2::BASKET_INVVOL_WF::{nb}", "C", "CROSS_BASIS_BASKET_ROBUSTNESS",
        f"BESTOFBREED with the Track B panel filtered at n_bars>={nb[2:]} of 288",
        E2["results"][nb]["BASKET_INVVOL_WF"],
        ["CLOCK_MISMATCH_ROBUSTNESS", "SLEEVE_CHOICE_SELECTED_AFTER_SEEING_RESULTS_REFIT"])

OUT = {
    "worker": "w8_signal_ensembling",
    "round": "alpha_hunt_2026-09-03_round4",
    "date_completed": "2026-09-05",
    "axis": "does combining the signals the project already owns divide the forward-"
            "confirmation ETA, which is the project's real bottleneck?",
    "headline": {
        "E1_correlation_delivered": True,
        "cross_basis_median_abs_rho": C["C1_cross_basis_correlation"]["cross_basis_median_abs_rho"],
        "cross_basis_max_abs_rho": C["C1_cross_basis_correlation"]["cross_basis_max_abs_rho"],
        "track_A_return_corr_median_abs": A["E1_return_corr_summary"]["median_abs"],
        "track_B_return_corr_median_abs_daily":
            B["daily_E1_return_correlation"]["summary"]["median_abs"],
        "E6_composite_beats_best_component": {
            "track_A": "YES - decisively (WF-best-component is DEAD, ETA 2181y; "
                       "composite ETA 8.7y a-priori-signs-free / 4.1y IC-weighted)",
            "track_B": "NO - the composite is far worse than AMIHUD_30D alone "
                       "(ETA 194y vs 8.9y daily)",
            "track_C_cross_basis": "YES but modestly - ETA divided by 1.2 to 2.3x, "
                                   "never below the 3-year bar"},
        "best_measured_eta_years_parameter_free": 4.64,
        "best_measured_eta_years_any_variant": 3.02,
        "verdict_of_the_axis": "PROMISING_NEEDS_VALIDATION",
        "verdict_missing_gate_cell": "eta_forward_confirmation still > 3 years for every "
                                     "composite; no composite reaches VALIDATED_FOR_FORWARD"},
    "correlation_matrices": {
        "track_A_score_25x25": A["E1_score_correlation"],
        "track_A_return_monthly_25x25": A["E1_return_correlation_monthly"],
        "track_B_return_weekly_14x14": B["weekly_E1_return_correlation"],
        "track_B_return_daily_14x14": B["daily_E1_return_correlation"],
        "track_B_score_daily_14x14": B["daily_E1_score_correlation"],
        "track_C_cross_basis": C["C1_cross_basis_correlation"]},
    "effective_independent_bets": {
        "track_A": A["E1_effective_independent_bets"],
        "track_B_weekly": {"K": 14,
                           "ENB_eig_entropy": B["weekly_E1_return_correlation"]["ENB_eig_entropy"],
                           "mean_rho": B["weekly_E1_return_correlation"]["summary"]["mean_rho"]},
        "track_B_daily": {"K": 14,
                          "ENB_eig_entropy": B["daily_E1_return_correlation"]["ENB_eig_entropy"],
                          "mean_rho": B["daily_E1_return_correlation"]["summary"]["mean_rho"]}},
    "duplicates_for_portfolio_dedup": {
        "track_B": {k: v["classification"] for k, v in B["daily_E5_orthogonalisation"].items()},
        "track_A": {k: v["classification"] for k, v in A["E5_orthogonalisation"].items()}},
    "w9_bias_audit": {
        "question": "does a calendar-day-level control applied to intraday observations "
                    "inflate this worker's result, as W9 found on his axis?",
        "exposure": "NONE by design. Track A uses no day-level control at all (its benchmark "
                    "is the global unconditional mean of the same evaluable window, reported "
                    "as excess_vs_population_bps). Track B legs are close-to-close daily "
                    "returns judged against the SAME-DAY cross-section over an identical clock "
                    "interval for every symbol, so a random score has exactly zero expected "
                    "excess by construction.",
        "placebo_random_signal_same_population": {
            k: {"real_day_net_bps": v["real"]["day_net_bps"],
                "placebo_day_net_bps": v["placebo_P1_unstratified"]["day_net_bps"]["mean"],
                "p_value_one_sided": v["placebo_P1_unstratified"]["p_value_one_sided"]}
            for k, v in P1.items() if isinstance(v, dict) and "real" in v},
        "placebo_hour_x_month_matched_DISJOINT": {
            k: {"real_day_net_bps": v["real_day_net_bps"],
                "placebo_day_net_bps": v["disjoint_placebo_day_net_bps"]["mean"],
                "placebo_max_over_400_draws": v["disjoint_placebo_day_net_bps"]["max"],
                "share_of_real_edge_explained": v["share_of_real_day_edge_explained"],
                "p_value_one_sided": v["p_value_one_sided"]}
            for k, v in P3.items()},
        "discarded_first_attempt": {
            "what": "d1 placebo P2 drew from the WHOLE (hour x month) cell and appeared to "
                    "reproduce 23-27% of the edge",
            "why_discarded": "measured expected overlap with the real selection = 34% "
                             "(cells are small: median 25 events) - it was partly redrawing "
                             "the real signal's own episodes. Superseded by the disjoint "
                             "placebo in d3.",
            "kept_in_evidence": "d1_placebo_results.json"},
        "hourbar_controlled_headline": {
            "sleeve_diagnostic": P2["diagnostic_track_A_sleeves"],
            "roadmap_after_correction": P2["roadmap_after_correction"]},
        "conclusion": "NOT CONTAMINATED. A disjoint placebo matched on hour-of-day and month "
                      "earns -2.7 bps/day where the real Track A sleeve earns +27.0 "
                      "(p<0.0025, 400 draws); applying the hour-bar control directly moves the "
                      "headline composite from +12.95 to +12.73 bps and its ETA from 4.64 to "
                      "4.55 years. The roadmap number is unchanged (extra sleeve SR 1.89 vs 1.93)."},
    "selection_out_of_sample_test": {
        "protocol": "PREREGISTRATION.md ADDENDUM 2026-09-05 (b), written before any result",
        "question": "does the sleeve-selection rule of BESTOFBREED hold on a period that did "
                    "not serve to make it?",
        "train": E1["train"], "eval": E1["eval"],
        "rule_S": E1["rule_S"],
        "rule_S_pick_on_TRAIN": E1["rule_S_pick_on_TRAIN"],
        "pick_agrees_with_original_post_hoc_choice": E1["pick_agrees_with_original"],
        "train_sr_ann_by_candidate": E1["train_sr_ann_by_candidate"],
        "eval_sr_ann_by_candidate": E1["eval_sr_ann_by_candidate"],
        "C1_selected_sleeve_pays_OOS": {
            "required": "net_bps>0 and t_declustered>=2.0",
            "measured_net_bps": E1["C1_selected_sleeve_on_EVAL"]["net_bps"],
            "measured_t": E1["C1_selected_sleeve_on_EVAL"]["t_stat_declustered"],
            "pass": False},
        "C2_selection_beats_noise": {
            "required": "EVAL SR_ann of the picked sleeve > placebo p90",
            **E1["C2_placebo"]["selected_sleeve_SR_ann_EVAL"], "pass": True},
        "C3_three_year_claim_survives": {
            "required": "basket EVAL eta < 3.0 years",
            "measured_eta_years":
                E1["C3_baskets_on_EVAL"]["BESTOFBREED_ruleS"]["INVVOL_WF"][
                    "eta_forward_confirmation_years"],
            "in_sample_eta_years": 2.92, "pass": False},
        "C4_clock_mismatch_not_the_edge": {
            "required": "net_bps retained >= 50% at n_bars>=280",
            "measured_retained": E2["net_bps_retained_at_nb280"],
            "symbol_days_lost": (E2["panel_stats_nb250"]["n_symbol_days"]
                                 - E2["panel_stats_nb280"]["n_symbol_days"]),
            "pass": E2["C4_pass"]},
        "marginal_contribution_of_track_A_sleeve_SR_ann": {
            "train": 1.55, "eval": 0.27,
            "note": "basket SR 3.42->3.18 while the Track B legs alone went 1.87->2.91; the "
                    "in-sample sub-3-year result was largely a Track A effect that does not "
                    "replicate"},
        "power": E1["power"],
        "genuinely_untouched_window": {
            "days_available": 27, "days_required": 1066, "status": "DATA_LIMITED"},
        "verdict": "PROMISING_NEEDS_VALIDATION",
        "verdict_reason": "NOT VALIDATED_FOR_FORWARD: C3 fails (EVAL eta 3.11y vs 2.92y in "
                          "sample) and C1 fails on significance (t 1.75 < 2.0). C2 and C4 pass, "
                          "so this is NOT a selection artefact - the rule reproduces the pick "
                          "from TRAIN alone, beats a noise argmax at p=0.0075 and ranks best "
                          "on EVAL too. The edge is real but weaker than discovery suggested, "
                          "and the 3-year bar is not cleared out of sample.",
        "declared_departure_from_preregistration": "the prewritten C1-fail branch said I would "
            "call this a 'selection artefact'; C2, written to adjudicate exactly that, rejects "
            "the wording. The VERDICT is unchanged under either reading."},
    "mechanisms": MECH,
}
with open(os.path.join(ROOT, "RESULTS.json"), "w") as f:
    json.dump(OUT, f, indent=1, default=str)
print("wrote RESULTS.json with", len(MECH), "mechanism entries")
from collections import Counter
print(Counter(m.get("verdict") for m in MECH))
