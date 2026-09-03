#!/usr/bin/env python3
"""
W3_LISTINGS_LIFECYCLE — gate.py
Machinerie commune du gate de validation Round 4 (§2 du BRIEFING).
Declustering 3 niveaux, block-bootstrap, n_required avec haircut 50%, ETA.
"""
from __future__ import annotations
import numpy as np, pandas as pd

Z_ALPHA = 1.959963985      # bilateral 5%
Z_POWER = 0.841621234      # power 80%
K_POWER = (Z_ALPHA + Z_POWER) ** 2     # 7.849
HAIRCUT = 0.5

COST_RT = 14.0          # convention briefing (2 jambes)
COST_STRESS = 28.0
COST_LS = 28.0          # livre long/short 4 jambes
COST_LS_STRESS = 56.0
COST_THIN = 60.0        # entree sur perp < 24h (books tres fins)


def _mt(x):
    x = np.asarray(pd.Series(x).dropna(), float)
    n = len(x)
    if n < 2:
        return dict(n=n, mean=float(x.mean()) if n else np.nan, sd=np.nan, t=np.nan)
    m, s = float(x.mean()), float(x.std(ddof=1))
    return dict(n=n, mean=m, sd=s, t=(m / (s / np.sqrt(n)) if s > 0 else np.nan))


