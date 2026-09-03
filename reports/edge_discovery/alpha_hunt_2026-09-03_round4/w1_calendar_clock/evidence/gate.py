"""W1_CALENDAR_CLOCK — the §2 validation gate, applied identically to every mechanism.

Everything here is fixed by PREREGISTRATION.md. Nothing is tuned per-mechanism.
"""
import numpy as np, pandas as pd

Z_POWER = 1.959963985 + 0.841621234      # 2.8016 : alpha=5% two-sided, power=80%
HAIRCUT = 0.5                            # mandatory: discovery overstates
SIX_MONTHS_START = pd.Timestamp("2026-03-01", tz="UTC")
SIX_MONTHS_END   = pd.Timestamp("2026-09-01", tz="UTC")
RNG = np.random.default_rng(20260903)


def _blocks(day_vals, block=7):
    """Moving-block bootstrap indices; block = 1 calendar week of day observations."""
    n = len(day_vals)
    if n < block:
        return None
    nblocks = int(np.ceil(n / block))
    starts = np.arange(n - block + 1)
    return starts, nblocks, block, n


def block_bootstrap_ci(day_vals, n_boot=5000, block=7, seed=None):
    rng = RNG if seed is None else np.random.default_rng(seed)
    b = _blocks(day_vals, block)
    if b is None:
        return (float("nan"), float("nan")), float("nan")
    starts, nblocks, block, n = b
    idx = rng.integers(0, len(starts), size=(n_boot, nblocks))
    off = np.arange(block)
    means = np.empty(n_boot)
    for k in range(n_boot):
        sel = (starts[idx[k]][:, None] + off).ravel()[:n]
        means[k] = day_vals[sel].mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return (float(lo), float(hi)), float(means.std(ddof=1))


def run_gate(obs, name, hypothesis="", n_ind_L1=None, cost_legs=2,
             notes="", n_boot=5000, extra=None):
    """obs: DataFrame with columns ['ts','ret_bps'] (+ optional 'n_symbols').
    ts = event time (tz-aware UTC). ret_bps = GROSS spread return of that episode, in bps.
    Returns a dict with every field of briefing §2."""
    obs = obs.dropna(subset=["ret_bps"]).copy()
    if len(obs) == 0:
        return {"mechanism": name, "verdict": "DATA_LIMITED", "n_raw": 0, "note": "no observations"}
    obs["ts"] = pd.to_datetime(obs["ts"], utc=True)
    obs["day"] = obs["ts"].dt.floor("D")
    obs["week"] = obs["ts"].dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("W").dt.start_time
    obs["year"] = obs["ts"].dt.year

    n_raw = int(len(obs))
    # ---- L2 : calendar day, all symbols (PRIMARY unit) ----
    day = obs.groupby("day")["ret_bps"].mean().sort_index()
    day_vals = day.to_numpy()
    n_L2 = int(len(day))
    n_L3 = int(obs["week"].nunique())

    mean_day = float(day_vals.mean())
    sd_day = float(day_vals.std(ddof=1)) if n_L2 > 1 else float("nan")
    se_block_day = sd_day / np.sqrt(n_L2) if n_L2 > 1 else float("nan")
    t_declustered = mean_day / se_block_day if se_block_day and se_block_day == se_block_day and se_block_day > 0 else float("nan")

    # naive (WRONG, shown only to expose the size of the clustering trap)
    sd_raw = float(obs["ret_bps"].std(ddof=1)) if n_raw > 1 else float("nan")
    t_naive = float(obs["ret_bps"].mean() / (sd_raw / np.sqrt(n_raw))) if sd_raw and sd_raw > 0 else float("nan")

    ci, se_boot = block_bootstrap_ci(day_vals, n_boot=n_boot)
    # variance-inflation-adjusted effective N (diagnostic only, never upgrades a verdict)
    n_eff = float(n_raw * (sd_raw / np.sqrt(n_raw) / se_boot) ** 2) if (se_boot and se_boot > 0 and sd_raw) else float("nan")

    IR_day = mean_day / sd_day if sd_day and sd_day > 0 else float("nan")
    n_required = float(Z_POWER ** 2 / (HAIRCUT * IR_day) ** 2) if IR_day and abs(IR_day) > 1e-12 else float("inf")

    # ---- event rate on the LAST 6 MONTHS only (conservative) ----
    recent = obs[(obs["ts"] >= SIX_MONTHS_START) & (obs["ts"] < SIX_MONTHS_END)]
    n_recent_days = recent["day"].nunique()
    weeks_span = (SIX_MONTHS_END - SIX_MONTHS_START).days / 7.0
    event_rate = n_recent_days / weeks_span if weeks_span > 0 else float("nan")
    if event_rate and event_rate > 0:
        eta_weeks = n_required / event_rate
        eta_days = eta_weeks * 7
        eta_years = eta_days / 365.25
    else:
        eta_weeks = eta_days = eta_years = float("inf")

    # ---- year by year (on day-level means) ----
    dyear = obs.groupby(["year", "day"])["ret_bps"].mean().reset_index()
    yby = dyear.groupby("year")["ret_bps"].agg(["mean", "count"]).rename(
        columns={"mean": "gross_bps", "count": "n_days"})
    year_by_year = {int(y): {"gross_bps": round(float(r.gross_bps), 2), "n_days": int(r.n_days)}
                    for y, r in yby.iterrows()}
    if len(yby) > 1:
        best = yby["gross_bps"].idxmax()
        rest = dyear[dyear["year"] != best]
        ex_best = float(rest.groupby("day")["ret_bps"].mean().mean()) if len(rest) else float("nan")
        rest_day = rest.groupby("day")["ret_bps"].mean().to_numpy()
        ex_best_t = float(rest_day.mean() / (rest_day.std(ddof=1) / np.sqrt(len(rest_day)))) if len(rest_day) > 1 else float("nan")
    else:
        best, ex_best, ex_best_t = None, float("nan"), float("nan")

    g = mean_day
    res = {
        "mechanism": name,
        "hypothesis": hypothesis,
        "n_raw": n_raw,
        "n_independent_L1": int(n_ind_L1) if n_ind_L1 is not None else None,
        "n_independent_L2": n_L2,
        "n_independent_L3": n_L3,
        "n_independent_L2_eff_diag": round(n_eff, 1) if n_eff == n_eff else None,
        "gross_bps": round(g, 3),
        "net_bps": round(g - 14, 3),
        "net_bps_stress28": round(g - 28, 3),
        "net_bps_2leg": round(g - 28, 3) if cost_legs == 2 else round(g - 14, 3),
        "net_bps_2leg_stress56": round(g - 56, 3) if cost_legs == 2 else round(g - 28, 3),
        "cost_legs": cost_legs,
        "sd_day_bps": round(sd_day, 2) if sd_day == sd_day else None,
        "IR_day": round(IR_day, 5) if IR_day == IR_day else None,
        "sharpe_ann_equiv": round(IR_day * np.sqrt(365), 3) if IR_day == IR_day else None,
        "t_stat_declustered": round(t_declustered, 3) if t_declustered == t_declustered else None,
        "t_stat_naive_WRONG": round(t_naive, 3) if t_naive == t_naive else None,
        "clustering_inflation_factor": round(abs(t_naive / t_declustered), 2)
            if (t_naive == t_naive and t_declustered == t_declustered and abs(t_declustered) > 1e-9) else None,
        "bootstrap_ci95": [round(ci[0], 3), round(ci[1], 3)] if ci[0] == ci[0] else None,
        "year_by_year": year_by_year,
        "best_year_dropped": int(best) if best is not None else None,
        "ex_best_year_gross_bps": round(ex_best, 3) if ex_best == ex_best else None,
        "ex_best_year_t": round(ex_best_t, 3) if ex_best_t == ex_best_t else None,
        "n_required_independent_days": round(n_required, 1) if np.isfinite(n_required) else None,
        "event_rate_per_week_last6m": round(event_rate, 3),
        "eta_forward_confirmation_days": round(eta_days, 1) if np.isfinite(eta_days) else None,
        "eta_forward_confirmation_years": round(eta_years, 2) if np.isfinite(eta_years) else None,
        "notes": notes,
    }
    if extra:
        res.update(extra)
    res["day_series"] = day  # not serialised; used for family-level max-t
    return res


