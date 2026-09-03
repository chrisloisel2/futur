"""W4_NEWS_SENTIMENT — shared library: causal F&G features + the round-4 validation gate.

Re-executable. Read-only on all data/. Writes nothing outside the worker directory.
"""
import numpy as np, pandas as pd
from pathlib import Path

ROOT = Path("/home/qbee/futur")
COST = 14.0
COST_STRESS = 28.0
RNG = np.random.default_rng(20260903)


# ---------------------------------------------------------------- F&G features
def load_fg():
    """Fear & Greed, daily. Returns a frame indexed by UTC day with CAUSAL features only.

    fg_pct365 at day t uses ONLY days strictly < t (365-day trailing window), so it is
    usable as a condition for any decision taken at or after 00:00 UTC on day t.
    Note: alternative.me publishes the day-t value at ~00:00 UTC for that day, and the
    index is built from the PRIOR day's market data, so using fg[t] to condition a trade
    entered during day t is PIT-safe. We nonetheless lag by one day for the trailing rank.
    """
    fg = pd.read_parquet(ROOT / "data/news_backfill/fear_greed.parquet")
    fg["day"] = pd.to_datetime(fg["date"], utc=True).dt.floor("D")
    fg = fg[["day", "fear_greed", "value_classification"]].drop_duplicates("day").sort_values("day")
    fg = fg.set_index("day").asfreq("D")          # 4 missing days -> NaN, never filled forward blindly
    fg["fear_greed"] = fg["fear_greed"].astype(float)

    v = fg["fear_greed"]
    # causal trailing-365d percentile rank: rank of v[t] among v[t-365 .. t-1]
    prev = v.shift(1)
    fg["fg_pct365"] = prev.rolling(365, min_periods=180).apply(
        lambda w: np.nan, raw=True)   # placeholder overwritten below (kept for shape)
    arr = v.to_numpy(dtype=float)
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(n):
        lo = max(0, i - 365)
        win = arr[lo:i]                            # strictly before i
        win = win[~np.isnan(win)]
        if len(win) >= 180 and not np.isnan(arr[i]):
            out[i] = (win < arr[i]).mean() + 0.5 * (win == arr[i]).mean()
    fg["fg_pct365"] = out
    fg["fg_chg_7d"] = v - v.shift(7)
    fg["fg_lvl"] = v
    return fg.reset_index().rename(columns={"index": "day"})


def attach_fg(df, ts_col="event_time"):
    """Attach causal F&G features to an event frame keyed on a UTC timestamp."""
    fg = load_fg()
    d = df.copy()
    d["_ts"] = pd.to_datetime(d[ts_col], utc=True)
    d["day"] = d["_ts"].dt.floor("D")
    return d.merge(fg, on="day", how="left")


# ------------------------------------------------------------------ declustering
def decluster_L1(d, ts="_ts", sym="symbol", hours=24):
    """L1: first event per symbol per rolling `hours` window."""
    d = d.sort_values([sym, ts])
    keep = []
    last = {}
    for i, (s, t) in enumerate(zip(d[sym].to_numpy(), d[ts].to_numpy())):
        lt = last.get(s)
        if lt is None or (t - lt) / np.timedelta64(1, "h") >= hours:
            keep.append(True); last[s] = t
        else:
            keep.append(False)
    return d[np.array(keep)]


def n_L2(d, ts="_ts"):
    """L2: distinct calendar days."""
    return int(pd.to_datetime(d[ts], utc=True).dt.floor("D").nunique())


def episode_id_fg(d, regime_col, ts="_ts"):
    """L3 for F&G mechanisms: maximal run of consecutive days in the same regime bucket.

    F&G is slow and strongly autocorrelated; consecutive days inside one regime episode
    are ONE bet, not many. Episodes are defined on the daily F&G calendar (not on events),
    then joined to events, so two events on different days of the same regime run share
    an episode id.
    """
    fg = load_fg().dropna(subset=[regime_col]) if regime_col in ("fg_pct365", "fg_lvl") else None
    dd = d.copy()
    dd["day"] = pd.to_datetime(dd[ts], utc=True).dt.floor("D")
    day_reg = dd[["day", "_bucket"]].drop_duplicates("day").sort_values("day")
    chg = (day_reg["_bucket"] != day_reg["_bucket"].shift(1)) | \
          (day_reg["day"].diff() > pd.Timedelta("1D"))
    day_reg["_ep"] = chg.cumsum()
    return dd.merge(day_reg[["day", "_ep"]], on="day", how="left")["_ep"].to_numpy()


# ------------------------------------------------------------------------ gate
def _tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 3 or np.std(x, ddof=1) == 0:
        return np.nan
    return float(np.mean(x) / (np.std(x, ddof=1) / np.sqrt(len(x))))


