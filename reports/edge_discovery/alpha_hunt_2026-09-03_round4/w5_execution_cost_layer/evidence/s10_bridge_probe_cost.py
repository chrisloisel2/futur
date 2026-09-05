"""W5/s10 - bridge the 3-symbol / 4-day queue simulator onto the 15-symbol / 7-week probe,
then produce the project's replacement cost model.

WHY A BRIDGE IS NEEDED
The queue simulator needs top-of-book SIZES and signed trades, which exist only in
data/microstructure_reduced (BTC/ETH/SOL, 2026-08-31+). The probe (data/execution_probe)
covers 15 symbols x 7 weeks but uses a fill rule (price traversal) that is mechanically
pessimistic (s02/H1). On the 9 (venue, symbol) cells where BOTH exist, the simulator runs the
probe's rule AND a queue rule on the same attempts, which identifies the bias.

BRIDGE FORM (multiplicative, deliberately)
    AS_queue = rho(spread) * AS_probe_rule ,    rho fitted on the 9 overlap cells,
    rho clipped to [RHO_FLOOR, 1.0].
Multiplicative, not additive, because (a) it cannot produce a negative adverse selection when
extrapolated to the wide-spread alts, and (b) the observed bias scales with the spread.
rho is FLOORED at the most favourable value actually observed (0.60): outside the fitted
spread range the correction is capped at the largest reduction ever measured. Extrapolation
beyond spread ~1 bps is stamped EXTRAPOLATED in the output.

SIMULATOR HAIRCUT (the honesty term the brief demands)
Neither instrument is in the real book. Residual optimism in the QUEUE simulator, none of
which it models: latency to the exchange, post-only rejection and re-quote, our own order
changing the queue behind us, hidden/iceberg liquidity ahead, and orders that join our price
level ahead of us after placement. HAIRCUT_BPS is added to every maker cost as an explicit,
separately-reported term so the reader can set it to zero and see the uncorrected number.
"""
import os, json, glob
import numpy as np, pandas as pd

FEE_T, FEE_M = 5.0, 2.0
RHO_FLOOR    = 0.60          # the most favourable rho actually observed (binance/okx SOL)
HAIRCUT_BPS  = 1.0           # one-way, added to maker cost for un-modelled real-world frictions


def overlap_cells(S):
    d = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(f"{S}/qsim2_*.parquet"))],
                  ignore_index=True)
    d = d[d.date != "2026-08-31"]
    r = []
    for (v, s), g in d.groupby(["venue", "symbol"]):
        as_t = float((g.s0_bps / 2 - g.mko_trav_60).mean())
        as_q = float((g.s0_bps / 2 - g.mko_k10_60).mean())
        as_h = float((g.s0_bps / 2 - g.mko_k20_60).mean())
        r.append(dict(venue=v, symbol=s, spread=float(g.s0_bps.mean()),
                      AS_trav=as_t, AS_k10=as_q, AS_k20=as_h,
                      rho_k10=as_q / as_t, rho_k20=as_h / as_t,
                      fill600_trav=float(g.fill_trav.mean()),
                      fill600_k10=float(g.fill_k10.mean()),
                      fill60_trav=float((g.ttf_trav <= 60).mean()),
                      fill60_k10=float((g.ttf_k10 <= 60).mean())))
    return pd.DataFrame(r)