def auto_verdict(r, family_maxt_crit=None):
    """Mechanical verdict from PREREGISTRATION §8. No discretion."""
    if r.get("n_raw", 0) == 0:
        return "DATA_LIMITED", "no observations"
    n_L2 = r.get("n_independent_L2") or 0
    if n_L2 < 30:
        return "DATA_LIMITED", f"only {n_L2} independent L2 episodes"
    t = r.get("t_stat_declustered")
    net = r.get("net_bps_2leg")
    net_stress = r.get("net_bps_2leg_stress56")
    eta = r.get("eta_forward_confirmation_years")
    reasons = []
    crit = family_maxt_crit if family_maxt_crit is not None else 1.96
    if t is None or abs(t) < crit:
        return "DEAD" if (t is None or abs(t) < 1.0) else "WEAK", \
               f"|t_declustered|={abs(t) if t else float('nan'):.2f} < family max-t crit {crit:.2f}"
    # sign convention: a mechanism is tradeable in whichever direction it fires,
    # but the direction must have been pre-committed; handled per-mechanism upstream.
    edge = abs(r["gross_bps"])
    if edge <= 28:
        return "COST_FRAGILE", f"gross {r['gross_bps']:.1f}bps <= 2-leg base cost 28bps"
    if edge <= 56:
        reasons.append("dies at 2-leg stress (56bps)")
        return "COST_FRAGILE", f"gross {r['gross_bps']:.1f}bps survives base but not stress"
    yby = r.get("year_by_year", {})
    exb = r.get("ex_best_year_gross_bps")
    if exb is not None and abs(exb) < 28:
        return "REGIME_DEPENDENT", f"ex-best-year gross {exb:.1f}bps below 2-leg base cost"
    if eta is None or eta >= 3.0:
        return "UNCONFIRMABLE_IN_HORIZON", f"ETA {eta} years >= 3"
    return "VALIDATED_FOR_FORWARD", "passes full gate"


def family_maxt(results, n_boot=2000, block=7, seed=7):
    """Family-wise max-|t| critical value (PREREG §7) by joint week-block bootstrap
    under the null (each mechanism's day series centred)."""
    series = [r["day_series"] for r in results if isinstance(r.get("day_series"), pd.Series) and len(r["day_series"]) > 10]
    if not series:
        return 1.96
    rng = np.random.default_rng(seed)
    maxts = np.zeros(n_boot)
    prepped = []
    for s in series:
        v = s.to_numpy()
        prepped.append(v - v.mean())
    for k in range(n_boot):
        best = 0.0
        for v in prepped:
            n = len(v)
            nb = int(np.ceil(n / block))
            st = rng.integers(0, n - block + 1, size=nb)
            sel = (st[:, None] + np.arange(block)).ravel()[:n]
            x = v[sel]
            sd = x.std(ddof=1)
            if sd > 0:
                best = max(best, abs(x.mean() / (sd / np.sqrt(n))))
        maxts[k] = best
    return float(np.percentile(maxts, 95))
