#!/usr/bin/env python3
"""
W4 calendar-basis / futures-curve mining, alpha_hunt_2026-08-30.

Reuses the w3 (2026-08-29) episode-decluster discipline throughout:
- fit extremity thresholds on the first 60% of chronological data (train),
  apply/test on the last 40% (test) -- except for theory-driven hard
  thresholds (M9 inversion: basis<0) or naturally-scheduled entries (M5
  roll-down), which don't need a fit quantile.
- decluster: one entry per contiguous regime episode (new episode when the
  regime label flips or a date gap appears), NOT one row per day.
- always report true independent-episode N, worst single episode, gross and
  net bps at both base (14bps) and stress (28bps) 2-leg round-trip cost, and
  year-by-year stability.

TWO data pitfalls guarded against explicitly (both found while building this):
1. [w3 pitfall #4, reused] annualized basis explodes as dte->0 (up to
   +-2500-3100%) -- any THRESHOLD FIT or entry classification uses only rows
   with near_dte>=7.
2. [new, found here] contract-roll contamination: if an episode enters near
   its contract's expiry and the k-day holding window crosses the roll date,
   the panel's "near" column at exit refers to a DIFFERENT contract (the new
   front month), so entry-to-exit is not measuring convergence of the same
   instrument at all -- it's comparing two unrelated contracts and injects
   huge spurious swings. Guard: an entry is only used for horizon k if
   near_dte(entry) > k, which (given quarterly listings only ever add
   longer-dated contracts) guarantees the near AND next contract identities
   are unchanged through the entire holding window.

Cost model per mission spec: 2-leg calendar/basis spread round-trip cost
base=14bps, stress=28bps. Cross-asset dispersion (M8) trades TWO calendar
spreads (4 legs) -> cost doubled: base=28bps, stress=56bps (flagged in its row).
"""
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sstats

warnings.filterwarnings("ignore")

ROOT = Path("/home/qbee/futur")
EV = ROOT / "reports/edge_discovery/alpha_hunt_2026-08-30/w4_calendar_basis/evidence"

COST_BASE = 14.0
COST_STRESS = 28.0
COST_BASE_4LEG = 28.0
COST_STRESS_4LEG = 56.0
MIN_DTE = 7  # w3 pitfall #4 floor

HORIZONS = [1, 3, 7, 14, 30]


def load_panel(symbol):
    df = pd.read_parquet(EV / f"panel_{symbol}.parquet")
    df = df.sort_values("date").reset_index(drop=True)
    df["basis_near_bps"] = df["basis_near_pct"] * 100.0
    df["basis_next_bps"] = df["basis_next_pct"] * 100.0
    df["cal_spread_bps"] = df["cal_spread_pct"] * 100.0
    return df


def train_test_split(df, train_frac=0.6):
    n = len(df)
    i = int(n * train_frac)
    return df.iloc[:i].copy(), df.iloc[i:].copy()


def fit_thresholds(train_series, lo_q=0.10, hi_q=0.90):
    s = train_series.dropna()
    return float(s.quantile(lo_q)), float(s.quantile(hi_q))


def classify(series, lo, hi):
    out = np.where(series >= hi, "RICH", np.where(series <= lo, "CHEAP", "NEUTRAL"))
    return pd.Series(out, index=series.index)


def build_episodes(df, regime_col, date_col="date"):
    """One row per contiguous regime run over a (possibly gappy, e.g. after
    dropping dte<7 rows) date-sorted frame. A calendar-day gap always starts
    a new episode, so pre-filtering rows out is a safe way to forbid
    ineligible days from being folded into an episode."""
    d = df.sort_values(date_col).reset_index(drop=True)
    regime = d[regime_col].astype(object).values
    dates = d[date_col]
    day_diff = dates.diff().dt.days.fillna(1).values
    changed = np.empty(len(d), dtype=bool)
    changed[0] = True
    if len(d) > 1:
        changed[1:] = (regime[1:] != regime[:-1]) | (day_diff[1:] > 1)
    d["episode_id"] = np.cumsum(changed)
    return d


