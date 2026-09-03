"""Shared helpers for worker V2 (wave 2): population build, declustering, bootstrap.
Independent reimplementation — no code from src/institutional/engines/liq_cascade/*_variant.py.
"""
import json, os
import numpy as np
import pandas as pd

ROOT = "/home/qbee/futur"
SCRATCH = "/tmp/claude-1000/-home-qbee-futur/df793692-b596-4e93-91e2-bc55f257c909/scratchpad/V2_FAR_FROM_LOW_BTC_LEAD"
METRICS = f"{ROOT}/data/derivatives_backfill/binance_vision_metrics"
COST14, COST28 = 14.0, 28.0

def load_events(path):
    cols = ["event_time","kind","symbol","px","dist_low_24h","dist_low_7d","btc_ret_30m",
            "n_events_sym_24h","n_events_mktwide_30m","fwd_4h","label_full","oi_drop_z","px_ret_30m","vol_24h"]
    df = pd.read_parquet(path, columns=cols)
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df.sort_values("event_time").reset_index(drop=True)
    return df

def load_onboard():
    cal = pd.read_parquet(f"{ROOT}/data/listings_backfill/binance/listings_calendar.parquet",
                          columns=["symbol","onboard_ts"])
    cal["onboard_ts"] = pd.to_datetime(cal["onboard_ts"], utc=True)
    return dict(zip(cal["symbol"], cal["onboard_ts"]))

def first_bar(symbol):
    p = f"{METRICS}/{symbol}_metrics_5m.parquet"
    if not os.path.exists(p):
        return pd.NaT
    import duckdb
    con = duckdb.connect(); con.execute("SET memory_limit='1200MB'; SET threads=2;")
    t = con.execute(f"SELECT min(create_time) FROM read_parquet('{p}')").fetchone()[0]
    con.close()
    return pd.Timestamp(t).tz_convert("UTC") if t is not None else pd.NaT

def load_px(symbol):
    d = pd.read_parquet(f"{METRICS}/{symbol}_metrics_5m.parquet",
                        columns=["create_time","sum_open_interest","sum_open_interest_value"])
    d["create_time"] = pd.to_datetime(d["create_time"], utc=True)
    d = d.sort_values("create_time").reset_index(drop=True)
    oi = d["sum_open_interest"].astype(float); oiv = d["sum_open_interest_value"].astype(float)
    px = np.where(oi > 0, oiv / oi, np.nan)
    d["px"] = np.where(px > 0, px, np.nan)
    return d[["create_time","px"]]

# ---------- declustering ----------
def chain_clusters(times_ns, gap_ns):
    """Chronological chain: new cluster when gap to PREVIOUS event >= gap. times sorted."""
    if len(times_ns) == 0:
        return np.array([], dtype=int)
    gaps = np.diff(times_ns)
    return np.concatenate([[0], np.cumsum(gaps >= gap_ns)])

def decluster(df, ret_col):
    """df has event_time (UTC), symbol, ret_col (bps). Returns dict with L1/L2/L3 tables."""
    d = df.sort_values("event_time").copy()
    t = d["event_time"].values.astype("datetime64[ns]").astype(np.int64)
    # L1: same symbol < 24h chain
    l1 = np.zeros(len(d), dtype=int); off = 0
    for sym, idx in d.groupby("symbol").indices.items():
        idx = np.sort(idx)
        c = chain_clusters(t[idx], 24 * 3600 * 10**9)
        l1[idx] = c + off; off += c.max() + 1 if len(c) else 0
    d["L1"] = l1
    d["L2"] = d["event_time"].dt.floor("D").astype("int64")
    d["L3"] = chain_clusters(t, 4 * 3600 * 10**9)
    out = {"rows": d}
    for L in ["L1","L2","L3"]:
        g = d.groupby(L).agg(ret=(ret_col,"mean"), n=(ret_col,"size"),
                             t0=("event_time","min"), nsym=("symbol","nunique")).reset_index()
        out[L] = g
    return out

