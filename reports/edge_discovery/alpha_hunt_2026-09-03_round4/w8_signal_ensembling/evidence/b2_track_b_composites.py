"""W8 Track B step 2 - correlation matrix + composites E1..E6 on the cross-sectional basis."""
import json, os, sys
import numpy as np
import pandas as pd
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from b1_track_b_ensemble import (APRIORI, build_features, xs_z, portfolio, gate_series,
                                 verdict, eta_of, OUTDIR, COST_RT)

HORIZONS = {"weekly": 7, "daily": 1}


def main():
    p = build_features()
    cols = list(APRIORI)
    Z = xs_z(p, cols)
    for c in cols:
        p["z_" + c] = Z[c]
    # forward returns (strictly forward, non-overlapping when sampled every h days)
    p = p.sort_values(["symbol", "day"]).reset_index(drop=True)
    g = p.groupby("symbol", sort=False)
    for h in set(HORIZONS.values()):
        p[f"fwd_{h}"] = g["close"].transform(lambda s: s.shift(-h) / s - 1.0)
    print("panel rows", len(p), "eligible", int(p["eligible"].sum()),
          "days", p["day"].nunique())

    res = {"track": "B", "population": "data_v2 perp_ohlcv daily panel, causal liq>=$1M/30d-median",
           "signals": cols, "n_rows": int(len(p)), "n_days": int(p["day"].nunique())}

    for hname, h in HORIZONS.items():
        sub = p[p["eligible"] & p[f"fwd_{h}"].notna()].copy()
        alldays = np.sort(sub["day"].unique())
        # non-overlapping rebalance grid
        days = alldays[::h]
        obs_per_year = 365.25 / h
        by_d_sym, by_d_fwd, by_d_z = {}, {}, {}
        gsub = sub.groupby("day")
        for d in days:
            try:
                blk = gsub.get_group(d)
            except KeyError:
                continue
            by_d_sym[d] = blk["symbol"].values
            by_d_fwd[d] = blk[f"fwd_{h}"].values
            by_d_z[d] = {c: blk["z_" + c].values for c in cols}
        days = [d for d in days if d in by_d_sym]

        # ---- per-signal portfolios (a-priori sign, decile long-short) -------------------
        per_sig, series = {}, {}
        for c in cols:
            sgn = APRIORI[c]
            sc = {d: sgn * by_d_z[d][c] for d in days}
            pf = portfolio(days, by_d_sym, sc, by_d_fwd)
            gg = gate_series(pf, f"SIGNAL::{c}::LS_decile::{hname}", obs_per_year)
            if gg:
                gg["verdict"] = verdict(gg)
                gg["apriori_sign"] = sgn
                per_sig[c] = gg
                s = pd.Series(pf["gross_bps"].values - pf["turnover"].values * COST_RT,
                              index=pd.to_datetime(pf["date"]))
                series[c] = s
        res[f"{hname}_per_signal"] = per_sig

        # ---- E1 correlation ------------------------------------------------------------
        sdf = pd.DataFrame(series)
        cm = sdf.corr()
        off = cm.values[np.triu_indices(len(cm), 1)]
        off = off[np.isfinite(off)]
        C = cm.fillna(0).values; np.fill_diagonal(C, 1.0)
        ev = np.linalg.eigvalsh(C); ev = ev[ev > 1e-9]
        res[f"{hname}_E1_return_correlation"] = {
            "labels": list(cm.columns), "matrix": np.round(cm.values, 4).tolist(),
            "n_periods": int(sdf.shape[0]),
            "summary": {"median_abs": float(np.median(np.abs(off))),
                        "mean_rho": float(np.mean(off)),
                        "mean_abs": float(np.mean(np.abs(off))),
                        "max_abs": float(np.max(np.abs(off))),
                        "n_pairs_abs_gt_0.5": int((np.abs(off) > 0.5).sum()),
                        "n_pairs": int(len(off))},
            "ENB_eig_entropy": float(np.exp(-(ev / ev.sum() * np.log(ev / ev.sum())).sum())),
            "ENB_1_over_sum_rho": float(len(C) / (1 + (len(C) - 1) * np.mean(off)))}
        # score-level cross-sectional correlation (mean over days)
        zz = sub[["z_" + c for c in cols]].values * np.array([APRIORI[c] for c in cols])
        okr = np.isfinite(zz).all(axis=1)
        res[f"{hname}_E1_score_correlation"] = {
            "labels": cols,
            "matrix": np.round(np.corrcoef(zz[okr], rowvar=False), 4).tolist(),
            "n_rows": int(okr.sum())}

        # ---- E2 naive composite (a-priori) + walk-forward-sign composite ---------------
        # walk-forward sign from expanding IC of each signal's own LS series
        wf_sign = {}
        for c in cols:
            s = series[c]
            exp_mean = s.expanding(min_periods=max(8, int(obs_per_year / 4))).mean().shift(1)
            wf_sign[c] = np.sign(exp_mean).reindex(s.index)
        comps = {}
        zstack = {d: np.column_stack([by_d_z[d][c] for c in cols]) for d in days}
        apr = np.array([APRIORI[c] for c in cols], float)

        def mk(scorefn, name):
            sc = {}
            for d in days:
                M = zstack[d] * scorefn(d)
                nok = np.isfinite(M).sum(axis=1)
                v = np.nansum(np.where(np.isfinite(M), M, 0.0), axis=1) / np.maximum(nok, 1)
                v[nok < 0.6 * len(cols)] = np.nan
                sc[d] = v
            pf = portfolio(days, by_d_sym, sc, by_d_fwd)
            gg = gate_series(pf, name, obs_per_year)
            if gg:
                gg["verdict"] = verdict(gg)
            return gg, pf

        gg, pf_apr = mk(lambda d: apr, f"EW_APRIORI::LS_decile::{hname}")
        comps["EW_APRIORI"] = gg
        ser_comp = pd.Series(pf_apr["gross_bps"].values - pf_apr["turnover"].values * COST_RT,
                             index=pd.to_datetime(pf_apr["date"]))

        def wfsgn(d):
            dd = pd.Timestamp(d)
            out = np.array([(wf_sign[c].get(dd, 0.0) if dd in wf_sign[c].index else 0.0)
                            for c in cols], float)
            return np.where(np.isfinite(out) & (out != 0), out, apr)
        gg, _ = mk(wfsgn, f"EW_WALKFORWARD::LS_decile::{hname}")
        comps["EW_WALKFORWARD"] = gg

        # long-only variants (SHORT_REJECTED project rule)
        sc_apr = {}
        for d in days:
            M = zstack[d] * apr
            nok = np.isfinite(M).sum(axis=1)
            v = np.nansum(np.where(np.isfinite(M), M, 0.0), axis=1) / np.maximum(nok, 1)
            v[nok < 0.6 * len(cols)] = np.nan
            sc_apr[d] = v
        pf_lo = portfolio(days, by_d_sym, sc_apr, by_d_fwd, long_short=False)
        gg = gate_series(pf_lo, f"EW_APRIORI::LONGONLY_decile_excess::{hname}", obs_per_year)
        if gg:
            gg["verdict"] = verdict(gg)
        comps["EW_APRIORI_LONGONLY"] = gg
        res[f"{hname}_E2_composites"] = comps

        # ---- E3 vote -------------------------------------------------------------------
        vote = {}
        for kmin in range(1, len(cols) + 1):
            sc = {}
            for d in days:
                M = zstack[d] * apr
                agree = (M > 0).sum(axis=1).astype(float)
                nok = np.isfinite(M).sum(axis=1)
                agree[nok < 0.6 * len(cols)] = np.nan
                sc[d] = agree
            pf = portfolio(days, by_d_sym, sc, by_d_fwd, decile=0.1)
            gg = gate_series(pf, f"VOTE_K>={kmin}::{hname}", obs_per_year)
            break  # vote as a score is rank-equivalent for all kmin -> use threshold form
        for kmin in range(1, len(cols) + 1):
            sc = {}
            for d in days:
                M = zstack[d] * apr
                agree = (M > 0).sum(axis=1).astype(float)
                nok = np.isfinite(M).sum(axis=1)
                sel = np.where((agree >= kmin) & (nok >= 0.6 * len(cols)), agree, np.nan)
                sc[d] = sel
            n_sel = np.mean([np.isfinite(sc[d]).sum() for d in days])
            if n_sel < 20:
                continue
            pf = portfolio(days, by_d_sym, sc, by_d_fwd, decile=0.99, long_short=False)
            gg = gate_series(pf, f"VOTE_K>={kmin}::LONGONLY_excess::{hname}", obs_per_year)
            if gg:
                gg["verdict"] = verdict(gg)
                gg["K_min"] = kmin
                gg["mean_names_selected"] = float(n_sel)
                vote[f"K{kmin}"] = gg
        res[f"{hname}_E3_vote"] = vote

        # ---- E4 walk-forward inverse-vol / confidence weights --------------------------
        w4 = {}
        for wname in ["INVVOL", "CONFIDENCE"]:
            W = {}
            for c in cols:
                s = series[c]
                if wname == "INVVOL":
                    v = s.expanding(min_periods=max(8, int(obs_per_year / 4))).std().shift(1)
                    W[c] = (1.0 / v).replace([np.inf, -np.inf], np.nan)
                else:
                    m = s.expanding(min_periods=max(8, int(obs_per_year / 4))).mean().shift(1)
                    sd = s.expanding(min_periods=max(8, int(obs_per_year / 4))).std().shift(1)
                    W[c] = (m / sd).clip(lower=0.0)
            def wfn(d, W=W):
                dd = pd.Timestamp(d)
                out = np.array([(W[c].get(dd, np.nan) if dd in W[c].index else np.nan)
                                for c in cols], float)
                out = np.where(np.isfinite(out), out, 0.0)
                if out.sum() <= 0:
                    return apr * 0.0
                return apr * out / out.sum()
            gg, _ = mk(wfn, f"{wname}_WF::LS_decile::{hname}")
            if gg:
                w4[wname] = gg
        res[f"{hname}_E4_weighted"] = w4

        # ---- E5 orthogonalisation (causal: betas from strictly prior rebalances) -------
        ortho = {}
        Zall = sub[["z_" + c for c in cols]].values
        dvals = sub["day"].values
        for j, c in enumerate(cols):
            others = [k for k in range(len(cols)) if k != j]
            sc, r2s = {}, []
            for i, d in enumerate(days):
                prior = dvals < d
                if prior.sum() < 5000:
                    continue
                Xp, yp = Zall[prior][:, others], Zall[prior][:, j]
                gp = np.isfinite(Xp).all(axis=1) & np.isfinite(yp)
                if gp.sum() < 2000:
                    continue
                A = np.c_[np.ones(gp.sum()), Xp[gp]]
                beta = np.linalg.lstsq(A, yp[gp], rcond=None)[0]
                Xc = zstack[d][:, others]; yc = zstack[d][:, j]
                gc = np.isfinite(Xc).all(axis=1) & np.isfinite(yc)
                v = np.full(len(yc), np.nan)
                v[gc] = yc[gc] - (np.c_[np.ones(gc.sum()), Xc[gc]] @ beta)
                sc[d] = APRIORI[c] * v
                r2s.append(1 - np.var(yp[gp] - A @ beta) / np.var(yp[gp]))
            dd = [d for d in days if d in sc]
            if len(dd) < 20:
                continue
            pf = portfolio(dd, by_d_sym, sc, by_d_fwd)
            gg = gate_series(pf, f"ORTHO::{c}::{hname}", obs_per_year)
            base = per_sig.get(c)
            ortho[c] = {"r2_explained_by_others": float(np.mean(r2s)) if r2s else None,
                        "raw_net_bps": (base or {}).get("net_bps"),
                        "raw_t": (base or {}).get("t_stat_declustered"),
                        "ortho_net_bps": (gg or {}).get("net_bps"),
                        "ortho_t": (gg or {}).get("t_stat_declustered")}
            bt, ot = ortho[c]["raw_t"], ortho[c]["ortho_t"]
            ortho[c]["classification"] = (
                "DUPLICATE" if (bt and ot is not None and bt > 1.5 and ot < 1.0)
                else "INDEPENDENT_CONTRIBUTOR" if (ot is not None and ot > 1.5)
                else "NO_EDGE_EITHER_WAY")
        res[f"{hname}_E5_orthogonalisation"] = ortho

        # ---- E6 composite vs walk-forward-best component -------------------------------
        sdf2 = sdf.copy()
        exp_t = sdf2.expanding(min_periods=max(12, int(obs_per_year / 2))).apply(
            lambda s: s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if s.std(ddof=1) > 0 else np.nan,
            raw=False).shift(1)
        pick = exp_t.idxmax(axis=1)
        wfbest = pd.Series(
            [sdf2.loc[i, pick.loc[i]] if isinstance(pick.loc[i], str) else np.nan
             for i in sdf2.index], index=sdf2.index).dropna()
        hind = max(per_sig.items(), key=lambda kv: (kv[1]["t_stat_declustered"] or -9))
        res[f"{hname}_E6"] = {
            "wf_best_component": {
                "n": int(len(wfbest)), "net_bps": float(wfbest.mean()),
                "t": float(wfbest.mean() / (wfbest.std(ddof=1) / np.sqrt(len(wfbest))))
                if len(wfbest) > 2 and wfbest.std(ddof=1) > 0 else None,
                "eta": eta_of(wfbest.values, obs_per_year),
                "distinct_picks": sorted(set(pick.dropna().astype(str)))},
            "hindsight_best_component_UNATTAINABLE": {
                "signal": hind[0], "net_bps": hind[1]["net_bps"],
                "t": hind[1]["t_stat_declustered"],
                "eta_years": hind[1]["eta_forward_confirmation_years"]},
            "composite_EW_APRIORI": {
                "net_bps": comps["EW_APRIORI"]["net_bps"] if comps["EW_APRIORI"] else None,
                "t": comps["EW_APRIORI"]["t_stat_declustered"] if comps["EW_APRIORI"] else None,
                "eta_years": comps["EW_APRIORI"]["eta_forward_confirmation_years"]
                if comps["EW_APRIORI"] else None}}
        # cache composite series for Track C
        ser_comp.to_frame("net_bps").to_parquet(
            os.path.join(OUTDIR, f"b2_composite_series_{hname}.parquet"))
        print(f"[{hname}] done, rebalances={len(days)}")

    with open(os.path.join(OUTDIR, "b2_track_b_results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("wrote b2_track_b_results.json")


if __name__ == "__main__":
    main()
