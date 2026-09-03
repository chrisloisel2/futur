"""
Group 4: meta-signals layered on M7 (funding-implied vs quarterly-implied carry
disagreement), round 2 W4's one PROMISING calendar-basis mechanism
(reports/edge_discovery/alpha_hunt_2026-08-30/w4_calendar_basis/REPORT.md).
Base signal (WITHOUT arm) is an exact re-derivation of round 2's own M7 construction
(same code shape: train_frac=0.6 threshold fit on the FIRST 60% of eligible rows,
decile classify+episode ONLY on the held-out last 40%, so the base ledger itself is
already OOS relative to its own threshold) applied to BOTH BTCUSDT and ETHUSDT using
the exact panels W4 already built (panel_{BTC,ETH}USDT.parquet, read-only reuse, not
rebuilt), k7d horizon (best-N horizon per W4's own file selection), cost = 14bps base
(project's stated base-cost convention for this 2-leg calendar-basis mechanism, per W4).
N here is inherently thin (round 2 flagged 15-24 "true independent" episodes on THIS
exact mechanism) so these results carry a DATA_LIMITED flag by construction -- reported
honestly, not oversold.
"""
import json
import numpy as np
import pandas as pd

ROOT = "/home/qbee/futur"
EV_W4 = f"{ROOT}/reports/edge_discovery/alpha_hunt_2026-08-30/w4_calendar_basis/evidence"
OUT = "/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w5/evidence/g4_funding_basis_meta.json"

COST_BASE = 14.0
MIN_DTE = 7


def load_panel(symbol):
    df = pd.read_parquet(f"{EV_W4}/panel_{symbol}.parquet")
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df["basis_near_bps"] = df["basis_near_pct"] * 100.0
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


def nonoverlap_filter_variable(entries, hold_days_col, date_col="date"):
    entries = entries.sort_values(date_col).reset_index(drop=True)
    keep_idx = []
    last_date, last_hold = None, None
    for i, row in entries.iterrows():
        d = row[date_col]
        if last_date is None or (d - last_date).days >= last_hold:
            keep_idx.append(i)
            last_date, last_hold = d, row[hold_days_col]
    return entries.loc[keep_idx].reset_index(drop=True)


def forward_value(full_df, date_col, value_col, entry_dates, k):
    idx = full_df.set_index(date_col)[value_col]
    targets = entry_dates + pd.Timedelta(days=k)
    return targets.map(idx)


def episode_pnl_basis_near_capped(full_df, entries, value_col, regime_col, k, rich_side=-1,
                                   cheap_side=1, dte_col="near_dte"):
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


def build_m7_episodes(symbol, k=14):
    # NOTE: round 2's W4 headline PROMISING result for M7 is at k14d/k30d (net_base_bps
    # +7.68/+33.21 BTC, +15.26/+30.32 ETH), NOT k7d (roughly breakeven, -0.08/+1.43) -- verified
    # by recomputing all 5 horizons against evidence/all_results.json before picking k14d as the
    # base signal to layer meta-signals onto (using the actually-promising horizon, not the one
    # that happened to have a saved episode CSV).
    panel = load_panel(symbol)
    df = panel.dropna(subset=["funding_ann_pct", "basis_near_ann", "basis_near_bps", "near_dte"]).copy()
    df = df[df["near_dte"] >= MIN_DTE].copy()
    df["disagreement"] = df["funding_ann_pct"] - df["basis_near_ann"]
    train, test = train_test_split(df)
    lo, hi = fit_thresholds(train["disagreement"])
    test = test.copy()
    test["regime"] = classify(test["disagreement"], lo, hi).values
    entries_all = episode_entries(test, "regime")
    cols = ["date", "regime", "disagreement", "basis_near_bps", "near_dte"]
    entries_k = entries_all[cols].copy()
    pnl_df = episode_pnl_basis_near_capped(df, entries_k, "basis_near_bps", "regime", k, rich_side=1, cheap_side=-1)
    # episode_pnl_basis_near_capped drops the original "disagreement" signal column (renames the
    # value_col into entry_val/exit_val instead) -- re-attach it by date so meta-signal tests that
    # need the disagreement MAGNITUDE (not basis_near_bps) can use it.
    pnl_df = pnl_df.merge(entries_k[["date", "disagreement"]], on="date", how="left")
    pnl_df["symbol"] = symbol
    pnl_df["net_bps"] = pnl_df["pnl_bps"] - COST_BASE
    return pnl_df, dict(lo=lo, hi=hi, n_train=len(train), n_test_days=len(test))


def summarize(bps):
    bps = np.asarray(bps, dtype=float)
    bps = bps[~np.isnan(bps)]
    n = len(bps)
    if n == 0:
        return dict(n=0, mean_bps=None, pf=None, t_stat=None, win_rate=None)
    mean = float(np.mean(bps))
    pos = bps[bps > 0].sum(); neg = -bps[bps < 0].sum()
    pf = float(pos / neg) if neg > 0 else (None if pos == 0 else float("inf"))
    t = float(mean / (np.std(bps, ddof=1) / np.sqrt(n))) if n > 1 and np.std(bps, ddof=1) > 0 else None
    return dict(n=n, mean_bps=round(mean, 2), pf=(round(pf, 3) if pf not in (None, float("inf")) else pf),
                t_stat=round(t, 2) if t is not None else None, win_rate=round(float((bps > 0).mean()), 3))