def episode_entries(df, regime_col, date_col="date", keep=("RICH", "CHEAP")):
    epd = build_episodes(df, regime_col, date_col)
    first = epd.groupby("episode_id").first().reset_index()
    first = first[first[regime_col].isin(keep)].copy()
    return first


def forward_value(full_df, date_col, value_col, entry_dates, k):
    idx = full_df.set_index(date_col)[value_col]
    targets = entry_dates + pd.Timedelta(days=k)
    return targets.map(idx)


def nonoverlap_filter_variable(entries, hold_days_col, date_col="date"):
    """Same idea as nonoverlap_filter but for the expiry-capped PnL functions,
    where each entry's realized holding period can be shorter than the
    nominal k (when capped at expiry) -- uses each kept entry's OWN realized
    hold_days as the required gap before the next entry is eligible."""
    entries = entries.sort_values(date_col).reset_index(drop=True)
    keep_idx = []
    last_date, last_hold = None, None
    for i, row in entries.iterrows():
        d = row[date_col]
        if last_date is None or (d - last_date).days >= last_hold:
            keep_idx.append(i)
            last_date, last_hold = d, row[hold_days_col]
    return entries.loc[keep_idx].reset_index(drop=True)


def nonoverlap_filter(entries, k, date_col="date"):
    """SECOND decluster pass, on top of regime-contiguity episodes. A
    continuous-signal threshold can flicker across its own boundary within
    one underlying macro regime (CHEAP -> NEUTRAL -> CHEAP over a few days),
    producing many "episodes" whose k-day holding windows overlap -- these
    are not independent observations, they are the same regime measured
    several times, exactly the autocorrelation trap that sank the original
    (pre-decluster) calendar-basis finding, resurfacing one level down.
    Greedy left-to-right: keep an entry only if it starts at least k days
    after the previously KEPT entry, guaranteeing no two retained holding
    windows overlap in calendar time."""
    entries = entries.sort_values(date_col).reset_index(drop=True)
    keep_idx = []
    last_date = None
    for i, row in entries.iterrows():
        d = row[date_col]
        if last_date is None or (d - last_date).days >= k:
            keep_idx.append(i)
            last_date = d
    return entries.loc[keep_idx].reset_index(drop=True)


def episode_pnl(full_df, entries, value_col, regime_col, k, rich_side=-1, cheap_side=1,
                 dte_col="near_dte"):
    """Guards against contract-roll contamination: entries whose near_dte does
    not exceed k are dropped (guarantees same-contract entry/exit). Used for
    the near-vs-next calendar spread (M2/M3/M10), where there is no clean
    convergence target once the 'near' identity rolls."""
    entries = entries.copy()
    entries = nonoverlap_filter(entries, k)
    if dte_col in entries.columns:
        entries = entries[entries[dte_col] > k].copy()
    entries["exit_date"] = entries["date"] + pd.Timedelta(days=k)
    exit_val = forward_value(full_df, "date", value_col, entries["date"], k)
    entries["entry_val"] = entries[value_col]
    entries["exit_val"] = exit_val.values
    entries = entries.dropna(subset=["exit_val"]).copy()
    side = np.where(entries[regime_col] == "RICH", rich_side, cheap_side)
    entries["side"] = side
    entries["pnl_bps"] = entries["side"] * (entries["exit_val"] - entries["entry_val"])
    return entries[["date", "exit_date", regime_col, "entry_val", "exit_val", "side", "pnl_bps"]]


