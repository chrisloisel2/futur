"""W8 Track A - SIGNAL ENSEMBLING on the liquidation-cascade EVENT basis.

Population : data/events/liq_cascade_dataset.parquet (49-symbol clean universe, the exact
             file LIQ_CASCADE_REPEAT_V1 / LIQ_CASCADE_FAR_FROM_LOW_V1 / round-3 W5 used).
Trade      : LONG at event_time, hold 4h (fwd_4h). One fixed horizon, no horizon search.
Discipline : every standardisation and every sign is CAUSAL (block-wise expanding ECDF /
             expanding rank-IC, strictly prior data only). See PREREGISTRATION.md.

Read-only on all inputs. Writes only JSON into this worker's evidence/ dir.
"""
import json, os, sys, warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
RNG = np.random.default_rng(20260903)

REPO = "/home/qbee/futur"
EV = os.path.join(REPO, "data/events/liq_cascade_dataset.parquet")
EV_EXT = os.path.join(REPO, "data/events/cascade_dataset.parquet")
OUTDIR = os.path.join(REPO, "reports/edge_discovery/alpha_hunt_2026-09-03_round4/"
                            "w8_signal_ensembling/evidence")
COST_RT = 14.0
COST_STRESS = 28.0
HOLD_H = 4
BURN_EVENTS = 2000
Z_CLIP = 3.0

# ---- preregistered signals + a-priori signs (PREREGISTRATION.md section 4) --------------
APRIORI = {
    "n_events_sym_24h": +1, "dist_low_24h": +1, "dist_low_7d": +1,
    "oi_drop_z": +1, "oi_drop_30m": +1, "oi_drop_1h": +1,
    "btc_vol_24h": +1, "vol_24h": -1, "n_events_mktwide_30m": +1,
    "mins_since_prev_event": -1, "ls_ratio_z": -1, "toptrader_z": -1, "taker_z": -1,
    "funding_z30": -1, "oi_vs_7d": -1, "oi_pctile_30d": -1,
    "oi_ret_2h": -1, "oi_ret_24h": -1, "px_accel": -1, "px_ret_1h": -1,
    "ret_24h": -1, "btc_ret_30m": -1, "taker_delta_1h": -1, "toptrader_delta_1h": -1,
    "ASIA_SESSION": +1,
}


# ---------------------------------------------------------------- causal transforms -----
def causal_z(values, block_id, min_prior=BURN_EVENTS):
    """Block-wise expanding-ECDF z-score. For every event in block b the empirical CDF is
    estimated on ALL events in blocks < b (strictly prior). Returns NaN before burn-in."""
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, np.nan)
    order = np.argsort(block_id, kind="mergesort")
    ub, starts = np.unique(block_id[order], return_index=True)
    ends = np.r_[starts[1:], len(order)]
    prior = []
    n_prior = 0
    for k in range(len(ub)):
        idx = order[starts[k]:ends[k]]
        if n_prior >= min_prior:
            ref = np.sort(np.concatenate(prior))
            ref = ref[~np.isnan(ref)]
            if len(ref) >= min_prior:
                x = v[idx]
                # mid-rank percentile against prior distribution
                lo = np.searchsorted(ref, x, side="left")
                hi = np.searchsorted(ref, x, side="right")
                p = (lo + hi) / (2.0 * len(ref))
                p = np.clip(p, 1.0 / (2 * len(ref)), 1 - 1.0 / (2 * len(ref)))
                z = stats.norm.ppf(p)
                z[np.isnan(x)] = np.nan
                out[idx] = np.clip(z, -Z_CLIP, Z_CLIP)
        cur = v[idx]
        cur = cur[~np.isnan(cur)]
        prior.append(cur)
        n_prior += len(cur)
    return out


