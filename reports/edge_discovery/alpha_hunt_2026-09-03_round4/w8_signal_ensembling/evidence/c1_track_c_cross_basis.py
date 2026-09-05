"""W8 Track C - cross-BASIS ensembling. The two bases (A = liquidation-cascade events,
B = cross-sectional daily panel) share exactly one unit: the calendar day. This is the last
preregistered test (PREREGISTRATION.md section 1) and the only one where the sqrt(K) argument
has a real chance, because the two bases have no mechanism in common.

Convention (declared, not fitted):
 * every sleeve series is a NET return-on-notional in bps, per calendar day;
 * a sleeve that does not trade on a day contributes 0 (capital idle) - this leaves SR_ann,
   hence ETA, invariant (mean scales by f, sd by sqrt(f), obs/yr by 1/f);
 * EQUAL_CAPITAL is the parameter-free headline; INVVOL_WF uses expanding, strictly-prior
   volatilities (walk-forward, shifted) and is the realistic operating version;
 * Track A cost = 14bps (stress 28) per EPISODE; Track B cost = measured turnover x 14
   (stress 28) per rebalance. No cost is averaged across sleeves.
"""
import itertools, json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from a1_track_a_ensemble import block_bootstrap_ci, OUTDIR

Z_A, Z_P = 1.959963985, 0.8416212336
COST_RT, COST_STRESS = 14.0, 28.0
SRC = os.path.join(OUTDIR, "c0_daily_sleeves.parquet")


def net_series(df, sleeve, cost):
    d = df[df["sleeve"] == sleeve]
    if d["cost_model"].iloc[0] == "PER_EPISODE":
        x = d["gross_bps"].values - cost
    else:
        x = d["gross_bps"].values - d["turnover"].values * cost
    return pd.Series(x, index=pd.DatetimeIndex(d["day"])).sort_index()


def eta_of(x, obs_per_year, haircut=0.5):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 5 or x.std(ddof=1) == 0:
        return None
    sr = x.mean() / x.std(ddof=1)
    out = {"sr_obs": float(sr), "sr_ann": float(sr * np.sqrt(obs_per_year)),
           "obs_per_year": float(obs_per_year)}
    if sr <= 0:
        out.update({"n_required": None, "eta_years": None, "note": "negative mean"})
        return out
    n = ((Z_A + Z_P) / (haircut * sr)) ** 2
    out.update({"n_required": float(n), "eta_years": float(n / obs_per_year),
                "eta_days": float(n / obs_per_year * 365.25)})
    return out


def gate_daily(s, label, obs_per_year, s_stress=None):
    """BRIEFING section-2 gate on a daily portfolio return series (already declustered:
    one observation per calendar day = L2; L3 = month)."""
    s = s.dropna()
    if len(s) < 30:
        return None
    x = s.values
    mon = s.index.to_period("M").astype(str).values
    yr = s.index.year.values
    ydf = pd.DataFrame({"y": yr, "x": x}).groupby("y")["x"].agg(["mean", "size"])
    best = ydf["mean"].idxmax() if len(ydf) > 1 else None
    exb = float(x[yr != best].mean()) if best is not None else float("nan")
    msr = pd.Series(x).groupby(mon).mean()
    e = eta_of(x, obs_per_year)
    g = {"label": label,
         "n_raw": int(len(x)), "n_independent_L1": int(len(x)),
         "n_independent_L2": int(len(set(s.index.date))),
         "n_independent_L3": int(len(set(mon))),
         "net_bps": float(x.mean()),
         "net_bps_stress28": float(s_stress.dropna().mean()) if s_stress is not None else None,
         "t_stat_declustered": float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))),
         "t_stat_L3_month": float(msr.mean() / (msr.std(ddof=1) / np.sqrt(len(msr))))
         if len(msr) > 2 else float("nan"),
         "bootstrap_ci95": block_bootstrap_ci(x, mon),
         "year_by_year": {int(k): {"net_bps": float(v["mean"]), "n": int(v["size"])}
                          for k, v in ydf.iterrows()},
         "ex_best_year": {"dropped_year": (int(best) if best is not None else None),
                          "net_bps": exb, "n": int((yr != best).sum()) if best is not None else 0},
         "eta": e, "n_required": (e or {}).get("n_required"),
         "eta_forward_confirmation_years": (e or {}).get("eta_years"),
         "sr_annualised": (e or {}).get("sr_ann"),
         "event_rate_episodes_per_week": obs_per_year / 52.1775,
         "span": [str(s.index.min().date()), str(s.index.max().date())]}
    return g


