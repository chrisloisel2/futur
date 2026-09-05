"""W8 - C4 of the preregistered addendum: is part of the edge carried by the intraday CLOCK
MISMATCH of the Track B daily bars?

The panel accepts a symbol-day with `n_bars >= 250` out of 288 five-minute bars, so a symbol's
"close" can be up to ~13% of a day early, and the mismatch could correlate with the liquidity
signals (illiquid names have more gaps) - which would touch AMIHUD_30D most. This script
rebuilds the Track B long legs under `n_bars >= 280` (>=97% of the day) and re-runs the basket.

No parameter is tuned: 250 remains the spec, 280 is reported as robustness only.
"""
import json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import b1_track_b_ensemble as B1
from b1_track_b_ensemble import APRIORI as APRIORI_B, xs_z, portfolio, COST_RT
from a1_track_a_ensemble import OUTDIR
from c1_track_c_cross_basis import align, combine, gate_daily, verdict, COST_STRESS

SCRATCH = ("/tmp/claude-1000/-home-qbee-futur/"
           "d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w8")
WANT = ["AMIHUD_30D", "MOM_30D"]


def legs_at(min_bars):
    """Track B long legs (top decile minus same-day eligible-universe mean) at a given bar
    completeness threshold. Implemented by pre-filtering the panel and reusing b1 unchanged."""
    src = os.path.join(SCRATCH, "daily_ohlcv.parquet")
    if min_bars == 250:
        B1.PANEL = src
    else:
        out = os.path.join(SCRATCH, f"daily_ohlcv_nb{min_bars}.parquet")
        p = pd.read_parquet(src)
        p[p["n_bars"] >= min_bars].to_parquet(out)
        B1.PANEL = out
    p = B1.build_features()
    cols = list(APRIORI_B)
    Z = xs_z(p, cols)
    for c in cols:
        p["z_" + c] = Z[c]
    p = p.sort_values(["symbol", "day"]).reset_index(drop=True)
    g = p.groupby("symbol", sort=False)
    p["fwd_1"] = g["close"].transform(lambda s: s.shift(-1) / s - 1.0)
    sub = p[p["eligible"] & p["fwd_1"].notna()].copy()
    days = list(np.sort(sub["day"].unique()))
    gs = sub.groupby("day")
    by_sym = {d: gs.get_group(d)["symbol"].values for d in days}
    by_fwd = {d: gs.get_group(d)["fwd_1"].values for d in days}
    rows = []
    for c in WANT:
        pf = portfolio(days, by_sym,
                       {d: APRIORI_B[c] * gs.get_group(d)["z_" + c].values for d in days},
                       by_fwd, long_short=False)
        rows.append(pd.DataFrame({"day": pd.to_datetime(pf["date"]), "sleeve": f"BLO_{c}",
                                  "track": "B", "gross_bps": pf["gross_bps"].values,
                                  "turnover": pf["turnover"].values,
                                  "n_episodes": pf["k"].values, "cost_model": "TURNOVER"}))
    stats = {"n_symbol_days": int(len(p)), "n_eligible": int(p["eligible"].sum()),
             "mean_universe": float(np.mean([len(by_sym[d]) for d in days])),
             "n_days": len(days)}
    return pd.concat(rows, ignore_index=True), stats