def causal_sign(z, y, block_id, min_prior=BURN_EVENTS):
    """Walk-forward sign: for every event in block b, sign of the Spearman IC between the
    signal and fwd_4h computed on ALL events in blocks < b."""
    out = np.zeros(z.shape)
    ic_out = np.full(z.shape, np.nan)
    order = np.argsort(block_id, kind="mergesort")
    ub, starts = np.unique(block_id[order], return_index=True)
    ends = np.r_[starts[1:], len(order)]
    pz, py, n_prior = [], [], 0
    for k in range(len(ub)):
        idx = order[starts[k]:ends[k]]
        if n_prior >= min_prior:
            a = np.concatenate(pz); b = np.concatenate(py)
            m = ~(np.isnan(a) | np.isnan(b))
            if m.sum() >= min_prior:
                ic = stats.spearmanr(a[m], b[m]).correlation
                if np.isfinite(ic):
                    out[idx] = np.sign(ic) if ic != 0 else 0.0
                    ic_out[idx] = ic
        pz.append(z[idx]); py.append(y[idx])
        n_prior += np.isfinite(z[idx]).sum()
    return out, ic_out


def causal_quantile_threshold(score, block_id, q, min_prior=BURN_EVENTS):
    """Threshold = q-quantile of the score distribution over STRICTLY PRIOR blocks."""
    out = np.full(score.shape, np.nan)
    order = np.argsort(block_id, kind="mergesort")
    ub, starts = np.unique(block_id[order], return_index=True)
    ends = np.r_[starts[1:], len(order)]
    prior, n_prior = [], 0
    for k in range(len(ub)):
        idx = order[starts[k]:ends[k]]
        if n_prior >= min_prior:
            ref = np.concatenate(prior); ref = ref[np.isfinite(ref)]
            if len(ref) >= min_prior:
                out[idx] = np.quantile(ref, q)
        cur = score[idx]; cur = cur[np.isfinite(cur)]
        prior.append(cur); n_prior += len(cur)
    return out


# ---------------------------------------------------------------- decluster + stats -----
def _utc(idx):
    """Robust to tz-aware Series and to tz-naive numpy datetime64 (UTC by construction)."""
    d = pd.DatetimeIndex(pd.to_datetime(idx))
    return d.tz_localize("UTC") if d.tz is None else d.tz_convert("UTC")


def decluster_L1(times, symbols, hold_h=HOLD_H):
    """Same-symbol overlapping holding windows collapsed to the first event of the cluster."""
    keep = np.zeros(len(times), dtype=bool)
    last = {}
    order = np.argsort(times, kind="mergesort")
    hold = np.timedelta64(hold_h, "h")
    for i in order:
        s = symbols[i]; t = times[i]
        if s not in last or t >= last[s]:
            keep[i] = True
            last[s] = t + hold
    return keep