def episode_pnl_basis_near_capped(full_df, entries, value_col, regime_col, k, rich_side=-1,
                                   cheap_side=1, dte_col="near_dte"):
    """Variant for perp-vs-quarterly BASIS mechanisms only (M1/M4/M7/M9): a
    quarterly future is cash-settled to the index at expiry, so basis_near is
    CONTRACTUALLY guaranteed to converge to (approximately) 0 at dte=0 -- this
    is a structural fact, not an estimated/fitted parameter. So instead of
    dropping entries whose near_dte<=k (as the strict guard above does, which
    is correct for cal-spread mechanisms but throws away a lot of legitimate
    RICH/CHEAP episodes here, since extreme-basis episodes empirically
    cluster near roll dates), we cap the holding period at
    min(k, near_dte_at_entry) and use exit_val=0 for capped entries. This
    recovers real sample size without contamination: capped entries still
    measure genuine convergence (to a known, not roll-contaminated, endpoint),
    just over a shorter realized holding period than k days."""
    entries = entries.copy()
    hold_days = np.minimum(k, entries[dte_col].values)
    entries["hold_days"] = hold_days
    entries = nonoverlap_filter_variable(entries, "hold_days")
    hold_days = entries["hold_days"].values
    entries["exit_date"] = entries["date"] + pd.to_timedelta(hold_days, unit="D")
    capped = entries[dte_col].values <= k
    exit_val = pd.Series(index=entries.index, dtype=float)
    nc_idx = entries.index[~capped]
    if len(nc_idx):
        exit_val.loc[nc_idx] = forward_value(full_df, "date", value_col, entries.loc[nc_idx, "date"], k).values
    exit_val.loc[entries.index[capped]] = 0.0
    entries["entry_val"] = entries[value_col]
    entries["exit_val"] = exit_val.values
    entries = entries.dropna(subset=["exit_val"]).copy()
    side = np.where(entries[regime_col] == "RICH", rich_side, cheap_side)
    entries["side"] = side
    entries["pnl_bps"] = entries["side"] * (entries["exit_val"] - entries["entry_val"])
    entries["capped"] = entries[dte_col].values <= k
    return entries[["date", "exit_date", "hold_days", regime_col, "entry_val", "exit_val", "side", "pnl_bps", "capped"]]


def summarize(pnl_bps, cost_base=COST_BASE, cost_stress=COST_STRESS):
    pnl = np.asarray(pnl_bps, dtype=float)
    n = len(pnl)
    out = {"n": int(n)}
    if n == 0:
        return out
    mean = float(np.mean(pnl))
    std = float(np.std(pnl, ddof=1)) if n > 1 else float("nan")
    t = mean / (std / np.sqrt(n)) if n > 1 and std > 0 else float("nan")
    p = float(2 * (1 - sstats.t.cdf(abs(t), df=n - 1))) if n > 1 and not np.isnan(t) else float("nan")
    out.update({
        "gross_mean_bps": round(mean, 2),
        "std_bps": round(std, 2) if not np.isnan(std) else None,
        "t_stat": round(t, 3) if not np.isnan(t) else None,
        "p_value": round(p, 4) if not np.isnan(p) else None,
        "net_base_bps": round(mean - cost_base, 2),
        "net_stress_bps": round(mean - cost_stress, 2),
        "worst_bps": round(float(np.min(pnl)), 2),
        "best_bps": round(float(np.max(pnl)), 2),
        "win_rate": round(float((pnl > 0).mean()), 3),
    })
    return out


def year_stability(entries):
    if len(entries) == 0:
        return {}
    e = entries.copy()
    e["year"] = e["date"].dt.year
    g = e.groupby("year")["pnl_bps"].agg(["count", "mean"])
    return {int(y): {"n": int(r["count"]), "mean_bps": round(float(r["mean"]), 2)} for y, r in g.iterrows()}


RESULTS = []
EPISODE_LEDGERS = {}


