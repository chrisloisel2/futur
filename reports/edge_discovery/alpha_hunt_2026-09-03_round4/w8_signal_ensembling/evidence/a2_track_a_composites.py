"""W8 Track A step 2 - correlation matrix, composites (E1-E6), full BRIEFING-section-2 gate.
Depends on a1_track_a_ensemble.py's cached causal matrices. Read-only on data/."""
import json, os, sys
import numpy as np
import pandas as pd
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from a1_track_a_ensemble import (_utc, APRIORI, causal_z, causal_sign, causal_quantile_threshold,
                                 decluster_L1, gate, verdict_of, COST_RT, EV, OUTDIR,
                                 BURN_EVENTS)

QS = [0.90, 0.80, 0.70]


def build():
    df = pd.read_parquet(EV)
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df.sort_values("event_time").reset_index(drop=True)
    df = df[df["fwd_4h"].notna()].reset_index(drop=True)
    df["ASIA_SESSION"] = (df["hour_utc"] < 8).astype(float)
    df["y_bps"] = df["fwd_4h"] * 10000.0
    sigs = json.load(open(os.path.join(OUTDIR, "a1_signals.json")))["signals"]
    zmat = np.load(os.path.join(OUTDIR, "a1_zmat.npy"))
    wfs = np.load(os.path.join(OUTDIR, "a1_wfsign.npy"))
    return df, sigs, zmat, wfs


def sel_metrics(df, score, q, label, net_extra=COST_RT):
    """Select top-q of the score using a CAUSAL threshold; run the full gate."""
    mo = df["event_time"].dt.to_period("M").astype(str).values
    thr = causal_quantile_threshold(score, mo, q)
    m = np.isfinite(score) & np.isfinite(thr) & (score >= thr)
    if m.sum() < 30:
        return None, m
    pop = np.isfinite(thr)          # same evaluable window, unconditional population
    pop_mean = float(df["y_bps"].values[pop].mean() - net_extra)
    g = gate(df["y_bps"].values[m] - net_extra, df["event_time"].values[m],
             df["symbol"].values[m], label,
             ev_gross=df["y_bps"].values[m], pop_mean_bps=pop_mean)
    g["selection_quantile"] = q
    g["verdict"] = verdict_of(g)
    return g, m


def daily_series(df, mask, net_extra=COST_RT):
    t = df["event_time"].values[mask]
    keep = decluster_L1(t, df["symbol"].values[mask])
    d = pd.Series(_utc(t[keep]).date)
    x = df["y_bps"].values[mask][keep] - net_extra
    return pd.Series(x).groupby(d.values).mean()


def monthly_series(df, mask, net_extra=COST_RT):
    t = df["event_time"].values[mask]
    keep = decluster_L1(t, df["symbol"].values[mask])
    m = pd.Series(_utc(t[keep]).to_period("M").astype(str))
    x = df["y_bps"].values[mask][keep] - net_extra
    return pd.Series(x).groupby(m.values).mean()