def verdict(g):
    if g is None:
        return "DATA_LIMITED"
    if not np.isfinite(g["net_bps"]) or g["net_bps"] <= 0:
        return "DEAD"
    t = g["t_stat_declustered"]
    if not np.isfinite(t) or t < 1.0:
        return "DEAD"
    if t < 2.0:
        return "WEAK"
    if g["net_bps_stress28"] is not None and g["net_bps_stress28"] <= 0:
        return "COST_FRAGILE"
    if np.isfinite(g["ex_best_year"]["net_bps"]) and g["ex_best_year"]["net_bps"] <= 0:
        return "REGIME_DEPENDENT"
    eta = g["eta_forward_confirmation_years"]
    if eta is None or eta > 3.0:
        return "UNCONFIRMABLE_IN_HORIZON"
    return "VALIDATED_FOR_FORWARD"


def align(sleeves, df, cost):
    """Common window = intersection of sleeve availability; calendar = union of days in it;
    a silent sleeve contributes 0 that day."""
    ser = {s: net_series(df, s, cost) for s in sleeves}
    lo = max(v.index.min() for v in ser.values())
    hi = min(v.index.max() for v in ser.values())
    days = sorted(set().union(*[set(v[(v.index >= lo) & (v.index <= hi)].index) for v in ser.values()]))
    idx = pd.DatetimeIndex(days)
    M = pd.DataFrame({s: ser[s].reindex(idx).fillna(0.0) for s in sleeves}, index=idx)
    active = pd.DataFrame({s: ser[s].reindex(idx).notna() for s in sleeves}, index=idx)
    return M, active


def combine(M, mode, min_prior=120):
    if mode == "EQUAL_CAPITAL":
        return M.mean(axis=1)
    v = M.expanding(min_periods=min_prior).std().shift(1)
    w = (1.0 / v).replace([np.inf, -np.inf], np.nan)
    w = w.div(w.sum(axis=1), axis=0)
    return (M * w).sum(axis=1).where(w.notna().all(axis=1))


