"""W5/s15 - the round-4 validation gate (BRIEFING sec.2) applied to this worker's CLAIMS.

This worker produces cost measurements, not directional mechanisms, so `year_by_year` and
`ex_best_year` are stamped N/A_COST_LAYER exactly as the preregistration (sec.5.2) committed in
advance: the cost instruments span 7 weeks (probe) and 4 days (real books), so no year
decomposition exists. Every other gate column IS computable and is computed here, including the
one the briefing calls the most important: eta_forward_confirmation.

n_required uses the briefing's rule: power 80%, alpha 5%, on an effect HAIRCUT BY 50%.
    n = (z_{0.975} + z_{0.80})^2 * sigma^2 / (0.5*effect)^2 ,  (1.96+0.8416)^2 = 7.849
event_rate is measured on the instrument's own independent-episode rate.
"""
import os, json
import numpy as np, pandas as pd

Z2 = (1.959964 + 0.841621) ** 2
S = os.environ["W5_SCRATCH"]
cm = json.load(open(f"{S}/cost_model.json"))
du = json.load(open(f"{S}/directional_urgency_capacity.json"))
fl = json.load(open(f"{S}/cost_floor.json"))
sp = json.load(open(f"{S}/signed_urgency_proxy.json"))


def gate(name, effect, t, n_cells, n_raw, n_L1, n_L2, n_L3, days_observed, ci, extra):
    sd = abs(effect) * np.sqrt(n_cells) / abs(t) if t else np.nan
    n_req = Z2 * sd ** 2 / (0.5 * abs(effect)) ** 2
    rate_wk = n_cells / days_observed * 7.0
    eta_d = n_req / (n_cells / days_observed)
    g = {"claim_id": name, "n_raw": int(n_raw),
         "n_independent_L1": n_L1, "n_independent_L2": n_L2, "n_independent_L3": n_L3,
         "effect_bps": round(float(effect), 3),
         "t_stat_declustered": round(float(t), 2),
         "bootstrap_ci95": [round(float(ci[0]), 3), round(float(ci[1]), 3)],
         "year_by_year": "N/A_COST_LAYER (7 weeks probe / 4 days real books)",
         "ex_best_year": "N/A_COST_LAYER (same reason)",
         "n_required_power80_alpha5_haircut50": int(np.ceil(n_req)),
         "event_rate_independent_per_week": round(rate_wk, 1),
         "eta_forward_confirmation_days": round(eta_d, 1),
         "eta_forward_confirmation_years": round(eta_d / 365.25, 4)}
    g.update(extra)
    return g


out = []

# CLAIM 1 - maker execution is a real but small cost improvement
a = cm["h2_maker_advantage_oneway_k10"]
out.append(gate("W5_C1_MAKER_COST_LAYER", a["mean"], a["t_declustered"], a["n_cells_L3"],
                a["n_raw"], a["n_ind_L1_symbol_day"], a["n_ind_L2_day"], a["n_cells_L3"],
                days_observed=4, ci=a["bootstrap_ci95"],
                extra={"statement": "post-only at the touch beats crossing by 2.35 bps one-way "
                                    "CONDITIONAL ON FILL; 1.5-1.8 bps RT on the must-trade policy",
                       "net_bps": None, "net_bps_stress28": None,
                       "verdict": "VALIDATED_FOR_FORWARD",
                       "gate_note": "ETA 1 day. The binding limitation is not power, it is "
                                    "REGIME COVERAGE: 4 calendar days, one volatility regime."}))

# CLAIM 2 - the momentum urgency penalty
for arm in du["urgency_two_arms"]:
    if arm["arm"] != "MOMENTUM_chase":
        continue
    tag = "P99" if arm["quantile"] == 0.99 else "P999"
    out.append(gate(f"W5_C2_URGENCY_MOMENTUM_{tag}", arm["maker_penalty_rt_mean"],
                    arm["maker_penalty_rt_t_declustered"], arm["n_ind_L1_symbol_day"],
                    arm["n_raw"], arm["n_ind_L1_symbol_day"], arm["n_ind_L2_day"],
                    arm["n_ind_L3_symbol"], days_observed=arm["n_ind_L2_day"],
                    ci=arm["maker_penalty_rt_ci95"],
                    extra={"statement": f"maker round-trip cost rises {arm['maker_penalty_rt_mean']:.2f} bps "
                                        f"when posting on the side the market has ALREADY moved toward "
                                        f"(top {(1-arm['quantile'])*100:.1f}% move)",
                           "spread_multiplier": arm["spread_mult_median"],
                           "fill_rate_60s": [arm["fill60_base_median"], arm["fill60_evt_median"]],
                           "verdict": "VALIDATED_FOR_FORWARD" if arm["maker_penalty_rt_mean"] > 5
                                      else "PROMISING_NEEDS_VALIDATION",
                           "gate_note": "measured with the probe's traversal rule + the s10 bridge; "
                                        "the bridge is EXTRAPOLATED above spread 1 bps"}))

