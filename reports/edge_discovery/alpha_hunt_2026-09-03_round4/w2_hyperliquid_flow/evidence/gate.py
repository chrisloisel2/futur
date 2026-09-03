#!/usr/bin/env python
"""W2 — the round-4 validation gate (briefing section 2), applied identically to every mechanism."""
import numpy as np, pandas as pd

COST, STRESS = 14.0, 28.0
RATE_WINDOW = ("2026-02-01", "2026-07-31")   # last 6 months of the Binance panel


def _boot(day_means, day_w, n=2000, seed=7):
    rng = np.random.default_rng(seed)
    k = len(day_means)
    if k < 5: return (np.nan, np.nan)
    idx = rng.integers(0, k, size=(n, k))
    dm, dw = day_means[idx], day_w[idx]
    mu = (dm*dw).sum(1)/dw.sum(1)
    return tuple(np.percentile(mu, [2.5, 97.5]))


def gate(df, retcol, name, weight_equal_event=True, extra=None):
    """df must carry: usr, coin, day, year, ret column (bps, already signed)."""
    d = df[np.isfinite(df[retcol])].copy()
    if len(d) < 30:
        return {"mechanism": name, "n_raw": int(len(d)), "verdict": "DATA_LIMITED",
                "note": "n<30"}
    r = d[retcol].values.astype(float)
    gross = float(r.mean())

    # --- declustering, 3 levels
    n_raw = len(d)
    n_l1 = d.groupby(["usr", "coin", "day"]).ngroups        # trader-programme
    n_l2 = d.groupby(["coin", "day"]).ngroups               # same symbol / 24h
    n_l3 = d.day.nunique()                                  # calendar day, all symbols

    g = d.groupby("day")[retcol]
    day_means = g.mean().values
    day_w = g.size().values.astype(float) if weight_equal_event else np.ones(len(day_means))
    mu_day = float((day_means*day_w).sum()/day_w.sum())
    sd_day = float(np.sqrt(np.average((day_means-mu_day)**2, weights=day_w)))
    t_l3 = mu_day/(sd_day/np.sqrt(len(day_means))) if sd_day > 0 else np.nan
    lo, hi = _boot(day_means, day_w)

    # coin x day level t (indicative)
    g2 = d.groupby(["coin", "day"])[retcol].mean().values
    t_l2 = float(g2.mean()/(g2.std(ddof=1)/np.sqrt(len(g2)))) if len(g2) > 2 else np.nan

    yb = d.groupby("year")[retcol].agg(["mean", "size"])
    ybd = {y: [round(float(v["mean"]), 2), int(v["size"])] for y, v in yb.iterrows()}
    if len(yb) > 1:
        best = yb["mean"].idxmax()
        dx = d[d.year != best]
        ex_best = float(dx[retcol].mean()); ex_best_year = str(best)
    else:
        ex_best, ex_best_year = np.nan, None

    # power on a 50%-haircut edge, sd measured at the DAY level (independent episodes)
    eff = 0.5*gross
    n_req = float((1.96+0.84)**2 * sd_day**2 / eff**2) if eff != 0 else np.inf
    rw = d[(d.day >= RATE_WINDOW[0]) & (d.day <= RATE_WINDOW[1])]
    nweeks = (pd.Timestamp(RATE_WINDOW[1])-pd.Timestamp(RATE_WINDOW[0])).days/7.0
    rate_days = rw.day.nunique()/nweeks if len(rw) else 0.0     # independent day-episodes / week
    eta_days = n_req/rate_days*7 if rate_days > 0 else np.inf
    out = {
        "mechanism": name, "ret_col": retcol,
        "n_raw": int(n_raw), "n_independent_L1_user_coin_day": int(n_l1),
        "n_independent_L2_coin_day": int(n_l2), "n_independent_L3_day": int(n_l3),
        "gross_bps": round(gross, 2), "net_bps": round(gross-COST, 2),
        "net_bps_stress28": round(gross-STRESS, 2),
        "t_stat_declustered_L3day": round(float(t_l3), 2),
        "t_stat_L2_coin_day": round(t_l2, 2) if np.isfinite(t_l2) else None,
        "bootstrap_ci95": [round(lo, 2), round(hi, 2)],
        "sd_day_bps": round(sd_day, 1),
        "year_by_year": ybd, "ex_best_year": round(ex_best, 2) if np.isfinite(ex_best) else None,
        "ex_best_year_dropped": ex_best_year,
        "n_required": int(min(n_req, 1e9)), "event_rate_indep_per_week": round(rate_days, 2),
        "eta_forward_confirmation_days": round(eta_days, 1) if np.isfinite(eta_days) else None,
        "eta_forward_confirmation_years": round(eta_days/365.25, 2) if np.isfinite(eta_days) else None,
    }
    if extra: out.update(extra)
    return out


def contrast(df, retcol, mask_a, mask_b, name):
    """Arm A minus arm B on the same population, day-blocked t-stat (briefing 1.3)."""
    d = df[np.isfinite(df[retcol])]
    a, b = d[mask_a[d.index]], d[mask_b[d.index]]
    da = a.groupby("day")[retcol].mean(); db = b.groupby("day")[retcol].mean()
    j = pd.concat([da, db], axis=1, join="inner"); j.columns = ["a", "b"]
    diff = (j.a-j.b).values
    t = float(diff.mean()/(diff.std(ddof=1)/np.sqrt(len(diff)))) if len(diff) > 2 else np.nan
    return {"mechanism": name, "arm_a_bps": round(float(a[retcol].mean()), 2),
            "arm_b_bps": round(float(b[retcol].mean()), 2),
            "spread_bps": round(float(a[retcol].mean()-b[retcol].mean()), 2),
            "n_a": int(len(a)), "n_b": int(len(b)), "n_days_paired": int(len(diff)),
            "spread_t_daypaired": round(t, 2)}