def main():
    df, sigs, zmat, wfs = build()
    K = len(sigs)
    apr = np.array([APRIORI[s] for s in sigs], dtype=float)
    mo = df["event_time"].dt.to_period("M").astype(str).values
    res = {"track": "A", "population": "liq_cascade_dataset.parquet (49 sym, LONG@event, hold 4h)",
           "n_events_evaluable": int(len(df)), "signals": sigs}

    # ---------------- E1 : correlation matrices -----------------------------------------
    ok = np.isfinite(zmat).all(axis=1)
    zs = zmat[ok] * apr                       # sign-aligned a-priori (orientation only)
    score_corr = np.corrcoef(zs, rowvar=False)
    res["E1_score_correlation"] = {"matrix": np.round(score_corr, 4).tolist(),
                                   "labels": sigs, "n_events_complete": int(ok.sum())}
    off = score_corr[np.triu_indices(K, 1)]
    res["E1_score_corr_summary"] = {
        "median_abs": float(np.median(np.abs(off))), "mean_abs": float(np.mean(np.abs(off))),
        "p90_abs": float(np.percentile(np.abs(off), 90)), "max_abs": float(np.max(np.abs(off))),
        "n_pairs_abs_gt_0.5": int((np.abs(off) > 0.5).sum()), "n_pairs": int(len(off))}

    # per-signal top-decile monthly return series -> return-correlation matrix
    ms, masks, per_signal = {}, {}, {}
    for j, s in enumerate(sigs):
        sc = wfs[:, j] * zmat[:, j]
        g, m = sel_metrics(df, sc, 0.90, f"SIGNAL::{s}::wf_sign::q90")
        if g is None:
            continue
        per_signal[s] = g
        masks[s] = m
        ms[s] = monthly_series(df, m)
    mdf = pd.DataFrame(ms)
    ret_corr = mdf.corr(min_periods=12)
    res["E1_return_correlation_monthly"] = {
        "labels": list(mdf.columns), "matrix": np.round(ret_corr.values, 4).tolist(),
        "n_months": int(mdf.shape[0])}
    offr = ret_corr.values[np.triu_indices(len(ret_corr), 1)]
    offr = offr[np.isfinite(offr)]
    res["E1_return_corr_summary"] = {
        "median_abs": float(np.median(np.abs(offr))), "mean_abs": float(np.mean(np.abs(offr))),
        "p90_abs": float(np.percentile(np.abs(offr), 90)), "max_abs": float(np.max(np.abs(offr))),
        "n_pairs_abs_gt_0.5": int((np.abs(offr) > 0.5).sum()), "n_pairs": int(len(offr))}
    res["per_signal_gate"] = per_signal

    # effective number of independent bets from the return-correlation eigenvalues
    C = ret_corr.fillna(0).values
    np.fill_diagonal(C, 1.0)
    ev = np.linalg.eigvalsh(C)
    ev = ev[ev > 1e-9]
    res["E1_effective_independent_bets"] = {
        "K": int(len(C)),
        "ENB_eig_entropy": float(np.exp(-(ev / ev.sum() * np.log(ev / ev.sum())).sum())),
        "ENB_1_over_sum_rho": float(len(C) / (1 + (len(C) - 1) * np.mean(offr))),
        "mean_pairwise_rho": float(np.mean(offr))}

    # ---------------- E2 : naive composites ---------------------------------------------
    def comp(sign_mat):
        s = np.where(np.isfinite(zmat), zmat, np.nan) * sign_mat
        n_ok = np.isfinite(s).sum(axis=1)
        out = np.nansum(np.where(np.isfinite(s), s, 0.0), axis=1) / np.maximum(n_ok, 1)
        out[n_ok < 0.6 * K] = np.nan
        return out

    comp_apr = comp(np.tile(apr, (len(df), 1)))
    comp_wf = comp(wfs)
    res["E2_composites"] = {}
    for name, sc in [("EW_APRIORI", comp_apr), ("EW_WALKFORWARD", comp_wf)]:
        for q in QS:
            g, m = sel_metrics(df, sc, q, f"{name}::q{int(q*100)}")
            if g:
                g["verdict"] = verdict_of(g)
                res["E2_composites"][f"{name}_q{int(q*100)}"] = g
                if abs(q - 0.90) < 1e-9:
                    masks[name] = m

    # ---------------- E3 : vote / concordance -------------------------------------------
    signed = np.where(np.isfinite(zmat), zmat, np.nan) * wfs
    agree = (signed > 0).sum(axis=1).astype(float)
    navail = np.isfinite(signed).sum(axis=1)
    agree[navail < 0.6 * K] = np.nan
    res["E3_vote"] = {}
    for kmin in range(1, K + 1):
        m = np.isfinite(agree) & (agree >= kmin)
        if m.sum() < 30:
            continue
        thr_ok = np.isfinite(causal_quantile_threshold(agree, mo, 0.5))
        m = m & thr_ok
        if m.sum() < 30:
            continue
        pop_mean = float(df["y_bps"].values[thr_ok].mean() - COST_RT)
        g = gate(df["y_bps"].values[m] - COST_RT, df["event_time"].values[m],
                 df["symbol"].values[m], f"VOTE_K>={kmin}", pop_mean_bps=pop_mean)
        g["verdict"] = verdict_of(g)
        g["K_min"] = kmin
        res["E3_vote"][f"K{kmin}"] = g

    # ---------------- E4 : risk / confidence weighted (expanding, OOS only) -------------
    umo = pd.unique(mo)
    mo_idx = {m: i for i, m in enumerate(umo)}
    mi = np.array([mo_idx[m] for m in mo])
    ep_ret = df["y_bps"].values - COST_RT
    W_iv = np.zeros_like(zmat)
    W_cf = np.zeros_like(zmat)
    for b in range(len(umo)):
        prior = mi < b
        if prior.sum() < BURN_EVENTS:
            continue
        cur = mi == b
        for j in range(K):
            zj = zmat[prior, j] * wfs[prior, j]
            yj = ep_ret[prior]
            good = np.isfinite(zj) & np.isfinite(yj)
            if good.sum() < 500:
                continue
            # component "return series": its own signed-score-weighted episode returns
            r = zj[good] * yj[good]
            sd = r.std(ddof=1)
            W_iv[cur, j] = 1.0 / sd if sd > 0 else 0.0
            ic = stats.spearmanr(zj[good], yj[good]).correlation
            W_cf[cur, j] = max(0.0, ic if np.isfinite(ic) else 0.0)
    for nm, W in [("INVVOL_WF", W_iv), ("CONFIDENCE_IC_WF", W_cf)]:
        sw = W.sum(axis=1)
        Wn = np.where(sw[:, None] > 0, W / np.maximum(sw[:, None], 1e-12), np.nan)
        s = np.where(np.isfinite(zmat), zmat, np.nan) * wfs * Wn
        n_ok = np.isfinite(s).sum(axis=1)
        sc = np.nansum(np.where(np.isfinite(s), s, 0.0), axis=1)
        sc[(n_ok < 0.6 * K) | (sw <= 0)] = np.nan
        for q in QS:
            g, m = sel_metrics(df, sc, q, f"{nm}::q{int(q*100)}")
            if g:
                g["verdict"] = verdict_of(g)
                res.setdefault("E4_weighted", {})[f"{nm}_q{int(q*100)}"] = g
                if abs(q - 0.90) < 1e-9:
                    masks[nm] = m

    # ---------------- E5 : orthogonalisation --------------------------------------------
    ortho = {}
    for j, s in enumerate(sigs):
        resid = np.full(len(df), np.nan)
        others = [k for k in range(K) if k != j]
        for b in range(len(umo)):
            prior = mi < b
            cur = mi == b
            if prior.sum() < BURN_EVENTS:
                continue
            Xp = zmat[prior][:, others]; yp = zmat[prior][:, j]
            gp = np.isfinite(Xp).all(axis=1) & np.isfinite(yp)
            if gp.sum() < 500:
                continue
            A = np.c_[np.ones(gp.sum()), Xp[gp]]
            try:
                beta = np.linalg.lstsq(A, yp[gp], rcond=None)[0]
            except Exception:
                continue
            Xc = zmat[cur][:, others]; yc = zmat[cur][:, j]
            gc = np.isfinite(Xc).all(axis=1) & np.isfinite(yc)
            idx = np.where(cur)[0][gc]
            resid[idx] = yc[gc] - (np.c_[np.ones(gc.sum()), Xc[gc]] @ beta)
        sd = np.nanstd(resid)
        rz = resid / sd if sd > 0 else resid
        sgn, _ = causal_sign(rz, df["y_bps"].values, mo)
        g, _ = sel_metrics(df, sgn * rz, 0.90, f"ORTHO::{s}::q90")
        base = per_signal.get(s)
        ortho[s] = {
            "raw_net_bps": (base or {}).get("net_bps"),
            "raw_t_declustered": (base or {}).get("t_stat_declustered"),
            "ortho_net_bps": (g or {}).get("net_bps"),
            "ortho_t_declustered": (g or {}).get("t_stat_declustered"),
            "r2_explained_by_others": float(1 - np.nanvar(resid) / np.nanvar(zmat[:, j]))
            if np.isfinite(np.nanvar(resid)) else None,
            "n_ortho": (g or {}).get("n_independent_L1"),
        }
        b_t = ortho[s]["raw_t_declustered"]; o_t = ortho[s]["ortho_t_declustered"]
        ortho[s]["classification"] = (
            "DUPLICATE" if (b_t is not None and o_t is not None and b_t > 1.5 and o_t < 1.0)
            else "INDEPENDENT_CONTRIBUTOR" if (o_t is not None and o_t > 1.5)
            else "NO_EDGE_EITHER_WAY")
    res["E5_orthogonalisation"] = ortho

    # ---------------- E6 : composite vs walk-forward-best component ---------------------
    # WF best component: at each month, the component with the best trailing episode t-stat
    comp_ep = {}
    for s, m in masks.items():
        if s not in per_signal:
            continue
        t = df["event_time"].values[m]
        keep = decluster_L1(t, df["symbol"].values[m])
        comp_ep[s] = (_utc(t[keep]), df["y_bps"].values[m][keep] - COST_RT)
    wf_pick, wf_ret, wf_t = [], [], []
    for b in range(len(umo)):
        cutoff = pd.Period(umo[b], freq="M").start_time.tz_localize("UTC")
        best, bestv = None, -np.inf
        for s, (tt, xx) in comp_ep.items():
            pr = tt < cutoff
            if pr.sum() < 100:
                continue
            v = xx[pr].mean() / (xx[pr].std(ddof=1) / np.sqrt(pr.sum())) if xx[pr].std(ddof=1) > 0 else -np.inf
            if v > bestv:
                bestv, best = v, s
        if best is None:
            continue
        tt, xx = comp_ep[best]
        cur = (tt >= cutoff) & (tt < (pd.Period(umo[b], freq="M").end_time.tz_localize("UTC")))
        if cur.sum() == 0:
            continue
        wf_pick.append({"month": umo[b], "picked": best, "n": int(cur.sum())})
        wf_ret.append(pd.Series(xx[cur], index=tt[cur]))
        wf_t.append(tt[cur])
    if wf_ret:
        allr = pd.concat(wf_ret).sort_index()
        d = _utc(allr.index).date
        dser = pd.Series(allr.values).groupby(d).mean()
        mser = pd.Series(allr.values).groupby(
            pd.PeriodIndex(allr.index, freq="M").astype(str)).mean()
        from a1_track_a_ensemble import eta_from_series, block_bootstrap_ci
        rate_dw = None
        tmax = allr.index.max(); cut = tmax - pd.Timedelta(days=183)
        rec = allr.index >= cut
        span_w = max((tmax - cut).days / 7.0, 1e-9)
        rate_dw = len(set(_utc(allr.index[rec]).date)) / span_w
        res["E6_walkforward_best_component"] = {
            "picks": wf_pick[-24:],
            "distinct_components_picked": sorted({p["picked"] for p in wf_pick}),
            "n_episodes": int(len(allr)), "net_bps": float(allr.mean()),
            "daily_net_bps": float(dser.mean()),
            "t_declustered": float(dser.mean() / (dser.std(ddof=1) / np.sqrt(len(dser)))),
            "eta": eta_from_series(dser.values, dser.index.values,
                                   obs_per_year=rate_dw * 52.1775)}
    # hindsight-best component (upper bound, NOT attainable)
    if per_signal:
        hb = max(per_signal.items(),
                 key=lambda kv: (kv[1].get("t_stat_declustered") or -9))
        res["E6_hindsight_best_component_UNATTAINABLE"] = {"signal": hb[0], "gate": hb[1]}

    with open(os.path.join(OUTDIR, "a2_track_a_results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("wrote a2_track_a_results.json")
    print("\n== E1 corr summary (scores) ==", json.dumps(res["E1_score_corr_summary"], indent=1))
    print("== E1 corr summary (returns) ==", json.dumps(res["E1_return_corr_summary"], indent=1))
    print("== ENB ==", json.dumps(res["E1_effective_independent_bets"], indent=1))
    print("\n== E2 ==")
    for k, v in res["E2_composites"].items():
        print(f"  {k:26s} n1={v['n_independent_L1']:5d} net={v['net_bps']:+7.2f} "
              f"exc={v.get('excess_vs_population_bps', float('nan')):+7.2f} "
              f"t_dcl={v['t_stat_declustered']:+5.2f} SRann={v.get('sr_annualised') or float('nan'):.2f} "
              f"ETA={v.get('eta_forward_confirmation_years')} {v['verdict']}")


if __name__ == "__main__":
    main()