def run_decile_mechanism(name, symbol, panel, signal_col, value_col, lo_q=0.10, hi_q=0.90,
                          horizons=HORIZONS, cost_base=COST_BASE, cost_stress=COST_STRESS,
                          rich_side=-1, cheap_side=1, min_dte=MIN_DTE, dte_col="near_dte",
                          extra_cols=None, pnl_fn=episode_pnl):
    extra_cols = extra_cols or []
    df_full = panel.copy()
    # eligibility mask applied BEFORE threshold-fit and classification (pitfall #4)
    elig = panel.dropna(subset=[signal_col, value_col, dte_col]).copy()
    elig = elig[elig[dte_col] >= min_dte]
    train, test = train_test_split(elig)
    lo, hi = fit_thresholds(train[signal_col], lo_q, hi_q)
    test = test.copy()
    test["regime"] = classify(test[signal_col], lo, hi).values
    entries_all = episode_entries(test, "regime")
    row_out = {"mechanism": name, "symbol": symbol, "train_thresholds": {"lo": round(lo, 3), "hi": round(hi, 3)},
               "n_train": int(len(train)), "n_test": int(len(test))}
    per_horizon = {}
    for k in horizons:
        cols = ["date", "regime", signal_col, value_col, dte_col] + extra_cols
        entries_k = entries_all[cols].copy()
        pnl_df = pnl_fn(df_full, entries_k, value_col, "regime", k, rich_side, cheap_side, dte_col=dte_col)
        s = summarize(pnl_df["pnl_bps"], cost_base, cost_stress)
        s["stability_by_year"] = year_stability(pnl_df)
        s["n_rich"] = int((pnl_df["regime"] == "RICH").sum())
        s["n_cheap"] = int((pnl_df["regime"] == "CHEAP").sum())
        if "capped" in pnl_df.columns:
            s["n_expiry_capped"] = int(pnl_df["capped"].sum())
        per_horizon[f"k{k}d"] = s
        EPISODE_LEDGERS[f"{name}_{symbol}_k{k}d"] = pnl_df
    row_out["horizons"] = per_horizon
    RESULTS.append(row_out)
    return row_out


print("Loading panels...")
panel_btc = load_panel("BTCUSDT")
panel_eth = load_panel("ETHUSDT")
PANELS = {"BTCUSDT": panel_btc, "ETHUSDT": panel_eth}

# ============================================================
# M1: perp vs near-quarterly basis, ANNUALIZED-basis-decile entry, multi-horizon
# ============================================================
print("M1 perp-vs-near-quarterly ...")
for sym, panel in PANELS.items():
    run_decile_mechanism("M1_PERP_VS_NEAR", sym, panel,
                          signal_col="basis_near_ann", value_col="basis_near_bps",
                          pnl_fn=episode_pnl_basis_near_capped)

# ============================================================
# M2: near-vs-next quarterly calendar spread mean reversion (genuinely new spread)
# ============================================================
print("M2 near-vs-next calendar spread ...")
for sym, panel in PANELS.items():
    run_decile_mechanism("M2_QQ_SPREAD_MEANREV", sym, panel,
                          signal_col="cal_spread_ann", value_col="cal_spread_bps")

# ============================================================
# M3: curve slope momentum vs reversion (5d change in cal_spread_ann)
# direction pre-committed on TRAIN.
# ============================================================
print("M3 curve slope momentum/reversion ...")
for sym, panel in PANELS.items():
    df = panel.dropna(subset=["cal_spread_ann", "cal_spread_bps", "near_dte"]).copy()
    df = df[df["near_dte"] >= MIN_DTE]
    df["slope_chg_5d"] = df["cal_spread_ann"].diff(5)
    df = df.dropna(subset=["slope_chg_5d"])
    train, test = train_test_split(df)
    lo, hi = fit_thresholds(train["slope_chg_5d"])
    train_t = train.copy()
    train_t["regime"] = classify(train_t["slope_chg_5d"], lo, hi).values
    train_entries = episode_entries(train_t, "regime")
    tcols = ["date", "regime", "slope_chg_5d", "cal_spread_bps", "near_dte"]
    train_entries_k7 = train_entries[tcols].copy()
    pnl_mom = episode_pnl(train, train_entries_k7, "cal_spread_bps", "regime", 7, rich_side=1, cheap_side=-1)
    pnl_rev = episode_pnl(train, train_entries_k7, "cal_spread_bps", "regime", 7, rich_side=-1, cheap_side=1)
    mom_mean = pnl_mom["pnl_bps"].mean() if len(pnl_mom) else -1e9
    rev_mean = pnl_rev["pnl_bps"].mean() if len(pnl_rev) else -1e9
    direction = "momentum" if mom_mean > rev_mean else "reversion"
    rich_side, cheap_side = (1, -1) if direction == "momentum" else (-1, 1)

    test_t = test.copy()
    test_t["regime"] = classify(test_t["slope_chg_5d"], lo, hi).values
    entries_all = episode_entries(test_t, "regime")
    per_horizon = {}
    for k in HORIZONS:
        entries_k = entries_all[tcols].copy()
        pnl_df = episode_pnl(df, entries_k, "cal_spread_bps", "regime", k, rich_side, cheap_side)
        s = summarize(pnl_df["pnl_bps"])
        s["stability_by_year"] = year_stability(pnl_df)
        per_horizon[f"k{k}d"] = s
        EPISODE_LEDGERS[f"M3_CURVE_SLOPE_MOM_{sym}_k{k}d"] = pnl_df
    RESULTS.append({"mechanism": "M3_CURVE_SLOPE_MOMENTUM", "symbol": sym,
                     "direction_precommitted_on_train": direction,
                     "train_thresholds": {"lo": round(lo, 3), "hi": round(hi, 3)},
                     "horizons": per_horizon})