def main():
    S = os.environ["W5_SCRATCH"]
    out = {}
    oc = overlap_cells(S)
    # rho(spread): OLS on 9 cells; report R2 and the fitted range explicitly
    b, a = np.polyfit(oc.spread, oc.rho_k10, 1)
    r2 = np.corrcoef(oc.spread, oc.rho_k10)[0, 1] ** 2
    out["bridge_fit"] = {"form": "rho = a + b*spread_bps, clipped to [RHO_FLOOR,1]",
                         "a": float(a), "b": float(b), "r2": float(r2),
                         "rho_floor": RHO_FLOOR,
                         "fitted_spread_range_bps": [float(oc.spread.min()), float(oc.spread.max())],
                         "cells": oc.round(4).to_dict("records"),
                         "haircut_bps_oneway": HAIRCUT_BPS}
    print("=== bridge overlap cells (simulator: probe rule vs queue rule, same attempts) ===")
    print(oc.round(3).to_string())
    print(f"\nrho = {a:.4f} + {b:.4f}*spread   R2={r2:.3f}   floor={RHO_FLOOR}")

    def rho(sp):
        return float(np.clip(a + b * sp, RHO_FLOOR, 1.0))

    # ---------------- probe panel: per-symbol corrected cost ---------------------------------
    p = pd.read_parquet(f"{S}/panel.parquet")
    rows = []
    for sym, g in p.groupby("symbol"):
        sp = float(g.spread_bps.median())
        adv = float(g.adv_buy.mean())                  # probe-rule markout at 60s, BUY side
        as_probe = sp / 2 - adv
        rr = rho(sp)
        as_corr = rr * as_probe
        f60 = float((g.ttf_buy <= 60).mean())
        f600 = float(g.fill_buy.mean())
        ct = 2 * (sp / 2 + FEE_T)
        cm_raw = 2 * (-sp / 2 + FEE_M + as_corr)
        cm = cm_raw + 2 * HAIRCUT_BPS
        rows.append(dict(symbol=sym, spread_bps=round(sp, 3), tick_bound=True,
                         markout60_probe=round(adv, 3),
                         AS60_probe_rule=round(as_probe, 3), rho=round(rr, 3),
                         AS60_corrected=round(as_corr, 3),
                         extrapolated=bool(sp > oc.spread.max()),
                         fill_rate_60s=round(f60, 3), fill_rate_600s=round(f600, 3),
                         cost_taker_rt=round(ct, 2),
                         cost_maker_rt_nohaircut=round(cm_raw, 2),
                         cost_maker_rt=round(cm, 2),
                         maker_gain_rt=round(ct - cm, 2),
                         cost_convention_rt=14.0,
                         err_vs_convention_taker=round(ct - 14.0, 2)))
    P = pd.DataFrame(rows).sort_values("spread_bps").reset_index(drop=True)
    print("\n=== per-symbol corrected cost model (probe panel, 15 symbols x 7 weeks) ===")
    print(P.to_string())
    out["per_symbol_cost"] = P.to_dict("records")

    # ---------------- urgency on the probe panel, in COST terms ------------------------------
    print("\n=== urgency (probe panel): cost inside the top-shock windows ===")
    urg = []
    for q in [0.90, 0.99, 0.999]:
        thr = p.groupby("symbol").shock_5m_bps.transform(lambda x: x.quantile(q))
        e, bb = p[p.shock_5m_bps >= thr], p[p.shock_5m_bps < thr]
        rec = {"quantile": q, "n_raw": int(len(e)),
               "n_ind_L1_symbol_day": int(e.groupby(["symbol", "date"]).ngroups),
               "n_ind_L2_day": int(e.date.nunique()),
               "n_ind_L3_symbol": int(e.symbol.nunique())}
        # per-symbol so liquidity tiers are not blended, then aggregate over symbol-days
        per = []
        for sym in sorted(p.symbol.unique()):
            ee, bbb = e[e.symbol == sym], bb[bb.symbol == sym]
            if len(ee) < 30: continue
            sp_e, sp_b = ee.spread_bps.median(), bbb.spread_bps.median()
            rr = rho(sp_b)
            as_e = rr * (sp_e / 2 - ee.adv_buy.mean())
            as_b = rr * (sp_b / 2 - bbb.adv_buy.mean())
            f_e = (ee.ttf_buy <= 60).mean(); f_b = (bbb.ttf_buy <= 60).mean()
            cm_e = 2 * (-sp_e / 2 + FEE_M + as_e) + 2 * HAIRCUT_BPS
            cm_b = 2 * (-sp_b / 2 + FEE_M + as_b) + 2 * HAIRCUT_BPS
            ct_e = 2 * (sp_e / 2 + FEE_T); ct_b = 2 * (sp_b / 2 + FEE_T)
            per.append(dict(symbol=sym, spread_mult=sp_e / sp_b,
                            AS_base=as_b, AS_evt=as_e, dAS=as_e - as_b,
                            fill60_base=f_b, fill60_evt=f_e, dfill_rel=f_e / f_b - 1,
                            taker_pen_rt=ct_e - ct_b, maker_pen_rt=cm_e - cm_b,
                            cost_taker_rt_evt=ct_e, cost_maker_rt_evt=cm_e))
        PU = pd.DataFrame(per)
        rec["per_symbol"] = PU.round(4).to_dict("records")
        for c in ["spread_mult", "dAS", "dfill_rel", "taker_pen_rt", "maker_pen_rt"]:
            rec[f"{c}_median"] = float(PU[c].median())
            rec[f"{c}_mean"] = float(PU[c].mean())
            # t on the 15 independent symbols (L3 = symbol here; each is a distinct book)
            rec[f"{c}_t_over_symbols"] = float(PU[c].mean() / (PU[c].std(ddof=1) / np.sqrt(len(PU))))
        urg.append(rec)
        print(f"  q={q}: n_raw={rec['n_raw']} n_symday={rec['n_ind_L1_symbol_day']} "
              f"spread x{rec['spread_mult_median']:.2f} | dAS={rec['dAS_median']:+.2f}bps "
              f"(t={rec['dAS_t_over_symbols']:.2f}) | fill60 {rec['dfill_rel_median']*100:+.1f}% "
              f"| taker penalty {rec['taker_pen_rt_median']:+.2f} rt | maker penalty "
              f"{rec['maker_pen_rt_median']:+.2f} rt (t={rec['maker_pen_rt_t_over_symbols']:.2f})")
        print(PU.round(3).to_string())
    out["urgency_probe_panel"] = urg

    # ---------------- the replacement cost model (what H5 consumes) --------------------------
    # tiers by measured spread; every number is ROUND-TRIP bps
    def tier_cost(sp, mode, urgent):
        rr = rho(sp)
        # AS_probe as a function of spread, fitted on the 15 probe symbols (see P)
        as_p = float(np.interp(sp, P.spread_bps, P.AS60_probe_rule))
        as_c = rr * as_p
        ct = 2 * (sp / 2 + FEE_T)
        cm = 2 * (-sp / 2 + FEE_M + as_c) + 2 * HAIRCUT_BPS
        if urgent:
            ct += float(np.interp(sp, P.spread_bps, np.full(len(P), 0.0))) + URG_T
            cm += URG_M
        return ct if mode == "taker" else cm

    URG_T = float(np.median([r["taker_pen_rt_median"] for r in urg if r["quantile"] == 0.99]))
    URG_M = float(np.median([r["maker_pen_rt_median"] for r in urg if r["quantile"] == 0.99]))
    tiers = {"MAJOR_TIGHT (BTC/ETH, spread<0.2bps)": 0.05,
             "LIQUID_ALT (SOL/XRP/LINK/DOGE, 0.8-1.5bps)": 1.2,
             "MID_ALT (ORDI/TIA/PYTH, 2-3bps)": 2.8,
             "WIDE_ALT (ADA/AR/FET, 5-7bps)": 5.5}
    cm_tab = []
    for name, sp in tiers.items():
        cm_tab.append(dict(tier=name, spread_bps=sp,
                           cost_taker_rt=round(tier_cost(sp, "taker", False), 2),
                           cost_maker_rt=round(tier_cost(sp, "maker", False), 2),
                           cost_taker_rt_urgent=round(tier_cost(sp, "taker", True), 2),
                           cost_maker_rt_urgent=round(tier_cost(sp, "maker", True), 2),
                           convention_rt=14.0))
    out["replacement_cost_model"] = {"urgency_penalty_taker_rt": URG_T,
                                     "urgency_penalty_maker_rt": URG_M,
                                     "tiers": cm_tab}
    print("\n=== REPLACEMENT COST MODEL (round-trip bps) ===")
    print(pd.DataFrame(cm_tab).to_string())

    json.dump(out, open(f"{S}/bridge_cost.json", "w"), indent=1, default=float)
    P.to_csv(f"{S}/per_symbol_cost.csv", index=False)
    print("\nwrote", f"{S}/bridge_cost.json")


if __name__ == "__main__":
    main()
