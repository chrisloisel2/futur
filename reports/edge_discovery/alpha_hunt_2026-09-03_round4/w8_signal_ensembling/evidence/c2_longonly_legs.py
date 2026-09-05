"""W8 - LONG-ONLY legs of the cross-sectional sleeves (project rule 11: no standalone
directional short; a long/short spread is only deliverable if its LONG leg is reported
separately). Long leg = top decile of the signal minus the eligible-universe mean of the
same day (excess return), cost charged on the leg's own measured turnover.

Also re-runs the Track C combination with LONG-ONLY Track B legs, since Track A is long-only
by construction: that combination is the policy-compliant version of the composite.
"""
import json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from b1_track_b_ensemble import APRIORI as APRIORI_B, build_features, xs_z, portfolio, COST_RT
from a1_track_a_ensemble import OUTDIR
from c1_track_c_cross_basis import align, combine, gate_daily, verdict, COST_STRESS

WANT = ["AMIHUD_30D", "AMIHUD_7D", "MOM_30D", "TURNOVER_30D", "MOM_7D"]
OUT_SER = os.path.join(OUTDIR, "c2_longonly_sleeves.parquet")


def main():
    p = build_features()
    cols = list(APRIORI_B)
    Z = xs_z(p, cols)
    for c in cols:
        p["z_" + c] = Z[c]
    p = p.sort_values(["symbol", "day"]).reset_index(drop=True)
    g = p.groupby("symbol", sort=False)
    p["fwd_1"] = g["close"].transform(lambda s: s.shift(-1) / s - 1.0)
    sub = p[p["eligible"] & p["fwd_1"].notna()].copy()
    days = list(np.sort(sub["day"].unique()))
    gsub = sub.groupby("day")
    by_sym, by_fwd, by_z = {}, {}, {}
    for d in days:
        blk = gsub.get_group(d)
        by_sym[d] = blk["symbol"].values
        by_fwd[d] = blk["fwd_1"].values
        by_z[d] = {c: blk["z_" + c].values for c in cols}
    apr = np.array([APRIORI_B[c] for c in cols], float)
    zstack = {d: np.column_stack([by_z[d][c] for c in cols]) for d in days}

    rows = []
    for c in WANT:
        pf = portfolio(days, by_sym, {d: APRIORI_B[c] * by_z[d][c] for d in days}, by_fwd,
                       long_short=False)
        rows.append(pd.DataFrame({"day": pd.to_datetime(pf["date"]),
                                  "sleeve": f"BLO_{c}", "track": "B",
                                  "gross_bps": pf["gross_bps"].values,
                                  "turnover": pf["turnover"].values,
                                  "n_episodes": pf["k"].values, "cost_model": "TURNOVER"}))
    sc = {}
    for d in days:
        M = zstack[d] * apr
        nok = np.isfinite(M).sum(axis=1)
        v = np.nansum(np.where(np.isfinite(M), M, 0.0), axis=1) / np.maximum(nok, 1)
        v[nok < 0.6 * len(cols)] = np.nan
        sc[d] = v
    pf = portfolio(days, by_sym, sc, by_fwd, long_short=False)
    rows.append(pd.DataFrame({"day": pd.to_datetime(pf["date"]), "sleeve": "BLO_EW_APRIORI",
                              "track": "B", "gross_bps": pf["gross_bps"].values,
                              "turnover": pf["turnover"].values,
                              "n_episodes": pf["k"].values, "cost_model": "TURNOVER"}))
    lo = pd.concat(rows, ignore_index=True)
    lo.to_parquet(OUT_SER)
    print("wrote", OUT_SER, round(os.path.getsize(OUT_SER) / 1e6, 3), "MB")

    # combine with the Track A sleeves (already long-only)
    a = pd.read_parquet(os.path.join(OUTDIR, "c0_daily_sleeves.parquet"))
    a["day"] = pd.to_datetime(a["day"])
    df = pd.concat([a, lo], ignore_index=True)

    res = {"note": "LONG-ONLY legs (top decile minus same-day eligible-universe mean). "
                   "Track A sleeves are long-only by construction."}
    sl = [f"BLO_{c}" for c in WANT] + ["BLO_EW_APRIORI"]
    M, _ = align(sl, df, COST_RT); Ms, _ = align(sl, df, COST_STRESS)
    opy = len(M) / max((M.index.max() - M.index.min()).days / 365.25, 1e-9)
    per = {}
    for s in sl:
        gg = gate_daily(M[s], s, opy, Ms[s])
        if gg:
            gg["verdict"] = verdict(gg)
            per[s] = gg
    res["long_only_legs"] = per

    baskets = {
        "LONGONLY_A+AMIHUD30": ["A_EW_WALKFORWARD_q90", "BLO_AMIHUD_30D"],
        "LONGONLY_A+AMIHUD30+MOM30": ["A_EW_WALKFORWARD_q90", "BLO_AMIHUD_30D", "BLO_MOM_30D"],
        "LONGONLY_BESTOFBREED": ["A_CONFIDENCE_IC_WF_q80", "BLO_AMIHUD_30D", "BLO_MOM_30D"],
    }
    out = {}
    for name, sls in baskets.items():
        M, _ = align(sls, df, COST_RT); Ms, _ = align(sls, df, COST_STRESS)
        opy2 = len(M) / max((M.index.max() - M.index.min()).days / 365.25, 1e-9)
        rec = {"sleeves": sls, "n_days": int(len(M)),
               "pairwise_rho": {f"{i}~{j}": float(M[i].corr(M[j]))
                                for k, i in enumerate(sls) for j in sls[k + 1:]}}
        singles = {}
        for s_ in sls:
            gg = gate_daily(M[s_], s_, opy2, Ms[s_])
            if gg:
                gg["verdict"] = verdict(gg)
                singles[s_] = gg
        rec["singles"] = singles
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            gg = gate_daily(combine(M, mode), f"{name}::{mode}", opy2, combine(Ms, mode))
            if gg:
                gg["verdict"] = verdict(gg)
            rec[mode] = gg
        bs = max(singles.values(), key=lambda x: (x["sr_annualised"] or -9)) if singles else None
        rec["best_single_sleeve_same_window"] = {
            "label": bs["label"], "net_bps": bs["net_bps"], "sr_annualised": bs["sr_annualised"],
            "eta_years": bs["eta_forward_confirmation_years"]} if bs else None
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            gg = rec[mode]
            if gg and bs and bs["eta_forward_confirmation_years"] and gg["eta_forward_confirmation_years"]:
                rec[f"eta_division_factor_{mode}"] = float(
                    bs["eta_forward_confirmation_years"] / gg["eta_forward_confirmation_years"])
        out[name] = rec
    res["C4_longonly_baskets"] = out
    with open(os.path.join(OUTDIR, "c2_longonly_results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("wrote c2_longonly_results.json")
    for k, v in per.items():
        print(f"  {k:20s} net={v['net_bps']:7.2f} n28={v['net_bps_stress28']:7.2f} "
              f"t={v['t_stat_declustered']:5.2f} SR={(v['sr_annualised'] or 0):5.2f} "
              f"ETA={v['eta_forward_confirmation_years']} {v['verdict']}")
    for k, v in out.items():
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            gg = v[mode]
            print(f"  {k:28s} {mode:14s} net={gg['net_bps']:7.2f} n28={gg['net_bps_stress28']:7.2f} "
                  f"t={gg['t_stat_declustered']:5.2f} SR={(gg['sr_annualised'] or 0):5.2f} "
                  f"ETA={round(gg['eta_forward_confirmation_years'],2) if gg['eta_forward_confirmation_years'] else 'NA'} "
                  f"{gg['verdict']} x{v.get('eta_division_factor_'+mode, float('nan')):.2f}")


if __name__ == "__main__":
    main()
