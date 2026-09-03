#!/usr/bin/env python
"""W7 round4 — the §2 validation gate, applied identically to every mechanism.

A mechanism is expressed as a daily POSITION matrix (index=UTC day, columns=asset,
values in [-1,1]) plus the matching FORWARD return matrix (return from day d close to
day d+1 close). Everything in the §2 table is derived from that single representation so
no mechanism can quietly use a friendlier statistic than another.

Cost convention (briefing §1.4): 14bps per round trip => 7bps per unit of |position change|.
Stress: 28bps per round trip => 14bps per unit.
"""
import numpy as np, pandas as pd
from scipy import stats

COST_PER_UNIT = 7.0
COST_PER_UNIT_STRESS = 14.0
Z = stats.norm.ppf(0.975) + stats.norm.ppf(0.80)      # 2.8016
ZSQ = Z**2                                            # 7.849


def _episodes(pos: pd.DataFrame):
    """L3 unit: a contiguous run of the same non-zero position state on one asset."""
    eps = []
    for a in pos.columns:
        s = np.sign(pos[a].fillna(0.0).values)
        d = pos.index
        i = 0
        while i < len(s):
            if s[i] == 0:
                i += 1; continue
            j = i
            while j + 1 < len(s) and s[j+1] == s[i]:
                j += 1
            eps.append((a, i, j, d[i], d[j]))
            i = j + 1
    return eps


def run_gate(pos: pd.DataFrame, fwd: pd.DataFrame, name: str, notes: str = "",
             vehicle: str = "PERP", extra: dict = None) -> dict:
    """pos/fwd aligned; fwd[a][d] = simple return from close(d) to close(d+1)."""
    pos = pos.reindex(columns=fwd.columns).fillna(0.0)
    fwd = fwd.reindex(index=pos.index)
    valid = fwd.notna()
    pos = pos.where(valid, 0.0)

    gross = (pos * fwd * 1e4)                                   # bps, per asset-day
    dpos = pos.diff().abs().fillna(pos.abs())
    cost = dpos * COST_PER_UNIT
    cost_s = dpos * COST_PER_UNIT_STRESS
    net = (gross - cost).fillna(0.0)
    net_s = (gross - cost_s).fillna(0.0)

    port = net.sum(axis=1)                                       # daily portfolio bps
    port_s = net_s.sum(axis=1)
    gross_port = gross.sum(axis=1).fillna(0.0)
    active = (pos.abs().sum(axis=1) > 0)
    gross_expo = pos.abs().sum(axis=1).replace(0, np.nan)

    # ---- declustering levels ----
    n_raw = int((pos.abs() > 0).sum().sum())                     # asset-days with a position
    n_l1 = n_raw                                                 # same-asset/24h == asset-day here
    n_l2 = int(active.sum())                                     # calendar days
    eps = _episodes(pos)
    n_l3 = len(eps)

    # ---- episode-level statistics (the ONLY basis for t-stats, per §1.2) ----
    ep_ret, ep_ret_s, ep_len, ep_year, ep_start = [], [], [], [], []
    for (a, i, j, d0, d1) in eps:
        ep_ret.append(float(net[a].iloc[i:j+1].sum()))
        ep_ret_s.append(float(net_s[a].iloc[i:j+1].sum()))
        ep_len.append(j - i + 1); ep_year.append(d0.year); ep_start.append(d0)
    ep_ret = np.array(ep_ret); ep_ret_s = np.array(ep_ret_s)
    ep_year = np.array(ep_year); ep_len = np.array(ep_len)

    def _t(x):
        x = x[np.isfinite(x)]
        if len(x) < 3 or x.std(ddof=1) == 0: return np.nan
        return float(x.mean()/(x.std(ddof=1)/np.sqrt(len(x))))

    net_bps = float(ep_ret.mean()) if len(ep_ret) else np.nan
    net_bps_s28 = float(ep_ret_s.mean()) if len(ep_ret_s) else np.nan
    gross_bps = float(np.mean([gross[a].iloc[i:j+1].sum() for (a,i,j,_,_) in eps])) if eps else np.nan
    t_dec = _t(ep_ret)

    # ---- block bootstrap, blocks = L3 episodes ----
    ci = (np.nan, np.nan)
    if len(ep_ret) >= 10:
        rng = np.random.default_rng(20260903)
        bs = np.array([ep_ret[rng.integers(0, len(ep_ret), len(ep_ret))].mean() for _ in range(5000)])
        ci = (float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)))

    # ---- annualised Sharpe of the daily portfolio (net) ----
    ndays = int(active.sum())
    span_days = max((pos.index[-1] - pos.index[0]).days, 1)
    dp = port.reindex(pos.index).fillna(0.0)
    sr = float(dp.mean()/dp.std(ddof=1)*np.sqrt(365.25)) if dp.std(ddof=1) > 0 else np.nan
    dps = port_s.reindex(pos.index).fillna(0.0)
    sr_s = float(dps.mean()/dps.std(ddof=1)*np.sqrt(365.25)) if dps.std(ddof=1) > 0 else np.nan

    # ---- year by year ----
    ybs = {}
    for y in sorted(set(ep_year)):
        m = ep_year == y
        ybs[int(y)] = {"n_ep": int(m.sum()), "net_bps": round(float(ep_ret[m].mean()), 2),
                       "total_bps": round(float(ep_ret[m].sum()), 1),
                       "t": round(_t(ep_ret[m]), 2) if m.sum() >= 3 else None}
    ex_best = None
    if len(ybs) >= 2:
        best = max(ybs, key=lambda y: ybs[y]["total_bps"])
        m = ep_year != best
        if m.sum() >= 3:
            ex_best = {"dropped_year": int(best), "n_ep": int(m.sum()),
                       "net_bps": round(float(ep_ret[m].mean()), 2), "t": round(_t(ep_ret[m]), 2)}

    # ---- n_required / event_rate / ETA (§2, 50% haircut mandatory) ----
    n_req = eta_d = eta_y = rate_wk = np.nan
    if len(ep_ret) >= 3 and ep_ret.std(ddof=1) > 0 and abs(net_bps) > 1e-9:
        n_req = float(ZSQ * (ep_ret.std(ddof=1)/(0.5*abs(net_bps)))**2)
        last6 = pos.index.max() - pd.Timedelta(days=182)
        rate_wk = float(sum(1 for d in ep_start if d >= last6)/26.0)
        if rate_wk > 0:
            eta_d = n_req/rate_wk*7.0; eta_y = eta_d/365.25
        else:                                   # mechanism has stopped firing recently
            eta_d = np.inf; eta_y = np.inf

    out = {
        "mechanism": name, "vehicle": vehicle, "notes": notes,
        "n_raw": n_raw, "n_independent_L1": n_l1, "n_independent_L2": n_l2,
        "n_independent_L3": n_l3, "L3_unit": "contiguous same-sign position episode",
        "mean_episode_days": round(float(ep_len.mean()), 2) if len(ep_len) else None,
        "gross_bps_per_episode": round(gross_bps, 2) if np.isfinite(gross_bps) else None,
        "net_bps": round(net_bps, 2) if np.isfinite(net_bps) else None,
        "net_bps_stress28": round(net_bps_s28, 2) if np.isfinite(net_bps_s28) else None,
        "t_stat_declustered": round(t_dec, 2) if np.isfinite(t_dec) else None,
        "bootstrap_ci95": [round(ci[0], 2), round(ci[1], 2)] if np.isfinite(ci[0]) else None,
        "sharpe_annual_net": round(sr, 3) if np.isfinite(sr) else None,
        "sharpe_annual_stress28": round(sr_s, 3) if np.isfinite(sr_s) else None,
        "year_by_year": ybs, "ex_best_year": ex_best,
        "n_required": round(n_req, 1) if np.isfinite(n_req) else None,
        "event_rate_per_week_last6m": round(rate_wk, 3) if np.isfinite(rate_wk) else None,
        "eta_forward_confirmation_days": (round(eta_d, 1) if np.isfinite(eta_d) else "inf"),
        "eta_forward_confirmation_years": (round(eta_y, 2) if np.isfinite(eta_y) else "inf"),
        "sample_start": str(pos.index.min().date()), "sample_end": str(pos.index.max().date()),
        "trading_days_active": ndays, "calendar_span_days": span_days,
    }
    if extra: out.update(extra)
    return out


