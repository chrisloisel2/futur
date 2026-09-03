#!/usr/bin/env python3
"""W6 round-4 shared library: declustering, episode statistics, ETA arithmetic.

Everything here implements PREREGISTRATION.md sections 3-5 literally.
"""
import numpy as np, pandas as pd, duckdb

Z_ALPHA = 1.959963984540054      # two-sided 5%
Z_POWER = 0.8416212335729143     # 80% power
K_POWER = (Z_ALPHA + Z_POWER) ** 2   # 7.8489
HAIRCUT = 0.5                    # mandatory 50% haircut on the discovered edge
COST_BPS = 14.0
COST_STRESS_BPS = 28.0
FACTORS = ("BTCUSDT", "ETHUSDT")
RECENT_START = pd.Timestamp("2026-02-01", tz="UTC")   # last 6 months of the panel


def load_panel(hourly_glob, min_dv7d=2e7):
    con = duckdb.connect(); con.execute("SET TimeZone='UTC'"); con.execute("PRAGMA threads=6")
    q = f"""
    SELECT regexp_extract(filename,'symbol=([A-Za-z0-9_]+)\\.parquet',1) AS symbol,
           ts, sd30, xs_size, r1h, r4h, doi_1h, doi_4h, bz1, bz7, fr, fpct,
           dv_1h, dv_24h, dv_7d, fi_1h, fi_15m, fwd_1h, fwd_4h, fwd_12h
    FROM read_parquet('{hourly_glob}', filename=true)
    WHERE dv_7d >= {min_dv7d} AND sd30 IS NOT NULL AND sd30 > 0 AND nflow_1h = 12
    """
    df = con.execute(q).df()
    df = df[~df.symbol.isin(FACTORS)].reset_index(drop=True)
    df["ts"] = pd.to_datetime(df.ts, utc=True)
    df["z1"] = df.r1h / df.sd30
    df["z4"] = df.r4h / (2.0 * df.sd30)
    df["vs"] = df.dv_1h / (df.dv_24h / 24.0)
    df["day"] = df.ts.values.astype("datetime64[D]")
    df["hour_idx"] = (df.ts.values.astype("datetime64[h]").astype(np.int64))
    df["year"] = df.ts.dt.year.astype(np.int16)
    df["sym_code"] = pd.factorize(df.symbol)[0].astype(np.int32)
    return df.sort_values(["sym_code", "hour_idx"], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- declustering
def decluster_L1(sym_code, hour_idx, gap_hours=24):
    """Greedy same-symbol / >=gap_hours forward scan. Input must be sorted by
    (sym_code, hour_idx). Returns a boolean mask of KEPT episodes.

    Implementation keeps at most one episode per gap window per symbol, so the
    inner loop runs once per KEPT episode (<= n_days), not once per raw episode.
    """
    keep = np.zeros(len(hour_idx), dtype=bool)
    if len(hour_idx) == 0:
        return keep
    bounds = np.flatnonzero(np.diff(sym_code)) + 1
    starts = np.r_[0, bounds]; ends = np.r_[bounds, len(sym_code)]
    for s, e in zip(starts, ends):
        h = hour_idx[s:e]
        i = 0
        while i < len(h):
            keep[s + i] = True
            i = int(np.searchsorted(h, h[i] + gap_hours, side="left"))
    return keep


def decluster_nonoverlap(hour_idx, horizon_hours):
    """Portfolio-level L1 for cross-sectional mechanisms: keep episodes whose
    forward windows do not overlap (>= horizon_hours apart)."""
    keep = np.zeros(len(hour_idx), dtype=bool)
    i = 0
    while i < len(hour_idx):
        keep[i] = True
        i = int(np.searchsorted(hour_idx, hour_idx[i] + horizon_hours, side="left"))
    return keep


# ---------------------------------------------------------------- statistics
def nw_tstat(x, lag=5):
    """Newey-West t-stat of mean(x) vs 0."""
    x = np.asarray(x, dtype=float); n = len(x)
    if n < 3:
        return np.nan
    m = x.mean(); u = x - m
    g0 = (u * u).sum() / n
    s = g0
    for L in range(1, min(lag, n - 1) + 1):
        g = (u[L:] * u[:-L]).sum() / n
        s += 2.0 * (1.0 - L / (lag + 1.0)) * g
    if s <= 0:
        return np.nan
    return m / np.sqrt(s / n)


def block_bootstrap_ci(day_means, block=5, n_boot=2000, seed=20260903):
    """Moving-block bootstrap CI95 of the mean of a daily series."""
    x = np.asarray(day_means, dtype=float); n = len(x)
    if n < block + 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    means = x[idx].mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def n_required(effect_bps, sd_bps):
    """N independent observations to confirm a HAIRCUT effect at 80% power / 5% two-sided."""
    e = HAIRCUT * effect_bps
    if not np.isfinite(e) or e <= 0 or not np.isfinite(sd_bps) or sd_bps <= 0:
        return np.inf
    return K_POWER * (sd_bps / e) ** 2


def episode_stats(ts, day, year, sym_code, hour_idx, signed_ret_bps, dv_1h, horizon_hours,
                  xs_mode=False, boot_block=5):
    """Full §2 gate block for one mechanism x horizon.

    signed_ret_bps: side * forward residual return, already in bps (GROSS).
    """
    out = {}
    n_raw = len(signed_ret_bps)
    out["n_raw"] = int(n_raw)
    if n_raw < 30:
        out.update(dict(insufficient=True)); return out

    g = float(np.mean(signed_ret_bps))
    out["gross_bps"] = g
    out["net_bps"] = g - COST_BPS
    out["net_bps_stress28"] = g - COST_STRESS_BPS

    # ---- L1
    if xs_mode:
        m1 = decluster_nonoverlap(hour_idx, horizon_hours)
    else:
        m1 = decluster_L1(sym_code, hour_idx, 24)
    x1 = signed_ret_bps[m1]
    out["n_independent_L1"] = int(m1.sum())
    out["gross_bps_L1"] = float(x1.mean())
    out["sd_episode_L1_bps"] = float(x1.std(ddof=1))
    out["t_stat_L1_episode"] = float(x1.mean() / (x1.std(ddof=1) / np.sqrt(len(x1)))) if len(x1) > 2 else np.nan

    # ---- L2 (day) : equal-weight day means over ALL raw episodes
    dser = pd.Series(signed_ret_bps).groupby(day).mean()
    dser = dser.sort_index()
    out["n_independent_L2_days"] = int(len(dser))
    out["gross_bps_daymean"] = float(dser.mean())
    out["sd_daymean_bps"] = float(dser.std(ddof=1))
    out["t_stat_declustered"] = float(nw_tstat(dser.values, lag=5))
    out["t_stat_day_plain"] = float(dser.mean() / (dser.std(ddof=1) / np.sqrt(len(dser)))) if len(dser) > 2 else np.nan
    lo, hi = block_bootstrap_ci(dser.values - COST_BPS, block=boot_block)
    out["bootstrap_ci95_net_bps"] = [lo, hi]
    lo1, hi1 = block_bootstrap_ci(dser.values - COST_BPS, block=1)
    out["bootstrap_ci95_net_bps_block1"] = [lo1, hi1]

    # ---- L3 (ISO week)
    wk = pd.Series(signed_ret_bps).groupby(pd.PeriodIndex(pd.to_datetime(day), freq="W")).mean()
    out["n_independent_L3_weeks"] = int(len(wk))
    out["sd_weekmean_bps"] = float(wk.std(ddof=1))
    out["t_stat_week"] = float(nw_tstat(wk.values, lag=2))

    # ---- year by year (net)
    yb = pd.Series(signed_ret_bps).groupby(year).agg(["mean", "count"])
    out["year_by_year"] = {int(k): {"net_bps": float(v["mean"] - COST_BPS), "n": int(v["count"])}
                           for k, v in yb.iterrows()}
    best_y = max(out["year_by_year"], key=lambda k: out["year_by_year"][k]["net_bps"])
    mk = year != best_y
    out["best_year"] = int(best_y)
    out["ex_best_year_net_bps"] = float(np.mean(signed_ret_bps[mk]) - COST_BPS) if mk.sum() > 30 else np.nan

    # ---- ETA, episode level (optimistic)
    recent = pd.to_datetime(ts) >= RECENT_START
    if xs_mode:
        m1r = m1 & recent.values
    else:
        m1r = m1 & recent.values
    weeks_recent = max((pd.to_datetime(ts).max() - RECENT_START).days / 7.0, 1e-9)
    rate_L1 = float(m1r.sum()) / weeks_recent
    out["event_rate_L1_per_week_recent6m"] = rate_L1
    nreq_ep = n_required(out["net_bps"], out["sd_episode_L1_bps"])
    out["n_required_episode"] = float(nreq_ep)
    out["eta_episode_days"] = float(7.0 * nreq_ep / rate_L1) if rate_L1 > 0 else np.inf

    # ---- ETA, day-clustered (HEADLINE)
    nreq_day = n_required(out["gross_bps_daymean"] - COST_BPS, out["sd_daymean_bps"])
    # a day only accrues if the mechanism fires that day
    span_days = max((pd.to_datetime(ts).max() - pd.to_datetime(ts).min()).days, 1)
    day_cov = min(1.0, len(dser) / span_days)
    recent_days = pd.Series(signed_ret_bps[recent.values]).groupby(day[recent.values]).mean()
    recent_span = max((pd.to_datetime(ts).max() - RECENT_START).days, 1)
    day_cov_recent = min(1.0, len(recent_days) / recent_span) if len(recent_days) else day_cov
    out["day_coverage_recent6m"] = float(day_cov_recent)
    out["n_required_days"] = float(nreq_day)
    out["eta_forward_confirmation_days"] = float(nreq_day / day_cov_recent) if day_cov_recent > 0 else np.inf
    out["eta_forward_confirmation_years"] = out["eta_forward_confirmation_days"] / 365.25

    # ---- break-even
    out["net_bps_min_for_1y_confirm"] = float(2.0 * out["sd_daymean_bps"] * np.sqrt(K_POWER / 365.0))
    out["net_bps_min_for_2y_confirm"] = float(2.0 * out["sd_daymean_bps"] * np.sqrt(K_POWER / 730.0))

    # ---- capacity
    out["capacity_usd_estimate"] = float(np.nanmedian(dv_1h) * 0.10 * horizon_hours)
    out["insufficient"] = False
    return out