def main():
    # the Track A sleeve is untouched by this test - reuse the one built by e1's protocol
    from e1_selection_oos_test import (hour_cell_means, sleeve_series, add_apriori_score)
    from d1_placebo_hour_stratified import build_scores
    from a1_track_a_ensemble import causal_quantile_threshold
    df, mo, y, scores = build_scores()
    hour = df["event_time"].dt.hour.values
    sc = scores["CONFIDENCE_IC_WF"]
    thr = causal_quantile_threshold(sc, mo, 0.80)
    pop = np.isfinite(thr)
    m = np.isfinite(sc) & pop & (sc >= thr)
    A = {}
    for cost in (COST_RT, COST_STRESS):
        base = hour_cell_means(y - cost, hour, mo, pop)
        s = sleeve_series(df, y, m, base, cost)
        A[cost] = pd.DataFrame({"day": s.index, "sleeve": "A_SEL", "track": "A",
                                "gross_bps": s.values + COST_RT, "turnover": np.nan,
                                "n_episodes": 1, "cost_model": "PER_EPISODE"})

    res = {"test": "C4 - clock-mismatch robustness (n_bars threshold)",
           "spec_threshold": 250, "robustness_threshold": 280, "bars_per_full_day": 288}
    out = {}
    for nb in (250, 280):
        legs, stats = legs_at(nb)
        res[f"panel_stats_nb{nb}"] = stats
        d14 = pd.concat([legs, A[COST_RT]], ignore_index=True)
        d28 = pd.concat([legs, A[COST_STRESS]], ignore_index=True)
        sls = ["A_SEL", "BLO_AMIHUD_30D", "BLO_MOM_30D"]
        M, _ = align(sls, d14, COST_RT)
        Ms, _ = align(sls, d28, COST_STRESS)
        opy = 365.25
        rec = {}
        for s_ in sls:
            g = gate_daily(M[s_], f"nb{nb}::{s_}", opy, Ms[s_])
            if g:
                g["verdict"] = verdict(g)
                rec[s_] = {"net_bps": g["net_bps"], "net_bps_stress28": g["net_bps_stress28"],
                           "t": g["t_stat_declustered"], "sr_annualised": g["sr_annualised"],
                           "eta_years": g["eta_forward_confirmation_years"]}
        for mode in ["INVVOL_WF", "EQUAL_CAPITAL"]:
            g = gate_daily(combine(M, mode), f"nb{nb}::BASKET::{mode}", opy,
                           combine(Ms, mode))
            if g:
                g["verdict"] = verdict(g)
            rec[f"BASKET_{mode}"] = g
        out[f"nb{nb}"] = rec
    res["results"] = out

    keep = {}
    for k in ["BLO_AMIHUD_30D", "BLO_MOM_30D", "A_SEL"]:
        keep[k] = out["nb280"][k]["net_bps"] / out["nb250"][k]["net_bps"]
    for mode in ["INVVOL_WF", "EQUAL_CAPITAL"]:
        keep[f"BASKET_{mode}"] = (out["nb280"][f"BASKET_{mode}"]["net_bps"]
                                  / out["nb250"][f"BASKET_{mode}"]["net_bps"])
    res["net_bps_retained_at_nb280"] = keep
    res["C4_pass"] = bool(keep["BASKET_INVVOL_WF"] >= 0.50)
    with open(os.path.join(OUTDIR, "e2_nbars_robustness_results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)

    for nb in (250, 280):
        st = res[f"panel_stats_nb{nb}"]
        print(f"\n--- n_bars >= {nb}: {st['n_symbol_days']} symbol-days, "
              f"{st['n_eligible']} eligible, mean universe {st['mean_universe']:.1f} ---")
        for k, v in out[f"nb{nb}"].items():
            if k.startswith("BASKET"):
                print(f"  {k:22s} net={v['net_bps']:+6.2f} n28={v['net_bps_stress28']:+6.2f} "
                      f"t={v['t_stat_declustered']:5.2f} SR={v['sr_annualised']:.2f} "
                      f"ETA={v['eta_forward_confirmation_years']:.2f}y {v['verdict']}")
            else:
                print(f"  {k:22s} net={v['net_bps']:+6.2f} n28={v['net_bps_stress28']:+6.2f} "
                      f"t={v['t']:5.2f} SR={v['sr_annualised']:.2f}")
    print("\nnet_bps retained at n_bars>=280:",
          {k: round(v, 3) for k, v in keep.items()}, "| C4 pass:", res["C4_pass"])
    print("wrote e2_nbars_robustness_results.json")


if __name__ == "__main__":
    main()