# ============================================================
# M4: time-to-expiry-normalized basis z-score (bucket by dte quintile on TRAIN)
# ============================================================
print("M4 TTE-normalized basis z-score ...")
for sym, panel in PANELS.items():
    df = panel.dropna(subset=["basis_near_ann", "basis_near_bps", "near_dte"]).copy()
    df = df[df["near_dte"] >= MIN_DTE]
    train, test = train_test_split(df)
    train_bins = pd.qcut(train["near_dte"], 5, duplicates="drop")
    train = train.copy()
    train["dte_bin"] = train_bins
    bin_stats = train.groupby("dte_bin", observed=True)["basis_near_ann"].agg(["mean", "std"])

    def assign_bin(dte_val):
        for iv in bin_stats.index:
            if iv.left < dte_val <= iv.right:
                return iv
        if dte_val <= bin_stats.index[0].left:
            return bin_stats.index[0]
        return bin_stats.index[-1]

    def zscore(sub):
        bins = sub["near_dte"].apply(assign_bin)
        m = bins.map(bin_stats["mean"])
        s = bins.map(bin_stats["std"])
        return (sub["basis_near_ann"] - m) / s

    df = df.assign(tte_z=zscore(df))
    train2, test2 = train_test_split(df)
    lo, hi = fit_thresholds(train2["tte_z"])
    test2 = test2.copy()
    test2["regime"] = classify(test2["tte_z"], lo, hi).values
    entries_all = episode_entries(test2, "regime")
    per_horizon = {}
    cols = ["date", "regime", "tte_z", "basis_near_bps", "near_dte"]
    for k in HORIZONS:
        entries_k = entries_all[cols].copy()
        pnl_df = episode_pnl_basis_near_capped(df, entries_k, "basis_near_bps", "regime", k, rich_side=-1, cheap_side=1)
        s = summarize(pnl_df["pnl_bps"])
        s["stability_by_year"] = year_stability(pnl_df)
        s["n_rich"] = int((pnl_df["regime"] == "RICH").sum())
        s["n_cheap"] = int((pnl_df["regime"] == "CHEAP").sum())
        per_horizon[f"k{k}d"] = s
        EPISODE_LEDGERS[f"M4_TTE_NORM_{sym}_k{k}d"] = pnl_df
    RESULTS.append({"mechanism": "M4_TTE_NORMALIZED_ZSCORE", "symbol": sym,
                     "train_thresholds": {"lo": round(lo, 3), "hi": round(hi, 3)},
                     "horizons": per_horizon})

print(f"Checkpoint: {len(RESULTS)} mechanism-symbol rows (M1-M4)")
with open(EV / "checkpoint_m1_m4.json", "w") as f:
    json.dump(RESULTS, f, indent=2, default=str)