def _block_boot_ci(ep_means, n_boot=2000):
    """Block bootstrap over INDEPENDENT episodes (each episode = one block)."""
    v = np.asarray(ep_means, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 5:
        return [np.nan, np.nan]
    idx = RNG.integers(0, len(v), size=(n_boot, len(v)))
    means = v[idx].mean(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def n_required(effect_bps, sd_bps, haircut=0.5, power=0.80, alpha=0.05):
    """N independent episodes to confirm a HAIRCUT edge forward at power 80%, alpha 5%."""
    e = abs(effect_bps) * haircut
    if not np.isfinite(e) or e <= 0 or not np.isfinite(sd_bps) or sd_bps <= 0:
        return np.nan
    z_a, z_b = 1.959963985, 0.8416212336
    return float(np.ceil(((z_a + z_b) * sd_bps / e) ** 2))


def run_gate(d, ret_col, name, ts="_ts", sym="symbol", ep_col="_ep",
             cost=COST, sign=1.0, note=""):
    """Full round-4 §2 gate on one arm.

    `ret_col` must be in bps and already forward-only. `sign` = +1 long, -1 short/fade.
    """
    d = d.dropna(subset=[ret_col]).copy()
    d["_r"] = sign * d[ret_col].astype(float)
    n_raw = len(d)
    if n_raw == 0:
        return {"name": name, "n_raw": 0, "verdict": "DEAD", "note": "empty"}

    d1 = decluster_L1(d, ts=ts, sym=sym)
    nL1 = len(d1)
    nL2 = n_L2(d, ts=ts)
    ep = d[ep_col] if ep_col in d.columns else pd.Series(np.arange(len(d)), index=d.index)
    nL3 = int(pd.Series(ep).nunique())

    # episode-level means = the independent unit
    g = d.assign(_ep_=ep.values).groupby("_ep_")["_r"].mean()
    gross_ep = float(g.mean())
    sd_ep = float(g.std(ddof=1)) if len(g) > 1 else np.nan
    net = gross_ep - cost
    net28 = gross_ep - COST_STRESS
    t_decl = _tstat(g.values - cost)
    ci = _block_boot_ci(g.values - cost)

    dd = d.copy()
    dd["_y"] = pd.to_datetime(dd[ts], utc=True).dt.year
    dd["_ep_"] = ep.values
    yby = {}
    for y, gy in dd.groupby("_y"):
        gm = gy.groupby("_ep_")["_r"].mean()
        yby[int(y)] = {"n_raw": int(len(gy)), "n_ep": int(gm.nunique() if hasattr(gm, "nunique") else len(gm)),
                       "net_bps": round(float(gm.mean()) - cost, 2)}
    # ex-best-year
    if len(yby) > 1:
        best = max(yby, key=lambda y: yby[y]["net_bps"])
        sub = dd[dd["_y"] != best]
        gm = sub.groupby("_ep_")["_r"].mean()
        exbest = {"dropped_year": int(best), "net_bps": round(float(gm.mean()) - cost, 2),
                  "n_ep": int(len(gm)), "t": round(_tstat(gm.values - cost), 2) if len(gm) > 2 else None}
    else:
        exbest = {"dropped_year": None, "net_bps": None, "n_ep": None, "t": None}

    # concentration: share of total episode-bps carried by the single best year
    tot = sum(abs(v["net_bps"]) * v["n_ep"] for v in yby.values()) or np.nan
    conc = max((abs(v["net_bps"]) * v["n_ep"] for v in yby.values()), default=np.nan) / tot if tot == tot else np.nan

    # event rate on the LAST 6 MONTHS of the sample (conservative)
    tmax = pd.to_datetime(d[ts], utc=True).max()
    recent = dd[pd.to_datetime(dd[ts], utc=True) >= tmax - pd.Timedelta(days=182)]
    n_ep_recent = int(recent["_ep_"].nunique()) if len(recent) else 0
    weeks = 182 / 7.0
    rate = n_ep_recent / weeks

    nreq = n_required(net, sd_ep)
    eta_w = nreq / rate if (rate > 0 and np.isfinite(nreq)) else np.inf
    eta_days = eta_w * 7 if np.isfinite(eta_w) else np.inf
    eta_years = eta_days / 365.25 if np.isfinite(eta_days) else np.inf

    return {
        "name": name, "note": note,
        "n_raw": int(n_raw), "n_independent_L1": int(nL1),
        "n_independent_L2": int(nL2), "n_independent_L3": int(nL3),
        "gross_bps": round(gross_ep, 2),
        "net_bps": round(net, 2), "net_bps_stress28": round(net28, 2),
        "sd_episode_bps": round(sd_ep, 2) if sd_ep == sd_ep else None,
        "t_stat_declustered": round(t_decl, 3) if t_decl == t_decl else None,
        "bootstrap_ci95": [round(ci[0], 2), round(ci[1], 2)] if ci[0] == ci[0] else None,
        "year_by_year": yby, "ex_best_year": exbest,
        "year_concentration_frac": round(float(conc), 3) if conc == conc else None,
        "n_required": None if not np.isfinite(nreq) else int(nreq),
        "event_rate_per_week_6m": round(rate, 2),
        "eta_forward_confirmation_days": None if not np.isfinite(eta_days) else round(eta_days, 1),
        "eta_forward_confirmation_years": None if not np.isfinite(eta_years) else round(eta_years, 2),
    }


def arm_spread(a, b, label):
    """Arm A minus arm B on the same population — the ONLY legitimate regime claim."""
    return {"comparison": label,
            "net_A": a.get("net_bps"), "net_B": b.get("net_bps"),
            "spread_bps": None if (a.get("net_bps") is None or b.get("net_bps") is None)
                          else round(a["net_bps"] - b["net_bps"], 2),
            "nA_L3": a.get("n_independent_L3"), "nB_L3": b.get("n_independent_L3")}