def block_bootstrap_ci(ep, n_boot=5000, seed=20260903):
    """IC95 sur la moyenne d'episodes independants (les episodes SONT deja les blocs)."""
    ep = np.asarray(pd.Series(ep).dropna(), float)
    if len(ep) < 3:
        return [np.nan, np.nan]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(ep), size=(n_boot, len(ep)))
    means = ep[idx].mean(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def n_required(ep):
    """N d'episodes independants pour confirmer forward, edge haircute de 50%."""
    st = _mt(ep)
    if not np.isfinite(st["sd"]) or st["sd"] <= 0 or not np.isfinite(st["mean"]):
        return np.nan
    d = abs(st["mean"]) / st["sd"]            # Sharpe par episode
    d_hc = HAIRCUT * d
    if d_hc <= 0:
        return np.nan
    return float(K_POWER / d_hc ** 2)


def episode_rate_per_week(ep_dates, lookback_end=None, months=6):
    """Episodes independants / semaine sur les `months` derniers mois (conservateur)."""
    s = pd.to_datetime(pd.Series(ep_dates), utc=True).dropna().sort_values()
    if s.empty:
        return 0.0
    end = pd.Timestamp(lookback_end, tz="UTC") if lookback_end is not None else s.max()
    start = end - pd.DateOffset(months=months)
    n = int(((s > start) & (s <= end)).sum())
    weeks = (end - start).total_seconds() / (7 * 86400)
    return n / weeks if weeks > 0 else 0.0


def eta_days_years(nreq, rate_wk):
    if not np.isfinite(nreq) or rate_wk is None or rate_wk <= 0:
        return dict(eta_weeks=np.inf, eta_days=np.inf, eta_years=np.inf)
    w = nreq / rate_wk
    return dict(eta_weeks=float(w), eta_days=float(w * 7), eta_years=float(w / 52.1775))


def decluster(df, ret_col, keys):
    """Moyenne du rendement par cle de declustering -> serie d'episodes + leur date."""
    g = df.groupby(keys, observed=True).agg(ret=(ret_col, "mean"), date=("_dt", "min")).reset_index()
    return g["ret"].to_numpy(float), g["date"]


def year_table(df, ret_col, l3_keys):
    """Table par annee, calculee sur les episodes L3 (pas sur N brut)."""
    rows = []
    for y, sub in df.groupby(df["_dt"].dt.year):
        ep, _ = decluster(sub, ret_col, l3_keys)
        st = _mt(ep)
        rows.append(dict(year=int(y), n_ep=st["n"], mean_bps=round(st["mean"], 1) if np.isfinite(st["mean"]) else None,
                         t=round(st["t"], 2) if np.isfinite(st["t"]) else None))
    return rows


def run_gate(df, ret_col, l1_keys, l2_keys, l3_keys, *, cost_rt=COST_RT, cost_stress=COST_STRESS,
             extra_costs=None, l3_alt_keys=None, label="", family="", hypothesis="",
             lookback_end="2026-09-03", n_boot=5000):
    """df doit contenir `_dt` (datetime UTC) et ret_col en BPS BRUTS (gross)."""
    df = df.dropna(subset=[ret_col]).copy()
    out = dict(id=label, family=family, hypothesis=hypothesis)
    out["n_raw"] = int(len(df))
    if len(df) < 3:
        out.update(verdict="DATA_LIMITED", note="n_raw<3")
        return out

    ep1, d1 = decluster(df, ret_col, l1_keys)
    ep2, d2 = decluster(df, ret_col, l2_keys)
    ep3, d3 = decluster(df, ret_col, l3_keys)
    out["n_independent_L1"], out["n_independent_L2"], out["n_independent_L3"] = len(ep1), len(ep2), len(ep3)

    s3 = _mt(ep3)
    gross = s3["mean"]
    out["gross_bps"] = round(gross, 1)
    out["net_bps"] = round(gross - cost_rt, 1)
    out["net_bps_stress28"] = round(gross - cost_stress, 1)
    if extra_costs:
        for k, c in extra_costs.items():
            out[k] = round(gross - c, 1)
    out["t_stat_L2"] = round(_mt(ep2)["t"], 2) if np.isfinite(_mt(ep2)["t"]) else None
    out["t_stat_declustered"] = round(s3["t"], 2) if np.isfinite(s3["t"]) else None
    ci = block_bootstrap_ci(ep3, n_boot=n_boot)
    out["bootstrap_ci95"] = [round(ci[0], 1), round(ci[1], 1)] if np.isfinite(ci[0]) else None

    out["year_by_year"] = year_table(df, ret_col, l3_keys)
    yrs = [r for r in out["year_by_year"] if r["mean_bps"] is not None and r["n_ep"] >= 3]
    if len(yrs) >= 2:
        best = max(yrs, key=lambda r: r["mean_bps"] if gross >= 0 else -r["mean_bps"])
        sub = df[df["_dt"].dt.year != best["year"]]
        epx, _ = decluster(sub, ret_col, l3_keys)
        out["ex_best_year"] = dict(dropped=best["year"], gross_bps=round(_mt(epx)["mean"], 1),
                                   t=round(_mt(epx)["t"], 2) if np.isfinite(_mt(epx)["t"]) else None,
                                   n_ep=int(len(epx)))
        sgn = np.sign(gross)
        out["years_same_sign"] = f"{sum(1 for r in yrs if np.sign(r['mean_bps'])==sgn)}/{len(yrs)}"
    else:
        out["ex_best_year"] = None
        out["years_same_sign"] = None

    nreq = n_required(ep3)
    out["n_required"] = round(nreq, 1) if np.isfinite(nreq) else None
    rate = episode_rate_per_week(d3, lookback_end=lookback_end)
    out["event_rate_per_week_6m"] = round(rate, 3)
    e = eta_days_years(nreq, rate)
    out["eta_forward_confirmation"] = dict(
        days=(round(e["eta_days"], 0) if np.isfinite(e["eta_days"]) else None),
        years=(round(e["eta_years"], 2) if np.isfinite(e["eta_years"]) else None))
    if l3_alt_keys is not None:
        epa, da = decluster(df, ret_col, l3_alt_keys)
        na, ra = n_required(epa), episode_rate_per_week(da, lookback_end=lookback_end)
        ea = eta_days_years(na, ra)
        out["eta_L3_alt"] = dict(unit=str(l3_alt_keys), n_ep=int(len(epa)),
                                 n_required=(round(na, 1) if np.isfinite(na) else None),
                                 rate_wk=round(ra, 3),
                                 years=(round(ea["eta_years"], 2) if np.isfinite(ea["eta_years"]) else None))
    return out


def add_time_keys(df, dt_col="_dt"):
    d = pd.to_datetime(df[dt_col], utc=True)
    df["_date"] = d.dt.date
    iso = d.dt.isocalendar()
    df["_isoweek"] = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)
    df["_month"] = d.dt.strftime("%Y-%m")
    df["_sym24"] = df.get("symbol", "NA").astype(str) + "|" + d.dt.floor("24h").astype(str)
    return df