def block_bootstrap_ci(x, blocks, n_boot=2000, seed=7):
    """Block bootstrap of the mean, blocks resampled with replacement."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"x": x, "b": blocks})
    g = [v.values for _, v in df.groupby("b")["x"]]
    if len(g) < 3:
        return [float("nan"), float("nan")]
    nb = len(g)
    means = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, nb, nb)
        means[i] = np.concatenate([g[j] for j in pick]).mean()
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


Z_A = 1.959963985
Z_P = 0.8416212336


def eta_from_series(daily_net_bps, days_index, haircut=0.5, obs_per_year=None):
    """ETA_forward_confirmation from a portfolio return series.
    n_required = ((z_a+z_p)/(haircut*SR_obs))^2 ; ETA_years = n_required / obs_per_year."""
    x = np.asarray(daily_net_bps, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 5 or x.std(ddof=1) == 0:
        return None
    sr_obs = x.mean() / x.std(ddof=1)
    if obs_per_year is None:
        span_years = (pd.Timestamp(max(days_index)) - pd.Timestamp(min(days_index))).days / 365.25
        obs_per_year = len(x) / max(span_years, 1e-9)
    sr_ann = sr_obs * np.sqrt(obs_per_year)
    if sr_obs <= 0:
        return {"sr_obs": float(sr_obs), "sr_ann": float(sr_ann), "obs_per_year": float(obs_per_year),
                "n_required": None, "eta_days": None, "eta_years": None,
                "note": "negative mean - not confirmable"}
    n_req = ((Z_A + Z_P) / (haircut * sr_obs)) ** 2
    eta_y = n_req / obs_per_year
    return {"sr_obs": float(sr_obs), "sr_ann": float(sr_ann), "obs_per_year": float(obs_per_year),
            "n_required": float(n_req), "eta_days": float(eta_y * 365.25), "eta_years": float(eta_y)}


def gate(ev_net, ev_time, ev_sym, label, ev_gross=None, pop_mean_bps=None,
         recent_months=6, verbose_year=True):
    """Full BRIEFING section-2 gate on an event-level selected population."""
    n_raw = int(len(ev_net))
    if n_raw == 0:
        return {"label": label, "n_raw": 0, "verdict": "DEAD", "note": "no episodes"}
    t = np.asarray(ev_time)
    keep1 = decluster_L1(t, np.asarray(ev_sym))
    net1 = np.asarray(ev_net)[keep1]
    t1 = t[keep1]
    tu = _utc(t1)
    days = tu.date
    months = tu.to_period("M").astype(str).values
    # daily portfolio series (equal weight within day) = the tradeable unit
    dser = pd.DataFrame({"d": days, "m": months, "x": net1}).groupby("d").agg(
        x=("x", "mean"), m=("m", "first"), k=("x", "size")).reset_index()
    n_L1 = int(keep1.sum())
    n_L2 = int(dser.shape[0])
    n_L3 = int(pd.unique(months).shape[0])

    mean_ep = float(net1.mean())
    sd_ep = float(net1.std(ddof=1)) if n_L1 > 1 else float("nan")
    t_ep = mean_ep / (sd_ep / np.sqrt(n_L1)) if n_L1 > 1 and sd_ep > 0 else float("nan")
    mean_d = float(dser["x"].mean())
    sd_d = float(dser["x"].std(ddof=1)) if n_L2 > 1 else float("nan")
    t_d = mean_d / (sd_d / np.sqrt(n_L2)) if n_L2 > 1 and sd_d > 0 else float("nan")
    mser = dser.groupby("m")["x"].mean()
    t_m = (mser.mean() / (mser.std(ddof=1) / np.sqrt(len(mser)))) if len(mser) > 1 else float("nan")

    ci_day = block_bootstrap_ci(dser["x"].values, dser["m"].values)

    yr = tu.year.values
    ydf = pd.DataFrame({"y": yr, "x": net1}).groupby("y")["x"].agg(["mean", "size"])
    year_by_year = {int(k): {"net_bps": float(v["mean"]), "n": int(v["size"])}
                    for k, v in ydf.iterrows()}
    if len(ydf) > 1:
        best = ydf["mean"].idxmax()
        m2 = yr != best
        ex_best = float(net1[m2].mean()) if m2.sum() > 0 else float("nan")
        ex_best_year = {"dropped_year": int(best), "net_bps": ex_best, "n": int(m2.sum())}
    else:
        ex_best_year = {"dropped_year": None, "net_bps": float("nan"), "n": 0}

    # event rate measured on the LAST 6 MONTHS (conservative, per BRIEFING)
    tmax = tu.max()
    cut = tmax - pd.Timedelta(days=int(30.44 * recent_months))
    tt = tu
    rec = tt >= cut
    span_w = max((tt.max() - cut).days / 7.0, 1e-9)
    rate_ep_week = float(rec.sum() / span_w)
    rec_days = len(set(tt[rec].date))
    rate_day_week = float(rec_days / span_w)

    eta_day = eta_from_series(dser["x"].values, dser["d"].values,
                              obs_per_year=rate_day_week * 52.1775)
    eta_ep = eta_from_series(net1, t1, obs_per_year=rate_ep_week * 52.1775)

    out = {
        "label": label,
        "n_raw": n_raw, "n_independent_L1": n_L1, "n_independent_L2": n_L2,
        "n_independent_L3": n_L3,
        "net_bps": mean_ep, "net_bps_stress28": mean_ep - (COST_STRESS - COST_RT),
        "sd_bps_episode": sd_ep,
        "t_stat_raw": float(t_ep),
        "t_stat_declustered": float(t_d),
        "t_stat_L3_month": float(t_m),
        "daily_portfolio_net_bps": mean_d, "daily_portfolio_sd_bps": sd_d,
        "bootstrap_ci95_daily": ci_day,
        "year_by_year": year_by_year, "ex_best_year": ex_best_year,
        "event_rate_episodes_per_week_last6m": rate_ep_week,
        "event_rate_tradingdays_per_week_last6m": rate_day_week,
        "eta_daily_portfolio": eta_day,
        "eta_episode_level": eta_ep,
        "n_required": (eta_day or {}).get("n_required"),
        "eta_forward_confirmation_days": (eta_day or {}).get("eta_days"),
        "eta_forward_confirmation_years": (eta_day or {}).get("eta_years"),
        "sr_annualised": (eta_day or {}).get("sr_ann"),
    }
    if pop_mean_bps is not None:
        out["excess_vs_population_bps"] = mean_ep - pop_mean_bps
        out["population_mean_bps"] = pop_mean_bps
    if ev_gross is not None:
        out["gross_bps"] = float(np.asarray(ev_gross)[keep1].mean())
    return out


def verdict_of(g, require_eta=3.0):
    if g.get("n_raw", 0) == 0:
        return "DEAD"
    nb = g.get("net_bps", float("nan"))
    td = g.get("t_stat_declustered", float("nan"))
    if not np.isfinite(nb) or nb <= 0 or not np.isfinite(td) or td < 1.0:
        return "DEAD" if (not np.isfinite(nb) or nb <= 0) else "WEAK"
    if td < 2.0:
        return "WEAK"
    eta = g.get("eta_forward_confirmation_years")
    if g.get("net_bps_stress28", -1) <= 0:
        return "COST_FRAGILE"
    ex = g.get("ex_best_year", {}).get("net_bps", float("nan"))
    if np.isfinite(ex) and ex <= 0:
        return "REGIME_DEPENDENT"
    if eta is None or eta > require_eta:
        return "UNCONFIRMABLE_IN_HORIZON"
    return "VALIDATED_FOR_FORWARD"


# ------------------------------------------------------------------------ main ----------
def main():
    df = pd.read_parquet(EV)
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df.sort_values("event_time").reset_index(drop=True)
    df = df[df["fwd_4h"].notna()].reset_index(drop=True)
    df["ASIA_SESSION"] = (df["hour_utc"] < 8).astype(float)
    df["y_bps"] = df["fwd_4h"] * 10000.0

    sigs = [s for s in APRIORI if s in df.columns]
    missing = [s for s in APRIORI if s not in df.columns]
    print(f"events={len(df)} signals={len(sigs)} missing={missing}")

    wk = df["event_time"].dt.to_period("W").astype(str).values
    mo = df["event_time"].dt.to_period("M").astype(str).values
    y = df["y_bps"].values

    Z, SGN_WF, IC_WF = {}, {}, {}
    for s in sigs:
        Z[s] = causal_z(df[s].values.astype(float), wk)
        SGN_WF[s], IC_WF[s] = causal_sign(Z[s], y, mo)
        print(f"  {s:24s} z_ok={np.isfinite(Z[s]).mean():.3f} "
              f"wf_sign_ok={(SGN_WF[s]!=0).mean():.3f} "
              f"final_IC={np.nanmax(np.where(np.isfinite(IC_WF[s]), IC_WF[s], np.nan)):+.4f}")

    zmat = np.column_stack([Z[s] for s in sigs])
    apr = np.array([APRIORI[s] for s in sigs], dtype=float)
    wfs = np.column_stack([SGN_WF[s] for s in sigs])

    np.save(os.path.join(OUTDIR, "a1_zmat.npy"), zmat)
    np.save(os.path.join(OUTDIR, "a1_wfsign.npy"), wfs)
    df[["event_time", "symbol", "kind", "y_bps"]].to_parquet(
        os.path.join(OUTDIR, "a1_events.parquet"))
    with open(os.path.join(OUTDIR, "a1_signals.json"), "w") as f:
        json.dump({"signals": sigs, "apriori": [int(a) for a in apr],
                   "n_events": int(len(df))}, f, indent=1)
    print("track A matrices cached")


if __name__ == "__main__":
    main()