# ============================================================
# M5: expiry-convergence roll-down harvest -- systematic, unconditional
# entries whenever near_dte first enters a fixed target band, NOT
# extremity-conditional (tests whether basis converges predictably as
# expiry approaches, tradeable via a fixed-schedule carry harvest).
# One entry per contract per target band (natural decluster: dte decreases
# monotonically through a contract's life, so it crosses any band once).
# Direction chosen by the SIGN of basis at entry (converge-to-zero bet).
# ============================================================
print("M5 expiry-convergence roll-down harvest ...")
ROLLDOWN_TARGETS = [15, 30, 45, 60, 75]
for sym, panel in PANELS.items():
    df = panel.dropna(subset=["basis_near_bps", "near_dte", "near_contract"]).copy()
    df = df.sort_values("date").reset_index(drop=True)
    for dte0 in ROLLDOWN_TARGETS:
        k = min(7, dte0 - 1) if dte0 <= 14 else 7
        band = df[(df["near_dte"] >= dte0 - 2) & (df["near_dte"] <= dte0 + 2)]
        entries = band.sort_values("date").groupby("near_contract", as_index=False).first()
        entries = entries[["date", "near_contract", "basis_near_bps", "near_dte"]].copy()
        entries["regime"] = np.where(entries["basis_near_bps"] > 0, "RICH", "CHEAP")
        pnl_df = episode_pnl_basis_near_capped(df, entries, "basis_near_bps", "regime", k,
                                                rich_side=-1, cheap_side=1)
        s = summarize(pnl_df["pnl_bps"])
        s["stability_by_year"] = year_stability(pnl_df)
        s["n_contracts"] = int(len(entries))
        RESULTS.append({"mechanism": "M5_ROLLDOWN_HARVEST", "symbol": sym,
                         "dte0": dte0, "k": k, "horizons": {f"k{k}d": s}})
        EPISODE_LEDGERS[f"M5_ROLLDOWN_{sym}_dte{dte0}_k{k}d"] = pnl_df

# ============================================================
# M6: basis jump events -- 1-day change in basis_near_bps, z-scored by a
# causal trailing-60d rolling std (shift(1), no lookahead). Direction
# (continuation vs reversal) pre-committed on TRAIN, tested OOS. Event-like
# by construction (isolated spikes), horizons k1/3/7 only.
# ============================================================
print("M6 basis jump events ...")
for sym, panel in PANELS.items():
    df = panel.dropna(subset=["basis_near_bps", "near_dte"]).copy().sort_values("date").reset_index(drop=True)
    df = df[df["near_dte"] >= MIN_DTE]
    df["chg_1d"] = df["basis_near_bps"].diff(1)
    df["roll_std"] = df["chg_1d"].rolling(60, min_periods=30).std().shift(1)
    df["jump_z"] = df["chg_1d"] / df["roll_std"]
    df = df.dropna(subset=["jump_z"])
    train, test = train_test_split(df)
    # theory: a jump is |z| large; fit an extremity threshold on TRAIN |z|
    hi_abs = float(train["jump_z"].abs().quantile(0.90))
    train_j = train.copy()
    train_j["regime"] = np.where(train_j["jump_z"] >= hi_abs, "UP_JUMP",
                                  np.where(train_j["jump_z"] <= -hi_abs, "DOWN_JUMP", "NEUTRAL"))
    train_entries = episode_entries(train_j, "regime", keep=("UP_JUMP", "DOWN_JUMP"))
    tcols = ["date", "regime", "jump_z", "basis_near_bps", "near_dte"]
    te = train_entries[tcols].copy()
    # continuation: UP_JUMP -> bet basis keeps rising (side=+1); reversal: bet it falls back (side=-1)
    pnl_cont = episode_pnl_basis_near_capped(train, te, "basis_near_bps", "regime", 3,
                                              rich_side=-1, cheap_side=1)  # placeholder, recompute below
    # build directly: side=+1*sign(UP=+1/DOWN=-1) for continuation, opposite for reversal
    def jump_pnl(base_df, entries_df, k, mode):
        e = entries_df.copy()
        jump_dir = np.where(e["regime"] == "UP_JUMP", 1, -1)
        side = jump_dir if mode == "continuation" else -jump_dir
        e["rich_flag"] = np.where(side == 1, "CHEAP", "RICH")  # reuse capped fn's RICH=-1/CHEAP=+1 convention
        pnl = episode_pnl_basis_near_capped(base_df, e, "basis_near_bps", "rich_flag", k,
                                             rich_side=-1, cheap_side=1)
        return pnl

    pnl_cont_train = jump_pnl(train, te, 3, "continuation")
    pnl_rev_train = jump_pnl(train, te, 3, "reversal")
    cont_mean = pnl_cont_train["pnl_bps"].mean() if len(pnl_cont_train) else -1e9
    rev_mean = pnl_rev_train["pnl_bps"].mean() if len(pnl_rev_train) else -1e9
    mode = "continuation" if cont_mean > rev_mean else "reversal"

    test_j = test.copy()
    test_j["regime"] = np.where(test_j["jump_z"] >= hi_abs, "UP_JUMP",
                                 np.where(test_j["jump_z"] <= -hi_abs, "DOWN_JUMP", "NEUTRAL"))
    entries_all = episode_entries(test_j, "regime", keep=("UP_JUMP", "DOWN_JUMP"))
    per_horizon = {}
    for k in [1, 3, 7]:
        entries_k = entries_all[tcols].copy()
        pnl_df = jump_pnl(df, entries_k, k, mode)
        s = summarize(pnl_df["pnl_bps"])
        s["stability_by_year"] = year_stability(pnl_df)
        s["n_up"] = int((entries_k["regime"] == "UP_JUMP").sum())
        s["n_down"] = int((entries_k["regime"] == "DOWN_JUMP").sum())
        per_horizon[f"k{k}d"] = s
        EPISODE_LEDGERS[f"M6_JUMPS_{sym}_k{k}d"] = pnl_df
    RESULTS.append({"mechanism": "M6_BASIS_JUMPS", "symbol": sym,
                     "direction_precommitted_on_train": mode, "jump_threshold_abs_z": round(hi_abs, 2),
                     "horizons": per_horizon})