btc_pnl, btc_meta = build_m7_episodes("BTCUSDT", k=14)
eth_pnl, eth_meta = build_m7_episodes("ETHUSDT", k=14)
print("BTC M7 k14d episodes (OOS/test-half only, matches W4 construction):", btc_meta, "n=", len(btc_pnl))
print("ETH M7 k14d episodes:", eth_meta, "n=", len(eth_pnl))

pooled = pd.concat([btc_pnl, eth_pnl], ignore_index=True).sort_values("date").reset_index(drop=True)
BASELINE = summarize(pooled["net_bps"])
print("\n=== BASELINE (M7 replica, always-enabled, BTC+ETH pooled) ===")
print("raw N (pooled, both symbols independent-episode already by construction):", BASELINE)

results = {}
results["baseline_m7_funding_basis_disagreement"] = dict(
    btc_meta=btc_meta, eth_meta=eth_meta,
    btc_stats=summarize(btc_pnl["net_bps"]), eth_stats=summarize(eth_pnl["net_bps"]),
    pooled_stats=BASELINE, n_raw=len(pooled), n_independent=len(pooled),
)

# ---------------------------------------------------------------------------
# T3.1 -- SELECT_ASSET: when BTC and ETH both have an active M7 episode in the
# same calendar week, does prioritizing the larger-|disagreement| leg (an a
# priori "trade the more extreme mispricing" rule -- no threshold fit, so no
# train/test split needed for THIS rule itself) beat naively taking both
# (equal-weight both legs, i.e. what capital-unconstrained WITHOUT does)?
# ---------------------------------------------------------------------------
btc_w = btc_pnl.copy(); btc_w["week"] = btc_w["date"].dt.to_period("W")
eth_w = eth_pnl.copy(); eth_w["week"] = eth_w["date"].dt.to_period("W")
concurrent = btc_w.merge(eth_w, on="week", suffixes=("_btc", "_eth"))
if len(concurrent) > 0:
    concurrent["pick_btc"] = concurrent["disagreement_btc"].abs() >= concurrent["disagreement_eth"].abs()
    concurrent["selected_net_bps"] = np.where(concurrent["pick_btc"], concurrent["net_bps_btc"], concurrent["net_bps_eth"])
    concurrent["naive_avg_net_bps"] = (concurrent["net_bps_btc"] + concurrent["net_bps_eth"]) / 2.0
    sel_stats = summarize(concurrent["selected_net_bps"])
    naive_stats = summarize(concurrent["naive_avg_net_bps"])
    delta_sel = round(sel_stats["mean_bps"] - naive_stats["mean_bps"], 2) if sel_stats["mean_bps"] is not None and naive_stats["mean_bps"] is not None else None
else:
    sel_stats = naive_stats = summarize([])
    delta_sel = None
print(f"\n[T3.1] SELECT_ASSET (larger |disagreement| leg) on {len(concurrent)} concurrent BTC/ETH weeks:")
print(f"   WITHOUT(naive avg both legs) mean={naive_stats['mean_bps']} | WITH(select larger-|disagreement| leg) mean={sel_stats['mean_bps']}  delta={delta_sel}")
results["T3_1"] = dict(test_id="T3.1", desc="SELECT_ASSET: on weeks where both BTC and ETH M7 fire concurrently, pick the larger-|funding-basis-disagreement| leg (a priori rule, no fit) vs naive equal-weight-both",
                        n_concurrent_weeks=len(concurrent), without=naive_stats, with_=sel_stats, delta_net_bps=delta_sel)

# ---------------------------------------------------------------------------
# T3.2 -- REDUCE/INCREASE_RISK: size each M7 trade by its own |disagreement|
# z-score (within-symbol, causal -- uses only that symbol's OWN train-period
# distribution to standardize, no cross-symbol leakage) instead of flat 1x
# sizing. Continuous rule, no threshold fit -> evaluated on the FULL pooled
# episode set (already OOS relative to the RICH/CHEAP threshold fit above).
# ---------------------------------------------------------------------------
def zscore_vs_train(symbol_pnl, symbol):
    panel = load_panel(symbol)
    df = panel.dropna(subset=["funding_ann_pct", "basis_near_ann"]).copy()
    df["disagreement"] = df["funding_ann_pct"] - df["basis_near_ann"]
    train, _ = train_test_split(df)
    mu, sd = train["disagreement"].mean(), train["disagreement"].std()
    z = (symbol_pnl["disagreement"] - mu) / sd
    return z.abs()

