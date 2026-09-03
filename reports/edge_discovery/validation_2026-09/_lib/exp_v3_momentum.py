"""V3 — XSEC_MOMENTUM_HORIZON_EXTENSION + XSEC_RESIDUAL_MOMENTUM_14D.

Exécute EXACTEMENT les specs préenregistrées :
  ../XSEC_MOMENTUM_HORIZON_EXTENSION/PREREGISTRATION.md  (PRIMARY 14D_LO excess, P1..P8)
  ../XSEC_RESIDUAL_MOMENTUM_14D/PREREGISTRATION.md       (PRIMARY resid14 LONG excess, P1..P8)

Aucun paramètre n'est modifié après avoir vu un résultat. Les perturbations sont des
tests de robustesse, jamais une grille de recherche.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl          # noqa: E402
import run_xsec_family as rf         # noqa: E402

OUT = "/home/qbee/futur/reports/edge_discovery/validation_2026-09"


def run_spec(xp, cfg, signal_fn, label):
    t0 = time.time()
    runs = vl.run_xsec(xp, cfg, signal_fn)
    print(f"    {label:38s} periods={len(runs):4d}  ({time.time()-t0:.0f}s)", flush=True)
    return runs


def gate(runs, column, n_legs, min_days, exclude_years=None, cost_multiplier=1.0):
    return rf.gate_from_runs(
        runs, column=column, n_legs=n_legs, minimum_calendar_days=min_days,
        exclude_years=exclude_years, cost_multiplier=cost_multiplier,
    )


def summarize(g, keys=("net_bps", "net_bps_stress28", "t_stat_declustered",
                       "n_independent_L3", "bootstrap_p05", "n_years_positive", "n_years")):
    return {k: g.get(k) for k in keys}


def main():
    xp, onboard, fallback = rf.load_panel()
    xp.logret = np.log(xp.close).diff()
    results = {}

    # ── A. XSEC_MOMENTUM_HORIZON_EXTENSION ────────────────────────────────
    print("[A] XSEC_MOMENTUM_HORIZON_EXTENSION", flush=True)
    base = vl.XSecConfig(formation_days=14, holding_days=14)
    primary = run_spec(xp, base, rf.sig_momentum(14), "PRIMARY 14D_LO")

    A = {
        "PRIMARY_14D_LO_excess": gate(primary, "excess_gross_bps", 1, 182),
        "PRIMARY_14D_LO_raw": gate(primary, "raw_gross_bps", 1, 182),
        "P2_14D_LS": gate(primary, "ls_gross_bps", 2, 182),
    }

    p1 = run_spec(xp, vl.XSecConfig(formation_days=30, holding_days=30),
                  rf.sig_momentum(30), "P1 30D_LO")
    A["P1_30D_LO_excess"] = gate(p1, "excess_gross_bps", 1, 365)

    p3 = run_spec(xp, vl.XSecConfig(formation_days=14, holding_days=14,
                                    liquidity_floor=2_000_000.0),
                  rf.sig_momentum(14), "P3 floor $2M")
    A["P3_floor2M_excess"] = gate(p3, "excess_gross_bps", 1, 182)

    A["P4_ex2021_excess"] = gate(primary, "excess_gross_bps", 1, 182, exclude_years=[2021])
    A["P5_cost150pct_excess"] = gate(primary, "excess_gross_bps", 1, 182, cost_multiplier=1.5)

    p7 = run_spec(xp, vl.XSecConfig(formation_days=14, holding_days=14, winsorize=None),
                  rf.sig_momentum(14), "P7 no winsorization")
    A["P7_nowinsor_excess"] = gate(p7, "excess_gross_bps", 1, 182)

    p8 = run_spec(xp, vl.XSecConfig(formation_days=14, holding_days=14, exec_lag_days=1),
                  rf.sig_momentum(14), "P8 exec lag 1d")
    A["P8_execlag1d_excess"] = gate(p8, "excess_gross_bps", 1, 182)

    # P6 : les 14 phases d'ancrage
    anchors = []
    for a in range(14):
        r = vl.run_xsec(xp, vl.XSecConfig(formation_days=14, holding_days=14, anchor=a),
                        rf.sig_momentum(14))
        if not r.empty:
            anchors.append({"anchor": a, "n": len(r),
                            "gross_excess": round(float(r["excess_gross_bps"].mean()), 2)})
    gr = [x["gross_excess"] for x in anchors]
    A["P6_anchors"] = {
        "per_anchor": anchors,
        "pooled_mean_gross_excess": round(float(np.mean(gr)), 2),
        "std": round(float(np.std(gr)), 2),
        "min": round(float(np.min(gr)), 2), "max": round(float(np.max(gr)), 2),
        "n_positive": int(sum(1 for x in gr if x > 0)), "n_anchors": len(gr),
    }
    print(f"    P6 anchors: pooled={A['P6_anchors']['pooled_mean_gross_excess']} "
          f"positive={A['P6_anchors']['n_positive']}/{len(gr)}", flush=True)
    results["XSEC_MOMENTUM_HORIZON_EXTENSION"] = A

    # ── B. XSEC_RESIDUAL_MOMENTUM_14D ─────────────────────────────────────
    print("[B] XSEC_RESIDUAL_MOMENTUM_14D", flush=True)
    resid = run_spec(xp, base, rf.sig_residual_momentum(), "PRIMARY resid14")
    B = {
        "PRIMARY_resid14_LONG_excess": gate(resid, "excess_gross_bps", 1, 182),
        "PRIMARY_resid14_LONG_raw": gate(resid, "raw_gross_bps", 1, 182),
        "resid14_LS": gate(resid, "ls_gross_bps", 2, 182),
    }
    rp1 = run_spec(xp, base, rf.sig_residual_momentum(beta_days=90, min_pairs=60), "P1 beta 90d")
    B["P1_beta90d_excess"] = gate(rp1, "excess_gross_bps", 1, 182)
    rp2 = run_spec(xp, base, rf.sig_residual_momentum(subtract_alpha=True), "P2 full residual")
    B["P2_full_residual_excess"] = gate(rp2, "excess_gross_bps", 1, 182)
    rp3 = run_spec(xp, base, rf.sig_residual_momentum(vol_scaled=True), "P3 vol-scaled")
    B["P3_volscaled_excess"] = gate(rp3, "excess_gross_bps", 1, 182)
    B["P4_ex2021_excess"] = gate(resid, "excess_gross_bps", 1, 182, exclude_years=[2021])
    B["P5_cost150pct_excess"] = gate(resid, "excess_gross_bps", 1, 182, cost_multiplier=1.5)
    rp7 = run_spec(xp, vl.XSecConfig(formation_days=14, holding_days=14, liquidity_floor=2_000_000.0),
                   rf.sig_residual_momentum(), "P7 floor $2M")
    B["P7_floor2M_excess"] = gate(rp7, "excess_gross_bps", 1, 182)
    rp8 = run_spec(xp, vl.XSecConfig(formation_days=14, holding_days=14, winsorize=None),
                   rf.sig_residual_momentum(), "P8 no winsorization")
    B["P8_nowinsor_excess"] = gate(rp8, "excess_gross_bps", 1, 182)
    results["XSEC_RESIDUAL_MOMENTUM_14D"] = B

    # ── C. « Même facteur ? » — rangs + rendements de portefeuille ─────────
    print("[C] overlap / same-factor checks", flush=True)
    mom7 = run_spec(xp, vl.XSecConfig(formation_days=7, holding_days=14),
                    rf.sig_momentum(7), "mom7 (grille 14j)")
    amih = run_spec(xp, base, rf.sig_amihud(30), "amihud (grille 14j)")

    rank_corr = {"mom14_vs_mom7": [], "mom14_vs_resid14": [], "mom14_vs_amihud": [],
                 "resid14_vs_amihud": []}
    fn14, fnr, fn7, fna = (rf.sig_momentum(14), rf.sig_residual_momentum(),
                           rf.sig_momentum(7), rf.sig_amihud(30))
    elig_mask = xp.eligibility(1_000_000.0)
    for d in xp.grid(base):
        e = elig_mask.columns[elig_mask.loc[d].to_numpy()]
        if len(e) < 20:
            continue
        s14, sr, s7, sa = fn14(xp, d, e), fnr(xp, d, e), fn7(xp, d, e), fna(xp, d, e)
        for key, (a, b) in {
            "mom14_vs_mom7": (s14, s7), "mom14_vs_resid14": (s14, sr),
            "mom14_vs_amihud": (s14, sa), "resid14_vs_amihud": (sr, sa),
        }.items():
            df = pd.concat([a, b], axis=1).dropna()
            if len(df) >= 20:
                rank_corr[key].append(float(sps.spearmanr(df.iloc[:, 0], df.iloc[:, 1]).statistic))

    C = {k: {"mean": round(float(np.mean(v)), 3), "std": round(float(np.std(v)), 3), "n": len(v)}
         for k, v in rank_corr.items() if v}

    # corrélation des rendements de portefeuille (excess, mêmes dates)
    def series(r):
        return r.set_index("date")["excess_gross_bps"]
    port = pd.DataFrame({
        "mom14": series(primary), "resid14": series(resid),
        "mom7": series(mom7), "amihud": series(amih),
    }).dropna()
    C["portfolio_return_corr"] = json.loads(port.corr().round(3).to_json())
    C["n_common_periods"] = int(len(port))

    # recouvrement des jambes longues (Jaccard)
    jac = []
    pl = primary.set_index("date")["long_leg"]
    rl = resid.set_index("date")["long_leg"]
    for d in pl.index.intersection(rl.index):
        a, b = set(pl[d]), set(rl[d])
        if a or b:
            jac.append(len(a & b) / len(a | b))
    C["jaccard_mom14_vs_resid14_long_leg"] = {
        "mean": round(float(np.mean(jac)), 3), "n": len(jac)} if jac else None

    # test apparié « resid − raw » sur les mêmes dates (bras A − bras B)
    paired = pd.DataFrame({"resid": series(resid), "raw": series(primary)}).dropna()
    diff = (paired["resid"] - paired["raw"]).to_numpy()
    l3 = vl.month_clusters(pd.Series(paired.index))
    m, se, t = vl.cluster_robust_t(diff, l3)
    C["paired_resid_minus_raw"] = {
        "mean_bps": round(float(m), 2), "t_L3": None if not np.isfinite(t) else round(float(t), 3),
        "n_periods": len(diff), "n_L3": int(len(pd.unique(l3))),
    }
    results["same_factor_checks"] = C

    # ── D. capacité ───────────────────────────────────────────────────────
    dvm = xp.dvm
    cap = []
    for d, legs in primary.set_index("date")["long_leg"].items():
        v = dvm.loc[d, [s for s in legs if s in dvm.columns]].dropna()
        if len(v):
            cap.append(float(v.median()))
    results["capacity"] = {
        "long_leg_trailing_30d_median_dv_usd": {
            "p05": round(float(np.percentile(cap, 5)), 0),
            "median": round(float(np.median(cap)), 0),
        },
        "implied_participation_300k_book_pct_of_adv": round(
            100.0 * (300_000 / 20) / float(np.percentile(cap, 5)), 4),
        "note": "participation = (book/nb noms) / dollar-volume médian 30j du 5e centile des jambes",
    }

    results["_meta"] = {
        "panel_days": int(len(xp.days)), "panel_symbols": int(len(xp.symbols)),
        "panel_range": [str(xp.days.min().date()), str(xp.days.max().date())],
        "onboard_fallback_symbols": fallback,
        "generated": pd.Timestamp.utcnow().isoformat(),
    }

    os.makedirs(f"{OUT}/_lib/out", exist_ok=True)
    with open(f"{OUT}/_lib/out/v3_raw.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    print("\n=== SYNTHÈSE V3 ===")
    for cand in ("XSEC_MOMENTUM_HORIZON_EXTENSION", "XSEC_RESIDUAL_MOMENTUM_14D"):
        print(f"\n{cand}")
        for k, v in results[cand].items():
            if isinstance(v, dict) and "net_bps" in v:
                print(f"  {k:34s} net={v['net_bps']:9.2f} net28={v['net_bps_stress28']:9.2f} "
                      f"t_L3={str(v['t_stat_declustered']):>7s} L3={v['n_independent_L3']:4d} "
                      f"boot_p05={v['bootstrap_p05']:9.2f} yrs+={v['n_years_positive']}/{v['n_years']}")
    print("\nsame-factor:", json.dumps(C, indent=2, default=str)[:1200])


if __name__ == "__main__":
    main()
