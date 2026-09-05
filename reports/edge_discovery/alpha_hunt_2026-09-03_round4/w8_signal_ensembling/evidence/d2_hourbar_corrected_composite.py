"""W8 / audit - the W9 CORRECTION applied to this worker's headline.

d1 showed that an hour x month stratified placebo reproduces 23-27% of the Track A sleeve's
day-level edge: the sleeve's selection is concentrated in hours that carry unconditional drift.
This script applies the analogue of W9's fix - control at the HOUR bar, not the calendar day -
by demeaning every selected episode with the mean net return of its own (hour_utc x month)
cell computed over the whole evaluable population, then re-runs the Track C combination and
the full BRIEFING section-2 gate on the corrected sleeves.

Track B legs need no correction: they are close-to-close daily returns judged against the
same-day cross-section over an identical clock interval for every symbol.
"""
import json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from a1_track_a_ensemble import causal_quantile_threshold, decluster_L1, _utc, COST_RT, OUTDIR
from c1_track_c_cross_basis import align, combine, gate_daily, verdict, COST_STRESS
from d1_placebo_hour_stratified import build_scores

VARIANTS = {"A_EW_WALKFORWARD_q90": ("EW_WALKFORWARD", 0.90),
            "A_CONFIDENCE_IC_WF_q80": ("CONFIDENCE_IC_WF", 0.80)}


def corrected_sleeves(cost):
    df, mo, y, scores = build_scores()
    hour = df["event_time"].dt.hour.values
    rows, diag = [], {}
    for label, (name, q) in VARIANTS.items():
        sc = scores[name]
        thr = causal_quantile_threshold(sc, mo, q)
        pop = np.isfinite(thr)
        mask = np.isfinite(sc) & pop & (sc >= thr)
        net = y - cost
        cell = pd.DataFrame({"h": hour, "m": mo, "x": net})
        mu = cell[pop].groupby(["h", "m"])["x"].mean()
        key = pd.MultiIndex.from_arrays([hour, mo])
        base = pd.Series(mu.reindex(key).values, index=np.arange(len(df)))
        base = base.fillna(float(net[pop].mean())).values     # empty cell -> population mean
        t = df["event_time"].values[mask]
        keep = decluster_L1(t, df["symbol"].values[mask])
        d = pd.to_datetime(_utc(t[keep]).date)
        raw = pd.Series(net[mask][keep]).groupby(d).mean()
        adj = pd.Series((net - base)[mask][keep]).groupby(d).mean()
        rows.append(pd.DataFrame({"day": adj.index, "sleeve": label + "_HOURADJ", "track": "A",
                                  "gross_bps": adj.values + cost, "turnover": np.nan,
                                  "n_episodes": 1, "cost_model": "PER_EPISODE"}))
        diag[label] = {"raw_day_net_bps": float(raw.mean()),
                       "hourbar_adjusted_day_net_bps": float(adj.mean()),
                       "removed_bps": float(raw.mean() - adj.mean()),
                       "removed_share": float(1 - adj.mean() / raw.mean()),
                       "n_active_days": int(len(adj))}
    return pd.concat(rows, ignore_index=True), diag


def main():
    lo14, diag = corrected_sleeves(COST_RT)
    lo28, _ = corrected_sleeves(COST_STRESS)
    b14 = pd.read_parquet(os.path.join(OUTDIR, "c2_longonly_sleeves.parquet"))
    b14["day"] = pd.to_datetime(b14["day"])
    df14 = pd.concat([b14, lo14], ignore_index=True)
    df28 = pd.concat([b14, lo28], ignore_index=True)

    res = {"diagnostic_track_A_sleeves": diag,
           "method": "each selected episode demeaned by the mean net return of its own "
                     "(hour_utc x calendar-month) cell over the whole evaluable population; "
                     "Track B legs untouched (same-day cross-section, identical clock).",
           "caveat": "the cell means are full-sample (a control, not a signal) - this removes "
                     "slightly MORE than a causal estimate would, so the corrected numbers are "
                     "conservative."}

    baskets = {
        "HEADLINE_corrected": ["A_EW_WALKFORWARD_q90_HOURADJ", "BLO_AMIHUD_30D", "BLO_MOM_30D"],
        "BESTOFBREED_corrected_REFIT": ["A_CONFIDENCE_IC_WF_q80_HOURADJ", "BLO_AMIHUD_30D",
                                        "BLO_MOM_30D"],
        "B_ONLY_no_track_A": ["BLO_AMIHUD_30D", "BLO_MOM_30D"],
    }
    out = {}
    for name, sls in baskets.items():
        M, _ = align(sls, df14, COST_RT)
        Ms, _ = align(sls, df28, COST_STRESS)
        opy = len(M) / max((M.index.max() - M.index.min()).days / 365.25, 1e-9)
        rec = {"sleeves": sls, "n_days": int(len(M)),
               "pairwise_rho": {f"{i}~{j}": float(M[i].corr(M[j]))
                                for k, i in enumerate(sls) for j in sls[k + 1:]}}
        singles = {}
        for s_ in sls:
            g = gate_daily(M[s_], s_, opy, Ms[s_])
            if g:
                g["verdict"] = verdict(g)
                singles[s_] = g
        rec["singles"] = singles
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            g = gate_daily(combine(M, mode), f"{name}::{mode}", opy, combine(Ms, mode))
            if g:
                g["verdict"] = verdict(g)
            rec[mode] = g
        bs = max(singles.values(), key=lambda x: (x["sr_annualised"] or -9)) if singles else None
        rec["best_single_sleeve_same_window"] = {
            "label": bs["label"], "sr_annualised": bs["sr_annualised"],
            "eta_years": bs["eta_forward_confirmation_years"]} if bs else None
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            g = rec[mode]
            if g and bs and bs["eta_forward_confirmation_years"] and g["eta_forward_confirmation_years"]:
                rec[f"eta_division_factor_{mode}"] = float(
                    bs["eta_forward_confirmation_years"] / g["eta_forward_confirmation_years"])
        out[name] = rec
    res["corrected_baskets"] = out
    # what the roadmap number becomes
    sr = out["HEADLINE_corrected"]["EQUAL_CAPITAL"]["sr_annualised"]
    srw = out["HEADLINE_corrected"]["INVVOL_WF"]["sr_annualised"]
    res["roadmap_after_correction"] = {
        "sr_needed_for_eta_3y": 3.2353,
        "extra_sleeve_sr_needed_equal_capital": float(np.sqrt(max(3.2353 ** 2 - sr ** 2, 0))),
        "extra_sleeve_sr_needed_invvol_wf": float(np.sqrt(max(3.2353 ** 2 - srw ** 2, 0)))}

    with open(os.path.join(OUTDIR, "d2_hourbar_corrected_results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(json.dumps(diag, indent=1))
    for name, rec in out.items():
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            g = rec[mode]
            print(f"{name:30s} {mode:14s} n={g['n_raw']:5d} net={g['net_bps']:+6.2f} "
                  f"n28={g['net_bps_stress28']:+6.2f} t={g['t_stat_declustered']:5.2f} "
                  f"tL3={g['t_stat_L3_month']:5.2f} SR={g['sr_annualised']:.2f} "
                  f"ETA={g['eta_forward_confirmation_years']:.2f}y exb={g['ex_best_year']['net_bps']:+6.2f} "
                  f"CI={[round(c,2) for c in g['bootstrap_ci95']]} {g['verdict']}")
    print("roadmap:", json.dumps(res["roadmap_after_correction"], indent=1))
    print("wrote d2_hourbar_corrected_results.json")


if __name__ == "__main__":
    main()