def stats_block(ep_ret, ep_dates=None):
    """ep_ret: episode-level net bps array. one-sample stats + block bootstrap (blocks = episodes)."""
    x = np.asarray(ep_ret, float); x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return {"n": int(n), "mean": float(x.mean()) if n else None}
    m = x.mean(); s = x.std(ddof=1); tt = m / (s / np.sqrt(n))
    rng = np.random.default_rng(12345)
    bs = np.array([x[rng.integers(0, n, n)].mean() for _ in range(5000)])
    return {"n": int(n), "mean": float(m), "sd": float(s), "t": float(tt),
            "boot_ci95": [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))],
            "boot_p_le0": float((bs <= 0).mean()), "boot_se": float(bs.std())}

def pf(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    g = x[x > 0].sum(); l = -x[x < 0].sum()
    return float(g / l) if l > 0 else None

def max_dd(x):
    c = np.cumsum(np.asarray(x, float)); peak = np.maximum.accumulate(c)
    return float((c - peak).min()) if len(c) else None

def welch(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    va, vb = a.var(ddof=1)/len(a), b.var(ddof=1)/len(b)
    return float((a.mean()-b.mean())/np.sqrt(va+vb))

def boot_diff_pooled(rows, arm_col, ret_col, cluster_col="L3", ndraw=5000, seed=7):
    """Block bootstrap of mean(A)-mean(B) resampling pooled clusters (both arms inside a cluster move together)."""
    rng = np.random.default_rng(seed)
    cl = rows[cluster_col].values; arm = rows[arm_col].values.astype(bool); r = rows[ret_col].values
    uniq, inv = np.unique(cl, return_inverse=True)
    K = len(uniq)
    # per-cluster sums and counts for each arm (cluster-level means then arm mean over legs)
    sA = np.bincount(inv, weights=np.where(arm, r, 0.0), minlength=K); nA = np.bincount(inv, weights=arm.astype(float), minlength=K)
    sB = np.bincount(inv, weights=np.where(~arm, r, 0.0), minlength=K); nB = np.bincount(inv, weights=(~arm).astype(float), minlength=K)
    diffs = np.empty(ndraw)
    for i in range(ndraw):
        pick = rng.integers(0, K, K)
        a = sA[pick].sum() / max(nA[pick].sum(), 1); b = sB[pick].sum() / max(nB[pick].sum(), 1)
        diffs[i] = a - b
    return {"diff_mean": float(diffs.mean()), "ci95": [float(np.percentile(diffs,2.5)), float(np.percentile(diffs,97.5))],
            "p_le0": float((diffs <= 0).mean())}

def causal_pct(df, feat_col, q, window_days=365, min_prior=200):
    """For each event (sorted), q-quantile of feat over events in [t-window, t). NaN if < min_prior."""
    d = df.sort_values("event_time")
    t = d["event_time"].values.astype("datetime64[ns]").astype(np.int64)
    v = d[feat_col].values.astype(float)
    w = window_days * 86400 * 10**9
    out = np.full(len(d), np.nan)
    lo = 0
    from bisect import bisect_left
    for i in range(len(d)):
        while t[lo] < t[i] - w:
            lo += 1
        # strictly prior: events with time < t[i]
        hi = bisect_left(t, t[i])
        if hi - lo >= min_prior:
            out[i] = np.nanquantile(v[lo:hi], q)
    return pd.Series(out, index=d.index)

def year_by_year(rows, ret_col, L="L3"):
    d = rows.copy(); d["year"] = d["event_time"].dt.year
    res = {}
    for y, g in d.groupby("year"):
        ep = g.groupby(L)[ret_col].mean()
        res[int(y)] = {"n_rows": int(len(g)), "n_ep": int(len(ep)), "net_bps": float(ep.mean())}
    return res

def full_stats(rows, ret_col_gross="gross_bps"):
    """rows with event_time, symbol, gross_bps. Returns dict of all gate outputs."""
    d = rows.copy()
    d["net14"] = d[ret_col_gross] - COST14; d["net28"] = d[ret_col_gross] - COST28
    dc = decluster(d, "net14")
    r = dc["rows"]
    ep3 = dc["L3"]; ep2 = dc["L2"]; ep1 = dc["L1"]
    st = stats_block(ep3["ret"].values)
    st2 = stats_block(ep2["ret"].values)
    yby = year_by_year(r, "net14")
    best = max(yby, key=lambda y: yby[y]["net_bps"] * yby[y]["n_ep"]) if yby else None
    exb = r[r["event_time"].dt.year != best].groupby("L3")["net14"].mean().mean() if best else None
    ep3_sorted = ep3.sort_values("t0")
    return {
        "n_raw": int(len(r)), "n_L1": int(len(ep1)), "n_L2": int(len(ep2)), "n_L3": int(len(ep3)),
        "gross_bps_raw": float(r[ret_col_gross].mean()), "net14_raw": float(r["net14"].mean()),
        "net14_L3": st["mean"], "net28_L3": st["mean"] - 14.0 if st.get("mean") is not None else None,
        "t_L3": st.get("t"), "boot_ci95_L3": st.get("boot_ci95"), "boot_p_le0_L3": st.get("boot_p_le0"),
        "boot_se_L3": st.get("boot_se"), "sd_L3": st.get("sd"),
        "t_L2_day": st2.get("t"), "net14_L2_day": st2.get("mean"),
        "pf_raw": pf(r["net14"].values), "pf_L3": pf(ep3["ret"].values),
        "hit_raw": float((r["net14"] > 0).mean()),
        "year_by_year": yby, "best_year": best, "ex_best_year_net14_L3": float(exb) if exb is not None else None,
        "worst_episode_bps": float(ep3["ret"].min()), "max_drawdown_bps_cum_L3": max_dd(ep3_sorted["ret"].values),
        "legs_per_L3_mean": float(ep3["n"].mean()), "legs_per_L3_max": int(ep3["n"].max()),
        "top2_L3_share_of_sum": float(np.sort(ep3["ret"].values)[-2:].sum() / ep3["ret"].sum()) if ep3["ret"].sum() > 0 else None,
    }, dc

def rate_and_eta(ep3, net14_L3, sd_L3, min_cal_days=60):
    """ep3: L3 episodes with t0. Returns event rates and N_required/ETA on haircut edge."""
    t = ep3["t0"]; tmax = t.max()
    def rate(days):
        n = int((t > tmax - pd.Timedelta(days=days)).sum()); return n / days
    r_hist = len(ep3) / max((tmax - t.min()).days, 1)
    r2y, r6m = rate(730), rate(182)
    cons = min(r2y, r6m)
    edge = 0.5 * net14_L3
    if edge <= 0 or not sd_L3:
        return {"rate_hist_per_day": r_hist, "rate_2y_per_day": r2y, "rate_6m_per_day": r6m, "rate_conservative_per_day": cons,
                "n_required": None, "eta_p50_days": None, "eta_conservative_days": None, "confirmable_in_horizon": False,
                "note": "edge <= 0: N_required undefined"}
    dcoh = edge / sd_L3
    nreq = int(np.ceil(((1.645 + 0.842) / dcoh) ** 2))
    eta_p50 = max(nreq / r2y, min_cal_days) if r2y > 0 else None
    eta_c = max(nreq / cons, min_cal_days) if cons > 0 else None
    return {"rate_hist_per_day": r_hist, "rate_2y_per_day": r2y, "rate_6m_per_day": r6m, "rate_conservative_per_day": cons,
            "expected_live_edge_bps": edge, "cohen_d": dcoh, "n_required": nreq, "minimum_calendar_days": min_cal_days,
            "eta_p50_days": eta_p50, "eta_p50_years": eta_p50 / 365.25 if eta_p50 else None,
            "eta_conservative_days": eta_c, "eta_conservative_years": eta_c / 365.25 if eta_c else None,
            "confirmable_in_horizon": bool(eta_c is not None and eta_c < 3 * 365.25)}

def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, default=str)
