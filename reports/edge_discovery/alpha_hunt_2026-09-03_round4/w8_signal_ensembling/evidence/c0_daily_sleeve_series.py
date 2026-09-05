"""W8 Track C step 0 - build the DAILY return series of every sleeve, on ONE calendar,
so that Track A (event basis) and Track B (cross-sectional daily basis) can be combined
at the only unit they share: the calendar day (PREREGISTRATION.md section 1).

Track A sleeve series : daily mean of the declustered (L1) selected episodes' fwd_4h, in bps
                        GROSS. Cost model = fixed 14bps (28bps stress) PER EPISODE, so the
                        series stores gross + n_episodes and c1 applies the cost.
Track B sleeve series : per-rebalance (daily) decile long-short GROSS bps + the sleeve's OWN
                        measured turnover, so the cost is charged on the composite's real
                        turnover (mandate pitfall), at any cost level.

Re-executable. Reads only this worker's cached matrices + the read-only sources used by
a1/b1. Writes ONE compact parquet in evidence/ (< 1 MB).
"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from a1_track_a_ensemble import (APRIORI as APRIORI_A, causal_quantile_threshold, decluster_L1,
                                 _utc, BURN_EVENTS, OUTDIR)
from b1_track_b_ensemble import (APRIORI as APRIORI_B, build_features, xs_z, portfolio, COST_RT)

OUT = os.path.join(OUTDIR, "c0_daily_sleeves.parquet")


# ------------------------------------------------------------------ TRACK A -------------
def track_a_series():
    df = pd.read_parquet(os.path.join(OUTDIR, "a1_events.parquet"))
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df.sort_values("event_time").reset_index(drop=True)
    sigs = json.load(open(os.path.join(OUTDIR, "a1_signals.json")))["signals"]
    zmat = np.load(os.path.join(OUTDIR, "a1_zmat.npy"))
    wfs = np.load(os.path.join(OUTDIR, "a1_wfsign.npy"))
    K = len(sigs)
    apr = np.array([APRIORI_A[s] for s in sigs], float)
    mo = df["event_time"].dt.to_period("M").astype(str).values
    y = df["y_bps"].values

    def comp(sign_mat, W=None):
        s = np.where(np.isfinite(zmat), zmat, np.nan) * sign_mat
        if W is not None:
            s = s * W
        n_ok = np.isfinite(s).sum(axis=1)
        out = np.nansum(np.where(np.isfinite(s), s, 0.0), axis=1)
        if W is None:
            out = out / np.maximum(n_ok, 1)
        out[n_ok < 0.6 * K] = np.nan
        return out

    # E4 CONFIDENCE_IC_WF weights, recomputed exactly as in a2 (expanding, month blocks)
    umo = pd.unique(mo)
    mo_idx = {m: i for i, m in enumerate(umo)}
    mi = np.array([mo_idx[m] for m in mo])
    ep_ret = y - COST_RT
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
            ic = stats.spearmanr(zj[good], yj[good]).correlation
            W_cf[cur, j] = max(0.0, ic if np.isfinite(ic) else 0.0)
    sw = W_cf.sum(axis=1)
    Wn = np.where(sw[:, None] > 0, W_cf / np.maximum(sw[:, None], 1e-12), np.nan)

    scores = {
        "A_EW_APRIORI": comp(np.tile(apr, (len(df), 1))),
        "A_EW_WALKFORWARD": comp(wfs),
        "A_CONFIDENCE_IC_WF": comp(wfs, Wn),
    }
    rows = []
    for name, sc in scores.items():
        for q in (0.90, 0.80):
            thr = causal_quantile_threshold(sc, mo, q)
            m = np.isfinite(sc) & np.isfinite(thr) & (sc >= thr)
            if m.sum() < 30:
                continue
            t = df["event_time"].values[m]
            keep = decluster_L1(t, df["symbol"].values[m])
            tt = _utc(t[keep])
            g = pd.DataFrame({"day": pd.to_datetime(tt.date), "g": y[m][keep]}) \
                .groupby("day")["g"].agg(["mean", "size"]).reset_index()
            rows.append(pd.DataFrame({
                "day": g["day"], "sleeve": f"{name}_q{int(q * 100)}", "track": "A",
                "gross_bps": g["mean"].values, "turnover": np.nan,
                "n_episodes": g["size"].values, "cost_model": "PER_EPISODE"}))
    # best single Track A component (walk-forward-signed), for reference
    for s in ["ret_24h", "px_ret_1h", "dist_low_7d"]:
        j = sigs.index(s)
        sc = wfs[:, j] * zmat[:, j]
        thr = causal_quantile_threshold(sc, mo, 0.90)
        m = np.isfinite(sc) & np.isfinite(thr) & (sc >= thr)
        t = df["event_time"].values[m]
        keep = decluster_L1(t, df["symbol"].values[m])
        tt = _utc(t[keep])
        g = pd.DataFrame({"day": pd.to_datetime(tt.date), "g": y[m][keep]}) \
            .groupby("day")["g"].agg(["mean", "size"]).reset_index()
        rows.append(pd.DataFrame({
            "day": g["day"], "sleeve": f"A_SIGNAL_{s}_q90", "track": "A",
            "gross_bps": g["mean"].values, "turnover": np.nan,
            "n_episodes": g["size"].values, "cost_model": "PER_EPISODE"}))
    return pd.concat(rows, ignore_index=True)


# ------------------------------------------------------------------ TRACK B -------------
def track_b_series():
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

    rows, series = [], {}
    for c in cols:
        sgn = APRIORI_B[c]
        pf = portfolio(days, by_sym, {d: sgn * by_z[d][c] for d in days}, by_fwd)
        series[c] = pd.Series(pf["gross_bps"].values - pf["turnover"].values * COST_RT,
                              index=pd.to_datetime(pf["date"]))
        rows.append(pd.DataFrame({"day": pd.to_datetime(pf["date"]),
                                  "sleeve": f"B_SIGNAL_{c}", "track": "B",
                                  "gross_bps": pf["gross_bps"].values,
                                  "turnover": pf["turnover"].values,
                                  "n_episodes": pf["k"].values,
                                  "cost_model": "TURNOVER"}))

    def mk(scorefn, name):
        sc = {}
        for d in days:
            M = zstack[d] * scorefn(d)
            nok = np.isfinite(M).sum(axis=1)
            v = np.nansum(np.where(np.isfinite(M), M, 0.0), axis=1) / np.maximum(nok, 1)
            v[nok < 0.6 * len(cols)] = np.nan
            sc[d] = v
        pf = portfolio(days, by_sym, sc, by_fwd)
        return pd.DataFrame({"day": pd.to_datetime(pf["date"]), "sleeve": name, "track": "B",
                             "gross_bps": pf["gross_bps"].values,
                             "turnover": pf["turnover"].values,
                             "n_episodes": pf["k"].values, "cost_model": "TURNOVER"})

    rows.append(mk(lambda d: apr, "B_EW_APRIORI"))
    obs_per_year = 365.25
    wf_sign = {}
    for c in cols:
        s = series[c]
        wf_sign[c] = np.sign(s.expanding(min_periods=max(8, int(obs_per_year / 4)))
                             .mean().shift(1)).reindex(s.index)

    def wfsgn(d):
        dd = pd.Timestamp(d)
        out = np.array([(wf_sign[c].get(dd, 0.0) if dd in wf_sign[c].index else 0.0)
                        for c in cols], float)
        return np.where(np.isfinite(out) & (out != 0), out, apr)
    rows.append(mk(wfsgn, "B_EW_WALKFORWARD"))

    W = {}
    for c in cols:
        s = series[c]
        m = s.expanding(min_periods=max(8, int(obs_per_year / 4))).mean().shift(1)
        sd = s.expanding(min_periods=max(8, int(obs_per_year / 4))).std().shift(1)
        W[c] = (m / sd).clip(lower=0.0)

    def wfn(d):
        dd = pd.Timestamp(d)
        out = np.array([(W[c].get(dd, np.nan) if dd in W[c].index else np.nan) for c in cols],
                       float)
        out = np.where(np.isfinite(out), out, 0.0)
        return apr * out / out.sum() if out.sum() > 0 else apr * 0.0
    rows.append(mk(wfn, "B_CONFIDENCE_WF"))
    return pd.concat(rows, ignore_index=True)


if __name__ == "__main__":
    a = track_a_series()
    print("track A sleeves:", a["sleeve"].nunique(), "rows", len(a))
    b = track_b_series()
    print("track B sleeves:", b["sleeve"].nunique(), "rows", len(b))
    out = pd.concat([a, b], ignore_index=True)
    out.to_parquet(OUT)
    print("wrote", OUT, os.path.getsize(OUT) / 1e6, "MB")
    print(out.groupby("sleeve")["day"].agg(["size", "min", "max"]))