# ============================================================
# M7: funding-implied carry vs quarterly-implied carry disagreement.
# disagreement = funding_ann_pct - basis_near_ann. RICH disagreement (funding
# >> quarterly) -> bet basis_near rises to catch up (side=+1, i.e. LONG
# quarterly/SHORT perp); CHEAP disagreement -> bet basis falls (side=-1).
# Note: inverted relative to M1's RICH/CHEAP convention (pass rich_side=+1).
# ============================================================
print("M7 funding-vs-basis disagreement ...")
for sym, panel in PANELS.items():
    df = panel.dropna(subset=["funding_ann_pct", "basis_near_ann", "basis_near_bps", "near_dte"]).copy()
    df = df[df["near_dte"] >= MIN_DTE].copy()
    df["disagreement"] = df["funding_ann_pct"] - df["basis_near_ann"]
    run_decile_mechanism("M7_FUNDING_BASIS_DISAGREEMENT", sym, df,
                          signal_col="disagreement", value_col="basis_near_bps",
                          rich_side=1, cheap_side=-1, pnl_fn=episode_pnl_basis_near_capped)

# ============================================================
# M8: cross-asset (BTC vs ETH) calendar-basis dispersion -- 4-leg RV trade
# (long one asset's calendar spread, short the other's). Cost doubled.
# ============================================================
print("M8 cross-asset dispersion ...")
btc = panel_btc[["date", "basis_near_ann", "basis_near_bps", "near_dte"]].rename(
    columns={"basis_near_ann": "basis_ann_btc", "basis_near_bps": "basis_bps_btc", "near_dte": "dte_btc"})
eth = panel_eth[["date", "basis_near_ann", "basis_near_bps", "near_dte"]].rename(
    columns={"basis_near_ann": "basis_ann_eth", "basis_near_bps": "basis_bps_eth", "near_dte": "dte_eth"})
merged = btc.merge(eth, on="date", how="inner").dropna()
merged = merged[(merged["dte_btc"] >= MIN_DTE) & (merged["dte_eth"] >= MIN_DTE)]
merged["disp_ann"] = merged["basis_ann_btc"] - merged["basis_ann_eth"]
merged["disp_bps"] = merged["basis_bps_btc"] - merged["basis_bps_eth"]
merged["near_dte"] = np.minimum(merged["dte_btc"], merged["dte_eth"])  # conservative: whichever leg expires first
run_decile_mechanism("M8_CROSS_ASSET_DISPERSION", "BTC_vs_ETH", merged,
                      signal_col="disp_ann", value_col="disp_bps",
                      cost_base=COST_BASE_4LEG, cost_stress=COST_STRESS_4LEG,
                      pnl_fn=episode_pnl_basis_near_capped)