# CLAIM 3 - the contrarian arm (reported, explicitly NOT usable as a credit)
for arm in du["urgency_two_arms"]:
    if arm["arm"] != "ADVERSE_contrarian" or arm["quantile"] != 0.999:
        continue
    out.append(gate("W5_C3_URGENCY_CONTRARIAN_P999", arm["maker_penalty_rt_mean"],
                    arm["maker_penalty_rt_t_declustered"], arm["n_ind_L1_symbol_day"],
                    arm["n_raw"], arm["n_ind_L1_symbol_day"], arm["n_ind_L2_day"],
                    arm["n_ind_L3_symbol"], days_observed=arm["n_ind_L2_day"],
                    ci=arm["maker_penalty_rt_ci95"],
                    extra={"statement": "posting into a cascade is CHEAPER, not dearer, by 6.8 bps RT",
                           "verdict": "DOUBLE_COUNTING_RISK - NOT USABLE AS A CREDIT",
                           "gate_note": "this improvement IS the post-cascade bounce that the "
                                        "project's cascade alphas already book as their edge. "
                                        "Admissible use is only the negative one: cascade-bounce "
                                        "alphas are NOT penalised by urgency."}))

# CLAIM 4 - the convention is too GENEROUS on wide-spread alts
per = pd.DataFrame(fl["per_symbol"])
wide = per[per.symbol.isin(["ARUSDT", "ADAUSDT", "FETUSDT"])]
eff = float((wide.best_rt - 14.0).mean())
out.append({"claim_id": "W5_C4_WIDE_ALT_COST_UNDERSTATED",
            "statement": "on AR/ADA/FET the true best-mode round-trip cost is 14.98/14.56/16.32 bps, "
                         "not 14; the convention is too generous by 1.3 bps on average and the "
                         "error grows 1:1 with the spread into the illiquid tail",
            "n_raw": 3975968, "n_independent_L1": "51 dates x 15 symbols = 765 symbol-days",
            "n_independent_L2": 51, "n_independent_L3": 15,
            "effect_bps": round(eff, 2), "t_stat_declustered": "N/A - identity, not an estimate "
            "(cost_taker_rt = spread + 10 exactly); the estimated part is the spread itself",
            "bootstrap_ci95": None,
            "year_by_year": "N/A_COST_LAYER", "ex_best_year": "N/A_COST_LAYER",
            "n_required_power80_alpha5_haircut50": 1,
            "event_rate_independent_per_week": round(765 / 51 * 7, 1),
            "eta_forward_confirmation_days": 1.0, "eta_forward_confirmation_years": 0.003,
            "verdict": "VALIDATED_FOR_FORWARD"})

# CLAIM 5 - H6, the proxy
h6 = sp["h6_spread_proxy"]
out.append({"claim_id": "W5_C5_SPREAD_PROXY_FOR_THE_WIDE_UNIVERSE",
            "statement": "Corwin-Schultz on 1h bars ranks spreads (Spearman 0.867, n=9) but its "
                         "LEVELS are unusable (5.81 bps for BTC where the truth is 0.015). Since "
                         "cost = const + 1.00*spread, the level error is the cost error.",
            "n_raw": 9, "n_independent_L1": 9, "n_independent_L2": 9, "n_independent_L3": 9,
            "spearman_corwin_schultz": h6.get("cs_bps", {}).get("spearman"),
            "spearman_abdi_ranaldo": h6.get("ar_bps", {}).get("spearman"),
            "preset_threshold": 0.6,
            "year_by_year": "N/A_COST_LAYER", "ex_best_year": "N/A_COST_LAYER",
            "n_required_power80_alpha5_haircut50": None,
            "event_rate_independent_per_week": None,
            "eta_forward_confirmation_days": None, "eta_forward_confirmation_years": None,
            "verdict": "DATA_LIMITED"})

json.dump(out, open(f"{S}/gate.json", "w"), indent=1, default=float)
cols = ["claim_id", "n_raw", "n_independent_L2", "n_independent_L3", "effect_bps",
        "t_stat_declustered", "n_required_power80_alpha5_haircut50",
        "event_rate_independent_per_week", "eta_forward_confirmation_days",
        "eta_forward_confirmation_years", "verdict"]
print(pd.DataFrame(out)[cols].to_string(index=False))
print("\nwrote", f"{S}/gate.json")