def two_arm(vals_a, vals_b, name_a="A", name_b="B"):
    """§1.3 — compare arms against each other, never against zero."""
    a = np.asarray(vals_a, float); b = np.asarray(vals_b, float)
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3: return None
    t, p = stats.ttest_ind(a, b, equal_var=False)
    sp = np.sqrt(((len(a)-1)*a.var(ddof=1) + (len(b)-1)*b.var(ddof=1))/(len(a)+len(b)-2))
    return {"n_"+name_a: len(a), "n_"+name_b: len(b),
            "mean_"+name_a: round(float(a.mean()), 2), "mean_"+name_b: round(float(b.mean()), 2),
            "diff": round(float(a.mean()-b.mean()), 2), "t_diff": round(float(t), 2),
            "p_diff": float(f"{p:.3g}"), "cohens_d": round(float((a.mean()-b.mean())/sp), 3) if sp > 0 else None}


def causal_z(s: pd.Series, win: int = 252, minp: int = 60) -> pd.Series:
    """Trailing z-score, strictly causal (no full-sample standardisation)."""
    m = s.rolling(win, min_periods=minp).mean()
    sd = s.rolling(win, min_periods=minp).std(ddof=1)
    return (s - m)/sd.replace(0, np.nan)


def causal_pct(s: pd.Series, win: int = 252, minp: int = 60) -> pd.Series:
    """Trailing percentile rank of the current value within its own past."""
    return s.rolling(win, min_periods=minp).apply(
        lambda x: (x[:-1] < x[-1]).mean() if len(x) > 1 else np.nan, raw=True)