def main():
    df = pd.read_parquet(SRC)
    df["day"] = pd.to_datetime(df["day"])
    res = {"track": "C",
           "population": "cross-basis: A = liq_cascade events (LONG 4h), B = 312-symbol daily "
                         "cross-sectional decile L/S. Shared unit = calendar day.",
           "convention": "net return-on-notional bps per day; idle sleeve = 0; "
                         "EQUAL_CAPITAL is parameter-free, INVVOL_WF is expanding/shifted."}

    A_SLEEVES = ["A_EW_APRIORI_q90", "A_EW_WALKFORWARD_q90", "A_EW_WALKFORWARD_q80",
                 "A_CONFIDENCE_IC_WF_q80"]
    B_SLEEVES = ["B_EW_APRIORI", "B_EW_WALKFORWARD", "B_CONFIDENCE_WF", "B_SIGNAL_AMIHUD_30D"]

    # ---- C0 : every sleeve, restated on the COMMON Track-C window -----------------------
    allsl = A_SLEEVES + B_SLEEVES + ["B_SIGNAL_MOM_30D", "B_SIGNAL_MOM_7D",
                                     "B_SIGNAL_TURNOVER_30D", "A_SIGNAL_ret_24h_q90",
                                     "A_SIGNAL_px_ret_1h_q90"]
    M_all, act_all = align(allsl, df, COST_RT)
    Ms_all, _ = align(allsl, df, COST_STRESS)
    span_years = (M_all.index.max() - M_all.index.min()).days / 365.25
    opy = len(M_all) / span_years
    res["common_window"] = {"start": str(M_all.index.min().date()),
                            "end": str(M_all.index.max().date()),
                            "n_days": int(len(M_all)), "obs_per_year": float(opy)}
    per = {}
    for s in allsl:
        g = gate_daily(M_all[s], s, opy, Ms_all[s])
        if g:
            g["verdict"] = verdict(g)
            g["active_day_fraction"] = float(act_all[s].mean())
            per[s] = g
    res["C0_sleeves_on_common_window"] = per

    # ---- C1 : the cross-basis correlation matrix (THE deliverable) ---------------------
    C = M_all.corr()
    off = C.values[np.triu_indices(len(C), 1)]
    ab = [(a, b, float(C.loc[a, b])) for a in A_SLEEVES for b in B_SLEEVES]
    res["C1_cross_basis_correlation"] = {
        "labels": list(C.columns), "matrix": np.round(C.values, 4).tolist(),
        "median_abs_all": float(np.median(np.abs(off))),
        "cross_basis_pairs_A_x_B": [{"a": a, "b": b, "rho": r} for a, b, r in ab],
        "cross_basis_median_abs_rho": float(np.median([abs(r) for _, _, r in ab])),
        "cross_basis_max_abs_rho": float(np.max([abs(r) for _, _, r in ab]))}

    # ---- C2 : all A x B pairs, both weightings -----------------------------------------
    pairs = {}
    for a, b in itertools.product(A_SLEEVES, B_SLEEVES):
        M, _ = align([a, b], df, COST_RT)
        Ms, _ = align([a, b], df, COST_STRESS)
        opy2 = len(M) / max((M.index.max() - M.index.min()).days / 365.25, 1e-9)
        rec = {"rho": float(M[a].corr(M[b])), "n_days": int(len(M))}
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            s = combine(M, mode); ss = combine(Ms, mode)
            g = gate_daily(s, f"C2::{a}+{b}::{mode}", opy2, ss)
            if g:
                g["verdict"] = verdict(g)
            rec[mode] = g
        # best single sleeve on the SAME window (the decisive comparison)
        best = None
        for s_ in (a, b):
            g = gate_daily(M[s_], s_, opy2, Ms[s_])
            if g and (best is None or (g["sr_annualised"] or -9) > (best["sr_annualised"] or -9)):
                best = g
        rec["best_single_sleeve_same_window"] = {
            "label": best["label"], "net_bps": best["net_bps"],
            "sr_annualised": best["sr_annualised"],
            "eta_years": best["eta_forward_confirmation_years"]} if best else None
        eq = rec["EQUAL_CAPITAL"]
        if eq and best and best["eta_forward_confirmation_years"] and eq["eta_forward_confirmation_years"]:
            rec["eta_division_factor_vs_best_single"] = float(
                best["eta_forward_confirmation_years"] / eq["eta_forward_confirmation_years"])
        pairs[f"{a}+{b}"] = rec
    res["C2_pairs"] = pairs

    # ---- C3 : multi-sleeve baskets ------------------------------------------------------
    baskets = {
        "PARAMFREE_A+B": ["A_EW_WALKFORWARD_q90", "B_EW_APRIORI"],
        "A_comp+AMIHUD30": ["A_EW_WALKFORWARD_q90", "B_SIGNAL_AMIHUD_30D"],
        "AMIHUD30+MOM30_withinB": ["B_SIGNAL_AMIHUD_30D", "B_SIGNAL_MOM_30D"],
        "TRIPLE_A+AMIHUD+MOM30": ["A_EW_WALKFORWARD_q90", "B_SIGNAL_AMIHUD_30D",
                                  "B_SIGNAL_MOM_30D"],
        "BESTOFBREED_SELECTED": ["A_CONFIDENCE_IC_WF_q80", "B_SIGNAL_AMIHUD_30D"],
        "BESTOFBREED_TRIPLE_SELECTED": ["A_CONFIDENCE_IC_WF_q80", "B_SIGNAL_AMIHUD_30D",
                                        "B_SIGNAL_MOM_30D"],
    }
    out = {}
    for name, sl in baskets.items():
        M, _ = align(sl, df, COST_RT)
        Ms, _ = align(sl, df, COST_STRESS)
        opy2 = len(M) / max((M.index.max() - M.index.min()).days / 365.25, 1e-9)
        rec = {"sleeves": sl, "n_days": int(len(M)),
               "pairwise_rho": {f"{i}~{j}": float(M[i].corr(M[j]))
                                for i, j in itertools.combinations(sl, 2)}}
        singles = {}
        for s_ in sl:
            g = gate_daily(M[s_], s_, opy2, Ms[s_])
            if g:
                g["verdict"] = verdict(g)
                singles[s_] = g
        rec["singles"] = singles
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            s = combine(M, mode); ss = combine(Ms, mode)
            g = gate_daily(s, f"C3::{name}::{mode}", opy2, ss)
            if g:
                g["verdict"] = verdict(g)
            rec[mode] = g
        bs = max(singles.values(), key=lambda g: (g["sr_annualised"] or -9)) if singles else None
        rec["best_single_sleeve_same_window"] = {
            "label": bs["label"], "net_bps": bs["net_bps"], "sr_annualised": bs["sr_annualised"],
            "eta_years": bs["eta_forward_confirmation_years"]} if bs else None
        for mode in ["EQUAL_CAPITAL", "INVVOL_WF"]:
            g = rec[mode]
            if g and bs and bs["eta_forward_confirmation_years"] and g["eta_forward_confirmation_years"]:
                rec[f"eta_division_factor_{mode}"] = float(
                    bs["eta_forward_confirmation_years"] / g["eta_forward_confirmation_years"])
            # sqrt(K) prediction from the measured correlation, for reference
            if g and singles:
                srs = np.array([x["sr_annualised"] or 0 for x in singles.values()])
                rho = np.mean([v for v in rec["pairwise_rho"].values()])
                k = len(srs)
                pred = srs.mean() * np.sqrt(k / (1 + (k - 1) * rho)) if (1 + (k - 1) * rho) > 0 else np.nan
                rec["sr_ann_predicted_equalSR_model"] = float(pred)
                rec[f"sr_ann_measured_{mode}"] = g["sr_annualised"]
        out[name] = rec
    res["C3_baskets"] = out

    with open(os.path.join(OUTDIR, "c1_track_c_results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("wrote c1_track_c_results.json")
    print("common window", res["common_window"])
    print("cross-basis median |rho| =", round(res["C1_cross_basis_correlation"]["cross_basis_median_abs_rho"], 4),
          " max =", round(res["C1_cross_basis_correlation"]["cross_basis_max_abs_rho"], 4))
    for name, rec in out.items():
        eq, iv = rec["EQUAL_CAPITAL"], rec["INVVOL_WF"]
        bs = rec["best_single_sleeve_same_window"]
        print(f"  {name:30s} rho={np.mean(list(rec['pairwise_rho'].values())):+.3f} "
              f"EQ: net={eq['net_bps']:+7.2f} SR={eq['sr_annualised']:.2f} "
              f"ETA={eq['eta_forward_confirmation_years']:.2f}y {eq['verdict']:24s} | "
              f"best_single {bs['label']} SR={bs['sr_annualised']:.2f} ETA={bs['eta_years']:.2f}y "
              f"| x{rec.get('eta_division_factor_EQUAL_CAPITAL', float('nan')):.2f}")


if __name__ == "__main__":
    main()