# ============================================================
# M9: curve inversion event (basis_near_pct<0, perp above quarterly -- true
# backwardation). Hard, theory-driven threshold (0), NOT fit from data, so
# uses the FULL 2021-2026 sample rather than train/test (no data-snooping
# risk since the cutoff isn't estimated). Trade: LONG quarterly/SHORT perp,
# betting reversion back to normal contango.
# ============================================================
print("M9 curve inversion event ...")
for sym, panel in PANELS.items():
    df = panel.dropna(subset=["basis_near_pct", "basis_near_bps", "near_dte"]).copy()
    df = df[df["near_dte"] >= MIN_DTE].copy()
    df["regime"] = np.where(df["basis_near_pct"] < 0, "CHEAP", "NEUTRAL")  # backwardation = CHEAP (bet basis rises)
    entries_all = episode_entries(df, "regime", keep=("CHEAP",))
    per_horizon = {}
    cols = ["date", "regime", "basis_near_bps", "near_dte"]
    for k in HORIZONS:
        entries_k = entries_all[cols].copy()
        pnl_df = episode_pnl_basis_near_capped(df, entries_k, "basis_near_bps", "regime", k,
                                                rich_side=-1, cheap_side=1)
        s = summarize(pnl_df["pnl_bps"])
        s["stability_by_year"] = year_stability(pnl_df)
        per_horizon[f"k{k}d"] = s
        EPISODE_LEDGERS[f"M9_INVERSION_{sym}_k{k}d"] = pnl_df
    RESULTS.append({"mechanism": "M9_CURVE_INVERSION_EVENT", "symbol": sym,
                     "threshold": "basis_near_pct<0 (theory-driven, full-sample, not fit)",
                     "n_inversion_days_raw": int((panel["basis_near_pct"] < 0).sum()),
                     "horizons": per_horizon})

# ============================================================
# M10: curve dislocation vs its own trailing 180d distribution (rolling
# z-score of cal_spread_ann, causal shift(1)) -- adaptive/live-tradeable
# relative-value signal, distinct from M2's fixed global-quantile design.
# ============================================================
print("M10 rolling curve dislocation ...")
for sym, panel in PANELS.items():
    df = panel.dropna(subset=["cal_spread_ann", "cal_spread_bps", "near_dte"]).copy().sort_values("date")
    df = df[df["near_dte"] >= MIN_DTE].reset_index(drop=True)
    roll_mean = df["cal_spread_ann"].rolling(180, min_periods=90).mean().shift(1)
    roll_std = df["cal_spread_ann"].rolling(180, min_periods=90).std().shift(1)
    df["dislocation_z"] = (df["cal_spread_ann"] - roll_mean) / roll_std
    df = df.dropna(subset=["dislocation_z"])
    run_decile_mechanism("M10_CURVE_DISLOCATION_ROLLING", sym, df,
                          signal_col="dislocation_z", value_col="cal_spread_bps",
                          pnl_fn=episode_pnl)

print(f"\nFINAL: {len(RESULTS)} mechanism-symbol-parameterization rows")
with open(EV / "all_results.json", "w") as f:
    json.dump(RESULTS, f, indent=2, default=str)

# save a handful of representative episode ledgers as small CSVs
for key in ["M1_PERP_VS_NEAR_BTCUSDT_k7d", "M1_PERP_VS_NEAR_ETHUSDT_k7d",
            "M2_QQ_SPREAD_MEANREV_BTCUSDT_k7d", "M2_QQ_SPREAD_MEANREV_ETHUSDT_k7d",
            "M9_INVERSION_BTCUSDT_k14d", "M9_INVERSION_ETHUSDT_k14d",
            "M7_FUNDING_BASIS_DISAGREEMENT_BTCUSDT_k7d", "M8_CROSS_ASSET_DISPERSION_BTC_vs_ETH_k7d",
            "M10_CURVE_DISLOCATION_ROLLING_BTCUSDT_k7d"]:
    if key in EPISODE_LEDGERS:
        EPISODE_LEDGERS[key].to_csv(EV / f"episodes_{key}.csv", index=False)

print("Done. Wrote all_results.json and episode CSVs to evidence/.")
