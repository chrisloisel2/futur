"""
W7 -- trade-print-based microstructure mechanisms (A5 redo, aggressive-flow
burst, acceleration of flow, trade intensity, price-impact asymmetry,
post-impact reversal, venue-specific toxic flow).

Processes ONE (venue, symbol, date) trades file at a time via duckdb
(out-of-core JSONL read), converts to small numpy arrays, computes trailing
signed/absolute notional features with a two-pointer window, resamples a
250ms last-trade price grid for forward-return lookups, and appends only
small summary rows (decile bucket means, n, corr) to a results list. No
unbounded accumulation: each file's raw trade array is discarded once its
summary rows are computed.
"""
from __future__ import annotations
import duckdb
import numpy as np
import pandas as pd
import os
import sys
import json

ROOT = "/home/qbee/futur-data-v2"
VENUES = ["binance", "bybit", "okx", "hyperliquid"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DATES = ["2026-08-15", "2026-08-16", "2026-08-17", "2026-08-28", "2026-08-29"]
PERIOD = {
    "2026-08-15": "A_early", "2026-08-16": "A_early", "2026-08-17": "A_early",
    "2026-08-28": "B_late", "2026-08-29": "B_late",
}
GRID_MS = 250
HORIZONS_MS = [250, 1000, 5000, 30000]
TRAIL_MS = 1000


def load_trades(venue, symbol, date):
    path = os.path.join(ROOT, "data/market_physics_v3/raw/trades",
                         "venue=%s" % venue, "symbol=%s" % symbol, "date=%s" % date, "events.jsonl")
    if not os.path.exists(path):
        return None
    try:
        df = duckdb.query(
            "SELECT event_ts_ns, price, qty, aggressor FROM read_ndjson_auto('%s', ignore_errors=true)" % path
        ).df()
    except Exception as e:
        return None
    if df.empty:
        return None
    df = df.sort_values("event_ts_ns").reset_index(drop=True)
    return df


def build_grid_price(ts_ns, price, grid_ms=GRID_MS):
    """250ms forward-filled last-trade price grid, no leakage (uses only
    trades at/before each grid point)."""
    t0 = ts_ns[0]
    t1 = ts_ns[-1]
    grid_ns = grid_ms * 1_000_000
    n_bins = int((t1 - t0) // grid_ns) + 1
    bin_idx = ((ts_ns - t0) // grid_ns).astype(np.int64)
    last_price_per_bin = np.full(n_bins, np.nan)
    # last trade price observed within each bin
    np.maximum.at  # noop just to ensure numpy imported style consistent
    for b, p in zip(bin_idx, price):
        last_price_per_bin[b] = p
    # forward fill
    s = pd.Series(last_price_per_bin).ffill()
    grid_ts = t0 + np.arange(n_bins) * grid_ns
    return grid_ts, s.values


def trailing_window_sum(ts_ns, values, window_ms):
    """Two-pointer trailing sum of `values` over the preceding window_ms,
    evaluated AT each trade's own timestamp (causal: window is [t-window, t])."""
    window_ns = window_ms * 1_000_000
    n = len(ts_ns)
    out = np.empty(n)
    cs = np.concatenate(([0.0], np.cumsum(values)))
    j = 0
    for i in range(n):
        lo_ts = ts_ns[i] - window_ns
        while ts_ns[j] < lo_ts:
            j += 1
        out[i] = cs[i + 1] - cs[j]
    return out


def forward_return_at_grid(anchor_ts, grid_ts, grid_price, horizon_ms):
    """For each anchor timestamp, forward return from price-at-anchor to
    price at anchor+horizon, both looked up on the causal grid."""
    horizon_ns = horizon_ms * 1_000_000
    idx0 = np.searchsorted(grid_ts, anchor_ts, side="right") - 1
    idx1 = np.searchsorted(grid_ts, anchor_ts + horizon_ns, side="right") - 1
    valid = (idx0 >= 0) & (idx1 < len(grid_ts)) & (idx1 >= idx0)
    ret = np.full(len(anchor_ts), np.nan)
    p0 = grid_price[idx0[valid]]
    p1 = grid_price[idx1[valid]]
    ret[valid] = 1e4 * (p1 - p0) / p0
    return ret


def decile_spread(signal, fwd_ret, n_buckets=10):
    ok = ~np.isnan(signal) & ~np.isnan(fwd_ret)
    s = signal[ok]
    r = fwd_ret[ok]
    if len(s) < 200:
        return None
    q = pd.qcut(s, n_buckets, labels=False, duplicates="drop")
    means = pd.Series(r).groupby(q).mean()
    if len(means) < 2:
        return None
    top = means.iloc[-1]
    bot = means.iloc[0]
    corr = np.corrcoef(s, r)[0, 1]
    return dict(n=int(ok.sum()), top=float(top), bot=float(bot), spread=float(top - bot), corr=float(corr))


def process_one(venue, symbol, date):
    df = load_trades(venue, symbol, date)
    if df is None or len(df) < 500:
        return []
    ts = df["event_ts_ns"].values.astype(np.int64)
    price = df["price"].values.astype(np.float64)
    qty = df["qty"].values.astype(np.float64)
    aggressor = df["aggressor"].values
    sign = np.where(aggressor == "buy", 1.0, -1.0)
    notional = price * qty
    signed_notional = sign * notional

    grid_ts, grid_price = build_grid_price(ts, price)

    trail_signed = trailing_window_sum(ts, signed_notional, TRAIL_MS)
    trail_gross = trailing_window_sum(ts, notional, TRAIL_MS)
    trail_count = trailing_window_sum(ts, np.ones_like(notional), TRAIL_MS)

    # acceleration: trailing intensity now vs trailing intensity as of
    # ~TRAIL_MS ago (nearest earlier trade's own trailing_count value).
    idx_prior = np.searchsorted(ts, ts - TRAIL_MS * 1_000_000, side="left")
    idx_prior = np.clip(idx_prior, 0, len(trail_count) - 1)
    accel = trail_count - trail_count[idx_prior]

    rows = []
    period = PERIOD[date]
    for h in HORIZONS_MS:
        fwd = forward_return_at_grid(ts, grid_ts, grid_price, h)

        # A5-redo: toxic flow / absorption (signed notional)
        r = decile_spread(trail_signed, fwd)
        if r:
            rows.append(dict(mech="A5_toxic_flow", venue=venue, symbol=symbol, period=period,
                              horizon_ms=h, **r))

        # aggressive-flow burst: |trailing signed notional| (magnitude, not sign) vs |fwd return| (vol proxy)
        r = decile_spread(np.abs(trail_signed), np.abs(fwd))
        if r:
            rows.append(dict(mech="AGGR_FLOW_BURST_vol", venue=venue, symbol=symbol, period=period,
                              horizon_ms=h, **r))

        # trade intensity regime: trailing trade count vs |fwd return|
        r = decile_spread(trail_count, np.abs(fwd))
        if r:
            rows.append(dict(mech="TRADE_INTENSITY_vol", venue=venue, symbol=symbol, period=period,
                              horizon_ms=h, **r))

        # acceleration of flow: change in trailing count vs signed fwd return
        # (tercile, not decile -- accel is low-cardinality/integer-ish)
        r = decile_spread(accel, fwd, n_buckets=3)
        if r:
            rows.append(dict(mech="FLOW_ACCEL", venue=venue, symbol=symbol, period=period,
                              horizon_ms=h, **r))

    return rows


if __name__ == "__main__":
    all_rows = []
    for venue in VENUES:
        for symbol in SYMBOLS:
            for date in DATES:
                try:
                    rows = process_one(venue, symbol, date)
                except Exception as e:
                    sys.stderr.write("FAIL %s %s %s: %s\n" % (venue, symbol, date, e))
                    rows = []
                all_rows.extend(rows)
                sys.stderr.write("done %s %s %s (%d rows so far)\n" % (venue, symbol, date, len(all_rows)))
    out_path = sys.argv[1] if len(sys.argv) > 1 else "./trades_flow_results.json"
    with open(out_path, "w") as f:
        json.dump(all_rows, f)
    print("wrote", len(all_rows), "rows to", out_path)
