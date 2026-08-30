"""
W7 -- cross-venue mechanisms: A1 (lead-lag redo), A6-directional (liquidity
shock propagation, redesigned to condition on the SIGN of the leader's
bid/ask depth shock separately), cross-venue book disagreement (dislocation
-> convergence).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import os
import json
import sys

OUT_DIR = "./book_out"
DATES = ["2026-08-15", "2026-08-16", "2026-08-17", "2026-08-28"]
PERIOD = {"2026-08-15": "A_early", "2026-08-16": "A_early", "2026-08-17": "A_early", "2026-08-28": "B_late"}
VENUES = ["binance", "okx", "bybit", "hyperliquid"]
GRID_MS = 250


def load_price(venue, symbol="BTCUSDT"):
    pts = []
    for date in DATES:
        p = os.path.join(OUT_DIR, "price_ticks_%s_%s_%s.parquet" % (venue, symbol, date))
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p)
        df["period"] = PERIOD[date]
        df["date"] = date
        pts.append(df)
    if not pts:
        return pd.DataFrame()
    df = pd.concat(pts, ignore_index=True).sort_values("ts_ns").reset_index(drop=True)
    df["mid"] = 0.5 * (df.bid_px + df.ask_px)
    return df


def load_deep(venue, symbol="BTCUSDT"):
    dts = []
    for date in DATES:
        d = os.path.join(OUT_DIR, "deep_ticks_%s_%s_%s.parquet" % (venue, symbol, date))
        if not os.path.exists(d):
            continue
        df = pd.read_parquet(d)
        df["period"] = PERIOD[date]
        df["date"] = date
        dts.append(df)
    if not dts:
        return pd.DataFrame()
    return pd.concat(dts, ignore_index=True).sort_values("ts_ns").reset_index(drop=True)


def resample_grid(df, ts_col, val_cols, t0, t1, grid_ms=GRID_MS):
    """Causal forward-fill resample onto a common ns grid [t0, t1)."""
    grid_ns = grid_ms * 1_000_000
    n_bins = int((t1 - t0) // grid_ns) + 1
    grid_ts = t0 + np.arange(n_bins) * grid_ns
    out = {}
    ts = df[ts_col].values.astype(np.int64)
    idx = np.searchsorted(ts, grid_ts, side="right") - 1
    valid = idx >= 0
    for c in val_cols:
        arr = np.full(n_bins, np.nan)
        v = df[c].values
        arr[valid] = v[idx[valid]]
        out[c] = arr
    return grid_ts, out


def decile_spread(signal, outcome, n_buckets=10):
    ok = ~np.isnan(signal) & ~np.isnan(outcome)
    s, o = signal[ok], outcome[ok]
    if len(s) < 200:
        return None
    try:
        q = pd.qcut(s, n_buckets, labels=False, duplicates="drop")
    except Exception:
        return None
    means = pd.Series(o).groupby(q).mean()
    if len(means) < 2:
        return None
    corr = float(np.corrcoef(s, o)[0, 1])
    return dict(n=int(ok.sum()), low=float(means.iloc[0]), high=float(means.iloc[-1]),
                spread=float(means.iloc[-1] - means.iloc[0]), corr=corr)


def run_a1_and_disagreement(symbol="BTCUSDT"):
    rows = []
    price_by_venue = {v: load_price(v, symbol) for v in VENUES}
    for period in ["A_early", "B_late"]:
        series = {}
        for v, df in price_by_venue.items():
            g = df[df.period == period]
            if len(g) < 200:
                continue
            series[v] = g
        if len(series) < 3:
            continue
        t0 = max(g.ts_ns.min() for g in series.values())
        t1 = min(g.ts_ns.max() for g in series.values())
        if t1 <= t0:
            continue
        grids = {}
        for v, g in series.items():
            gts, out = resample_grid(g, "ts_ns", ["mid"], t0, t1)
            grids[v] = out["mid"]
        common_ts = t0 + np.arange(len(next(iter(grids.values())))) * (GRID_MS * 1_000_000)
        mat = pd.DataFrame(grids)
        n_days = series[list(series.keys())[0]]["date"].nunique()

        # --- A1: leader trailing return vs LOO-consensus forward return ---
        for leader in mat.columns:
            others = [c for c in mat.columns if c != leader]
            loo = mat[others].mean(axis=1)
            leader_ret_trail = np.log(mat[leader]).diff(4)  # 4*250ms=1000ms trailing
            for h_steps, h_ms in [(1, 250), (4, 1000), (20, 5000)]:
                loo_fwd = np.log(loo.shift(-h_steps)) - np.log(loo)
                r = decile_spread(leader_ret_trail.values, (loo_fwd.values * 1e4), n_buckets=10)
                if r:
                    rows.append(dict(mech="A1_LEAD_LAG", venue=leader, symbol=symbol, period=period,
                                      horizon_ms=h_ms, n_days=n_days, **r))

        # --- cross-venue disagreement: |venue mid - consensus mid| dislocation -> convergence ---
        consensus = mat.mean(axis=1)
        for v in mat.columns:
            dislocation_bps = 1e4 * (mat[v] - consensus) / consensus
            for h_steps, h_ms in [(4, 1000), (20, 5000)]:
                fwd_conv = -(1e4 * (mat[v].shift(-h_steps) - consensus.shift(-h_steps)) / consensus.shift(-h_steps) - dislocation_bps)
                # fwd_conv = how much the dislocation shrinks (positive = converges)
                ok = ~dislocation_bps.isna() & ~fwd_conv.isna()
                if ok.sum() < 200:
                    continue
                absd = dislocation_bps.abs()
                r = decile_spread(absd.values, fwd_conv.values, n_buckets=10)
                if r:
                    rows.append(dict(mech="CROSS_VENUE_DISAGREEMENT", venue=v, symbol=symbol, period=period,
                                      horizon_ms=h_ms, n_days=n_days, **r))
    return rows


def run_a6_directional(symbol="BTCUSDT"):
    rows = []
    price_by_venue = {v: load_price(v, symbol) for v in VENUES}
    deep_by_venue = {v: load_deep(v, symbol) for v in VENUES}
    for period in ["A_early", "B_late"]:
        price_g = {}
        for v, df in price_by_venue.items():
            g = df[df.period == period]
            if len(g) >= 200:
                price_g[v] = g
        if len(price_g) < 3:
            continue
        t0 = max(g.ts_ns.min() for g in price_g.values())
        t1 = min(g.ts_ns.max() for g in price_g.values())
        if t1 <= t0:
            continue
        mid_grids = {}
        for v, g in price_g.items():
            gts, out = resample_grid(g, "ts_ns", ["mid"], t0, t1)
            mid_grids[v] = out["mid"]
        mid_mat = pd.DataFrame(mid_grids)
        n_days_price = price_g[list(price_g.keys())[0]]["date"].nunique()

        for leader in ["binance", "okx"]:
            if leader not in deep_by_venue or deep_by_venue[leader].empty:
                continue
            dg = deep_by_venue[leader]
            dg = dg[dg.period == period]
            if len(dg) < 200:
                continue
            gts, out = resample_grid(dg, "ts_ns", ["bid_depth5", "ask_depth5"], t0, t1)
            bid5 = pd.Series(out["bid_depth5"])
            ask5 = pd.Series(out["ask_depth5"])
            followers = [c for c in mid_mat.columns if c != leader]
            if not followers:
                continue
            loo = mid_mat[followers].mean(axis=1)
            n_days = dg["date"].nunique()

            # trailing 1s % change (4 grid steps), directional
            bid_chg = (bid5 - bid5.shift(4)) / bid5.shift(4).replace(0, np.nan)
            ask_chg = (ask5 - ask5.shift(4)) / ask5.shift(4).replace(0, np.nan)

            for h_steps, h_ms in [(4, 1000), (20, 5000)]:
                loo_fwd_bps = 1e4 * (np.log(loo.shift(-h_steps)) - np.log(loo))
                # bid depth DROP (negative bid_chg) -> hypothesis: downward price move
                bid_drop_signal = -bid_chg  # positive = bigger drop
                r = decile_spread(bid_drop_signal.values, loo_fwd_bps.values, n_buckets=10)
                if r:
                    # sign check: hypothesis says bid-depth-drop -> DOWN move,
                    # i.e. high bid-drop bucket should have LOWER (more negative) fwd return
                    rows.append(dict(mech="A6_DIRECTIONAL_BID_DROP", venue=leader, symbol=symbol, period=period,
                                      horizon_ms=h_ms, n_days=n_days, hypothesis="bid_drop->down_move", **r))
                # ask depth DROP -> hypothesis: upward price move
                ask_drop_signal = -ask_chg
                r = decile_spread(ask_drop_signal.values, loo_fwd_bps.values, n_buckets=10)
                if r:
                    rows.append(dict(mech="A6_DIRECTIONAL_ASK_DROP", venue=leader, symbol=symbol, period=period,
                                      horizon_ms=h_ms, n_days=n_days, hypothesis="ask_drop->up_move", **r))
    return rows


if __name__ == "__main__":
    rows = run_a1_and_disagreement()
    rows += run_a6_directional()
    out_path = sys.argv[1] if len(sys.argv) > 1 else "./cross_venue_results.json"
    with open(out_path, "w") as f:
        json.dump(rows, f, default=str)
    print("wrote", len(rows), "rows")
