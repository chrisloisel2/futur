"""W8 Track B - SIGNAL ENSEMBLING on the CROSS-SECTIONAL DAILY basis.

Population : daily perp OHLCV panel aggregated from
             /home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv (venue=binance, 312 sym).
             Built read-only by b0_build_daily_panel.py into scratch (never into data/).
Signals    : the project's own cross-sectional family (CROSS_SECTIONAL_MOMENTUM_*_V1/V2,
             AMIHUD_ILLIQUIDITY_PREMIUM_V1) plus the classic cross-sectional factors that
             rounds 1-3 filed as WEAK. All preregistered in PREREGISTRATION.md section 2.
Costs      : charged on the COMPOSITE's own measured turnover (mandate pitfall), 14bps for a
             full round-trip of the book, stress 28bps.
"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy import stats

PANEL = sys.argv[1] if len(sys.argv) > 1 else \
    "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad/w8/daily_ohlcv.parquet"
OUTDIR = "/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w8_signal_ensembling/evidence"
COST_RT = 14.0
MIN_QV = 1e6
MIN_HIST = 60
Z_A, Z_P = 1.959963985, 0.8416212336

APRIORI = {
    "MOM_7D": +1, "MOM_30D": +1, "REV_1D": -1, "AMIHUD_7D": +1, "AMIHUD_30D": +1,
    "VOL_20D": -1, "MAX_RET_7D": -1, "VOLUME_SHOCK_Z": +1, "TURNOVER_30D": -1,
    "DIST_HIGH_30D": +1, "SKEW_30D": -1, "RANGE_20D": -1, "BETA_BTC_30D": -1,
    "IDIOVOL_30D": -1,
}


def build_features():
    p = pd.read_parquet(PANEL)
    p["day"] = pd.to_datetime(p["day"])
    p = p.sort_values(["symbol", "day"]).reset_index(drop=True)
    p = p[p["n_bars"] >= 250]                    # complete trading day only
    g = p.groupby("symbol", sort=False)
    p["ret"] = g["close"].pct_change()
    p["lqv"] = np.log(p["quote_volume"].clip(lower=1.0))
    g = p.groupby("symbol", sort=False)
    p["qv_med30"] = g["quote_volume"].transform(lambda s: s.rolling(30, min_periods=20).median())
    p["nhist"] = g.cumcount()

    p["MOM_7D"] = g["close"].transform(lambda s: s / s.shift(7) - 1.0)
    p["MOM_30D"] = g["close"].transform(lambda s: s / s.shift(30) - 1.0)
    p["REV_1D"] = p["ret"]
    ill = (p["ret"].abs() / p["quote_volume"].clip(lower=1.0))
    p["_ill"] = ill
    g = p.groupby("symbol", sort=False)
    p["AMIHUD_7D"] = np.log(g["_ill"].transform(lambda s: s.rolling(7, min_periods=5).mean()).clip(lower=1e-30))
    p["AMIHUD_30D"] = np.log(g["_ill"].transform(lambda s: s.rolling(30, min_periods=20).mean()).clip(lower=1e-30))
    p["VOL_20D"] = g["ret"].transform(lambda s: s.rolling(20, min_periods=15).std())
    p["MAX_RET_7D"] = g["ret"].transform(lambda s: s.rolling(7, min_periods=5).max())
    p["VOLUME_SHOCK_Z"] = g["lqv"].transform(
        lambda s: (s - s.rolling(30, min_periods=20).mean()) / s.rolling(30, min_periods=20).std())
    p["TURNOVER_30D"] = np.log(p["qv_med30"].clip(lower=1.0))
    p["_hi30"] = g["high"].transform(lambda s: s.rolling(30, min_periods=20).max())
    p["DIST_HIGH_30D"] = p["close"] / p["_hi30"] - 1.0
    p["SKEW_30D"] = g["ret"].transform(lambda s: s.rolling(30, min_periods=20).skew())
    p["_rng"] = (p["high"] - p["low"]) / p["close"]
    g = p.groupby("symbol", sort=False)
    p["RANGE_20D"] = g["_rng"].transform(lambda s: s.rolling(20, min_periods=15).mean())

    btc = p[p["symbol"] == "BTCUSDT"][["day", "ret"]].rename(columns={"ret": "btc_ret"})
    p = p.merge(btc, on="day", how="left")
    p = p.sort_values(["symbol", "day"]).reset_index(drop=True)
    g = p.groupby("symbol", sort=False)
    cov = g.apply(lambda d: d["ret"].rolling(30, min_periods=20).cov(d["btc_ret"]))
    p["_cov"] = cov.reset_index(level=0, drop=True).sort_index().values
    p["_bvar"] = g["btc_ret"].transform(lambda s: s.rolling(30, min_periods=20).var())
    p["BETA_BTC_30D"] = p["_cov"] / p["_bvar"]
    p["_resid"] = p["ret"] - p["BETA_BTC_30D"] * p["btc_ret"]
    g = p.groupby("symbol", sort=False)
    p["IDIOVOL_30D"] = g["_resid"].transform(lambda s: s.rolling(30, min_periods=20).std())

    p["eligible"] = (p["qv_med30"] >= MIN_QV) & (p["nhist"] >= MIN_HIST)
    return p.drop(columns=[c for c in p.columns if c.startswith("_")])


def xs_z(p, cols):
    """Cross-sectional rank -> normal quantile, WITHIN each day, on the eligible universe.
    Causal by construction: uses no other day."""
    out = {}
    e = p["eligible"].values
    for c in cols:
        v = p[c].values.astype(float)
        z = np.full(len(p), np.nan)
        s = pd.Series(np.where(e, v, np.nan))
        r = s.groupby(p["day"].values).rank(pct=True)
        n = s.groupby(p["day"].values).transform("count")
        rr = r.values
        ok = np.isfinite(rr) & (n.values >= 20)
        rr = np.clip(rr, 1e-6, 1 - 1e-6)
        z[ok] = np.clip(stats.norm.ppf(rr[ok]), -3, 3)
        out[c] = z
    return out


def eta_of(x, obs_per_year, haircut=0.5):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 5 or x.std(ddof=1) == 0:
        return None
    sr = x.mean() / x.std(ddof=1)
    d = {"sr_obs": float(sr), "sr_ann": float(sr * np.sqrt(obs_per_year)),
         "obs_per_year": float(obs_per_year)}
    if sr <= 0:
        d.update({"n_required": None, "eta_years": None, "note": "negative mean"})
        return d
    n = ((Z_A + Z_P) / (haircut * sr)) ** 2
    d.update({"n_required": float(n), "eta_years": float(n / obs_per_year),
              "eta_days": float(n / obs_per_year * 365.25)})
    return d


def block_boot(x, blocks, n_boot=2000, seed=11):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"x": x, "b": blocks})
    g = [v.values for _, v in df.groupby("b")["x"]]
    if len(g) < 3:
        return [float("nan")] * 2
    m = np.empty(n_boot)
    for i in range(n_boot):
        m[i] = np.concatenate([g[j] for j in rng.integers(0, len(g), len(g))]).mean()
    return [float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]


def portfolio(dates, sym_by_date, score_by_date, fwd_by_date, decile=0.1, long_short=True):
    """Non-overlapping decile portfolio. Returns per-rebalance gross bps and turnover."""
    rows = []
    prev_w = {}
    for d in dates:
        s = score_by_date[d]; f = fwd_by_date[d]; sy = sym_by_date[d]
        ok = np.isfinite(s) & np.isfinite(f)
        if ok.sum() < 20:
            continue
        s, f, sy = s[ok], f[ok], sy[ok]
        n = len(s)
        k = max(2, int(round(decile * n)))
        idx = np.argsort(s)
        bot, top = idx[:k], idx[-k:]
        w = {}
        for i in top:
            w[sy[i]] = w.get(sy[i], 0.0) + (0.5 if long_short else 1.0) / k
        if long_short:
            for i in bot:
                w[sy[i]] = w.get(sy[i], 0.0) - 0.5 / k
        gross = (f[top].mean() - f[bot].mean()) if long_short else (f[top].mean() - f.mean())
        keys = set(w) | set(prev_w)
        turn = 0.5 * sum(abs(w.get(x, 0.0) - prev_w.get(x, 0.0)) for x in keys)
        rows.append({"date": d, "gross_bps": gross * 10000.0, "turnover": turn,
                     "n_universe": n, "k": k})
        prev_w = w
    return pd.DataFrame(rows)


def gate_series(pf, label, obs_per_year, cost_rt=COST_RT, stress=28.0):
    if pf is None or len(pf) < 20:
        return None
    net = pf["gross_bps"].values - pf["turnover"].values * cost_rt
    net_s = pf["gross_bps"].values - pf["turnover"].values * stress
    mon = pd.to_datetime(pf["date"]).dt.to_period("M").astype(str).values
    yr = pd.to_datetime(pf["date"]).dt.year.values
    ydf = pd.DataFrame({"y": yr, "x": net}).groupby("y")["x"].agg(["mean", "size"])
    best = ydf["mean"].idxmax() if len(ydf) > 1 else None
    exb = float(net[yr != best].mean()) if best is not None else float("nan")
    sd = net.std(ddof=1)
    g = {
        "label": label, "n_raw": int(len(pf)), "n_independent_L1": int(len(pf)),
        "n_independent_L2": int(pd.unique(pf["date"]).shape[0]),
        "n_independent_L3": int(pd.unique(mon).shape[0]),
        "gross_bps": float(pf["gross_bps"].mean()),
        "mean_turnover": float(pf["turnover"].mean()),
        "cost_bps_from_turnover": float(pf["turnover"].mean() * cost_rt),
        "net_bps": float(net.mean()), "net_bps_stress28": float(net_s.mean()),
        "t_stat_declustered": float(net.mean() / (sd / np.sqrt(len(net)))) if sd > 0 else float("nan"),
        "t_stat_L3_month": float(pd.Series(net).groupby(mon).mean().pipe(
            lambda s: s.mean() / (s.std(ddof=1) / np.sqrt(len(s))))) if len(set(mon)) > 2 else float("nan"),
        "bootstrap_ci95": block_boot(net, mon),
        "year_by_year": {int(k): {"net_bps": float(v["mean"]), "n": int(v["size"])}
                         for k, v in ydf.iterrows()},
        "ex_best_year": {"dropped_year": (int(best) if best is not None else None),
                         "net_bps": exb, "n": int((yr != best).sum()) if best is not None else 0},
        "mean_universe": float(pf["n_universe"].mean()),
    }
    e = eta_of(net, obs_per_year)
    g["eta"] = e
    g["n_required"] = (e or {}).get("n_required")
    g["eta_forward_confirmation_years"] = (e or {}).get("eta_years")
    g["sr_annualised"] = (e or {}).get("sr_ann")
    g["event_rate_episodes_per_week"] = obs_per_year / 52.1775
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
    if g["net_bps_stress28"] <= 0:
        return "COST_FRAGILE"
    if np.isfinite(g["ex_best_year"]["net_bps"]) and g["ex_best_year"]["net_bps"] <= 0:
        return "REGIME_DEPENDENT"
    eta = g["eta_forward_confirmation_years"]
    if eta is None or eta > 3.0:
        return "UNCONFIRMABLE_IN_HORIZON"
    return "VALIDATED_FOR_FORWARD"