btc_pnl2 = btc_pnl.copy(); btc_pnl2["abs_z"] = zscore_vs_train(btc_pnl, "BTCUSDT")
eth_pnl2 = eth_pnl.copy(); eth_pnl2["abs_z"] = zscore_vs_train(eth_pnl, "ETHUSDT")
pooled2 = pd.concat([btc_pnl2, eth_pnl2], ignore_index=True)
pooled2 = pooled2.dropna(subset=["abs_z"])
w = pooled2["abs_z"].values
w = w * (len(w) / w.sum())  # mean-1-normalized weights, same total "capital" as flat 1x
without_stats = summarize(pooled2["net_bps"])
with_mean = float((w * pooled2["net_bps"]).sum() / w.sum())
delta_size = round(with_mean - without_stats["mean_bps"], 2) if without_stats["mean_bps"] is not None else None
print(f"\n[T3.2] REDUCE/INCREASE_RISK: size M7 trades by |disagreement| z-score (mean-1-normalized): WITHOUT(flat)={without_stats['mean_bps']} WITH(z-sized)={round(with_mean,2)} delta={delta_size}")
results["T3_2"] = dict(test_id="T3.2", desc="REDUCE/INCREASE_RISK: size M7 trades by within-symbol train-period |disagreement| z-score instead of flat 1x, mean-1-normalized",
                        n=len(pooled2), without=without_stats, with_=dict(mean_bps=round(with_mean, 2)), delta_net_bps=delta_size)

# ---------------------------------------------------------------------------
# T3.3 -- ENABLE/DISABLE by BTC 20d realized-vol regime (reuse the causal
# daily_ohlcv.parquet built for Group 3 -- shared, read-only). Train/test
# split done on episode COUNT order (chronological), not by date-median,
# since N is thin (accepted DATA_LIMITED given ~roughly half the already-thin
# pooled sample per side).
# ---------------------------------------------------------------------------
daily = pd.read_parquet("/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w5/evidence/daily_ohlcv.parquet")
btc_daily = daily[daily["symbol"] == "BTCUSDT"].sort_values("day").reset_index(drop=True)
btc_daily["day"] = pd.to_datetime(btc_daily["day"]).dt.tz_localize(None).dt.normalize()  # calendar-date only, aligns with M7 panel's date grid
btc_daily["ret_1d"] = btc_daily["close"] / btc_daily["close"].shift(1) - 1
btc_daily["btc_rvol_20d"] = btc_daily["ret_1d"].rolling(20, min_periods=10).std().shift(1)
vol_lookup = btc_daily.set_index("day")["btc_rvol_20d"]

pooled3 = pooled.copy().sort_values("date").reset_index(drop=True)
pooled3["date_norm"] = pooled3["date"].dt.normalize()
pooled3["btc_rvol_20d"] = pooled3["date_norm"].map(vol_lookup)
pooled3 = pooled3.dropna(subset=["btc_rvol_20d"])
mid_i = len(pooled3) // 2
train3, test3 = pooled3.iloc[:mid_i], pooled3.iloc[mid_i:]
if len(train3) > 3 and len(test3) > 3:
    thresh3 = train3["btc_rvol_20d"].median()
    lo_mean = train3.loc[train3["btc_rvol_20d"] <= thresh3, "net_bps"].mean()
    hi_mean = train3.loc[train3["btc_rvol_20d"] > thresh3, "net_bps"].mean()
    direction3 = "<=" if lo_mean >= hi_mean else ">"
    gate_test = (test3["btc_rvol_20d"] <= thresh3) if direction3 == "<=" else (test3["btc_rvol_20d"] > thresh3)
    without3 = summarize(test3["net_bps"])
    with3 = summarize(test3.loc[gate_test, "net_bps"])
    delta3 = round(with3["mean_bps"] - without3["mean_bps"], 2) if with3["mean_bps"] is not None and without3["mean_bps"] is not None else None
    print(f"\n[T3.3] ENABLE/DISABLE M7 by BTC 20d realized-vol regime (train-picked direction: btc_rvol_20d {direction3} {round(thresh3,6)}), n_train={len(train3)} n_test={len(test3)}")
    print(f"   TEST(OOS) WITHOUT n={without3['n']} mean={without3['mean_bps']} | WITH n={with3['n']} mean={with3['mean_bps']}  delta={delta3}")
    results["T3_3"] = dict(test_id="T3.3", desc="ENABLE/DISABLE M7 (pooled BTC+ETH) by BTC 20d realized-vol regime, chronological-order train/test split (thin N, DATA_LIMITED)",
                            n_train=len(train3), n_test=len(test3), direction=direction3, threshold=round(float(thresh3), 6),
                            without=without3, with_=with3, delta_net_bps=delta3)
else:
    print("\n[T3.3] SKIPPED -- insufficient N for train/test split")
    results["T3_3"] = dict(test_id="T3.3", status="SKIPPED_INSUFFICIENT_N", n=len(pooled3))

with open(OUT, "w") as f:
    json.dump(results, f, indent=2, default=str)
print("\nWrote", OUT)
