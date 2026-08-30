"""
W7 -- single-venue book-tick mechanisms: A3-redo (queue depletion hazard,
binance/OKX post-crossed-book-fix), A4-redo (refill/sweep, maker+taker cost
framing), microprice, OFI, depth asymmetry (L1/L5/L25), spread transitions,
failed sweep, book recovery (resilience), liquidity vacuum.

Reads the compact price_ticks/deep_ticks/depletion parquets produced by
book_reconstruct.py for one venue across all available BTCUSDT dates, joins
them causally (as-of, never future-peeking), and emits summary rows only.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import glob
import os
import json
import sys

OUT_DIR = "./book_out"
DATES = ["2026-08-15", "2026-08-16", "2026-08-17", "2026-08-28"]
PERIOD = {"2026-08-15": "A_early", "2026-08-16": "A_early", "2026-08-17": "A_early", "2026-08-28": "B_late"}
VENUES = ["binance", "okx", "bybit", "hyperliquid"]

TAKER_BPS = 5.0
MAKER_BPS = 1.5


def load_venue(venue, symbol="BTCUSDT"):
    pts, dts, des = [], [], []
    for date in DATES:
        p = os.path.join(OUT_DIR, "price_ticks_%s_%s_%s.parquet" % (venue, symbol, date))
        d = os.path.join(OUT_DIR, "deep_ticks_%s_%s_%s.parquet" % (venue, symbol, date))
        e = os.path.join(OUT_DIR, "depletion_%s_%s_%s.parquet" % (venue, symbol, date))
        if not os.path.exists(p):
            continue
        dfp = pd.read_parquet(p)
        dfp["period"] = PERIOD[date]
        dfp["date"] = date
        pts.append(dfp)
        if os.path.exists(d):
            dfd = pd.read_parquet(d)
            dfd["period"] = PERIOD[date]
            dfd["date"] = date
            dts.append(dfd)
        if os.path.exists(e):
            dfe = pd.read_parquet(e)
            dfe["period"] = PERIOD[date]
            dfe["date"] = date
            des.append(dfe)
    price = pd.concat(pts, ignore_index=True) if pts else pd.DataFrame()
    deep = pd.concat(dts, ignore_index=True) if dts else pd.DataFrame()
    depl = pd.concat(des, ignore_index=True) if des else pd.DataFrame()
    return price, deep, depl


def fwd_return_asof(anchor_ts, grid_ts, grid_mid, horizon_ms):
    horizon_ns = int(horizon_ms * 1_000_000)
    idx0 = np.searchsorted(grid_ts, anchor_ts, side="right") - 1
    idx1 = np.searchsorted(grid_ts, anchor_ts + horizon_ns, side="right") - 1
    n = len(anchor_ts)
    ok = (idx0 >= 0) & (idx1 < len(grid_ts)) & (idx1 >= idx0) & (idx0 < len(grid_ts))
    ret = np.full(n, np.nan)
    p0 = grid_mid[idx0[ok]]
    p1 = grid_mid[idx1[ok]]
    ret[ok] = 1e4 * (p1 - p0) / p0
    return ret


def binary_split(is_high, outcome):
    """Robust fallback for heavily-zero-inflated signals (e.g. churn, where
    >90% of events see zero messages before depleting): split high(True)
    vs low(False) directly instead of quantile-binning, which degenerates
    when most mass sits at a single value."""
    ok = ~np.isnan(outcome)
    ih = np.asarray(is_high)[ok]
    o = outcome[ok]
    if ok.sum() < 60 or ih.sum() < 20 or (~ih).sum() < 20:
        return None
    lo = float(o[~ih].mean())
    hi = float(o[ih].mean())
    return dict(n=int(ok.sum()), n_high=int(ih.sum()), n_low=int((~ih).sum()),
                low=lo, high=hi, spread=hi - lo)


def decile_spread(signal, outcome, n_buckets=3):
    ok = ~np.isnan(signal) & ~np.isnan(outcome)
    s, o = signal[ok], outcome[ok]
    if len(s) < 60:
        return None
    try:
        q = pd.qcut(s, n_buckets, labels=False, duplicates="drop")
    except Exception:
        return None
    means = pd.Series(o).groupby(q).mean()
    if len(means) < 2:
        return None
    return dict(n=int(ok.sum()), low=float(means.iloc[0]), high=float(means.iloc[-1]),
                spread=float(means.iloc[-1] - means.iloc[0]))


def run_venue(venue, symbol="BTCUSDT"):
    price, deep, depl = load_venue(venue, symbol)
    rows = []
    if price.empty:
        return rows

    price = price.sort_values("ts_ns").reset_index(drop=True)
    price["mid"] = 0.5 * (price.bid_px + price.ask_px)
    price["spread_bps"] = 1e4 * (price.ask_px - price.bid_px) / price["mid"]
    denom = (price.bid_qty.fillna(0) + price.ask_qty.fillna(0))
    price["microprice"] = np.where(denom > 0,
                                    (price.ask_px * price.bid_qty.fillna(0) + price.bid_px * price.ask_qty.fillna(0)) / denom.replace(0, np.nan),
                                    price["mid"])
    price["microprice_offset_bps"] = 1e4 * (price["microprice"] - price["mid"]) / price["mid"]
    price["qimb_l1"] = (price.bid_qty.fillna(0) - price.ask_qty.fillna(0)) / denom.replace(0, np.nan)

    HORIZONS = [250, 1000, 5000]

    for period, g in price.groupby("period"):
        g = g.sort_values("ts_ns").reset_index(drop=True)
        ts = g.ts_ns.values.astype(np.int64)
        mid = g["mid"].values
        n_days = g["date"].nunique()

        # --- microprice offset -> forward return ---
        for h in HORIZONS:
            fwd = fwd_return_asof(ts, ts, mid, h)
            r = decile_spread(g["microprice_offset_bps"].values, fwd)
            if r:
                rows.append(dict(mech="MICROPRICE_OFFSET", venue=venue, symbol=symbol, period=period,
                                  horizon_ms=h, n_days=n_days, **r))

        # --- OFI (top-of-book) -> forward return ---
        pb0 = g.bid_px.shift(1).values; qb0 = g.bid_qty.shift(1).fillna(0).values
        pa0 = g.ask_px.shift(1).values; qa0 = g.ask_qty.shift(1).fillna(0).values
        pb1 = g.bid_px.values; qb1 = g.bid_qty.fillna(0).values
        pa1 = g.ask_px.values; qa1 = g.ask_qty.fillna(0).values
        ofi = (np.where(pb1 >= pb0, qb1, 0) - np.where(pb1 <= pb0, qb0, 0)
               - np.where(pa1 <= pa0, qa1, 0) + np.where(pa1 >= pa0, qa0, 0))
        for h in HORIZONS:
            fwd = fwd_return_asof(ts, ts, mid, h)
            r = decile_spread(ofi, fwd)
            if r:
                rows.append(dict(mech="OFI_TOB", venue=venue, symbol=symbol, period=period,
                                  horizon_ms=h, n_days=n_days, **r))

        # --- depth (queue) imbalance L1 -> forward return ---
        for h in HORIZONS:
            fwd = fwd_return_asof(ts, ts, mid, h)
            r = decile_spread(g["qimb_l1"].values, fwd)
            if r:
                rows.append(dict(mech="DEPTH_IMBALANCE_L1", venue=venue, symbol=symbol, period=period,
                                  horizon_ms=h, n_days=n_days, **r))

        # --- spread transitions: trailing 1s change in spread_bps -> forward |return| (vol) ---
        spread = g["spread_bps"].values
        idx_1s_ago = np.searchsorted(ts, ts - 1_000_000_000, side="left")
        idx_1s_ago = np.clip(idx_1s_ago, 0, len(spread) - 1)
        spread_chg = spread - spread[idx_1s_ago]
        for h in HORIZONS:
            fwd = fwd_return_asof(ts, ts, mid, h)
            r = decile_spread(spread_chg, np.abs(fwd))
            if r:
                rows.append(dict(mech="SPREAD_TRANSITION_vol", venue=venue, symbol=symbol, period=period,
                                  horizon_ms=h, n_days=n_days, **r))
            r2 = decile_spread(spread_chg, fwd)
            if r2:
                rows.append(dict(mech="SPREAD_TRANSITION_dir", venue=venue, symbol=symbol, period=period,
                                  horizon_ms=h, n_days=n_days, **r2))

        # --- sweep + refill (A4-redo) and failed sweep ---
        bid_step_away = g.bid_px.values < g.bid_px.shift(1).values
        ask_step_away = g.ask_px.values > g.ask_px.shift(1).values
        for side, mask, old_qty_col in [("bid", bid_step_away, "bid_qty"), ("ask", ask_step_away, "ask_qty")]:
            sweep_idx = np.where(mask)[0]
            if len(sweep_idx) < 40:
                continue
            old_qty = g[old_qty_col].shift(1).values[sweep_idx]
            new_qty_now = g[old_qty_col].values[sweep_idx]
            # refill 500ms after the sweep tick (as-of on same series)
            sweep_ts = ts[sweep_idx]
            idx_500ms = np.searchsorted(ts, sweep_ts + 500_000_000, side="right") - 1
            idx_500ms = np.clip(idx_500ms, 0, len(g) - 1)
            refill_qty = g[old_qty_col].values[idx_500ms]
            refill_ratio = refill_qty / np.where(old_qty > 0, old_qty, np.nan)
            sweep_dir = 1.0 if side == "bid" else -1.0  # bid steps down = bearish continuation dir is down
            for h in [500, 2000]:
                fwd = fwd_return_asof(sweep_ts, ts, mid, h)
                fwd_signed_toward_sweep = fwd * (-sweep_dir)  # continuation = price keeps moving away (same dir as step)
                r = decile_spread(refill_ratio, fwd_signed_toward_sweep, n_buckets=3)
                if r:
                    rows.append(dict(mech="A4_REFILL_ASYMMETRY", venue=venue, symbol=symbol, period=period,
                                      side=side, horizon_ms=h, n_days=n_days, **r))
            # failed sweep: does price revert back to old best within 2s? (fraction + mean reversion bps)
            fwd2000 = fwd_return_asof(sweep_ts, ts, mid, 2000)
            reverted = (fwd2000 * (-sweep_dir)) < 0  # moved back toward old level
            ok = ~np.isnan(fwd2000)
            if ok.sum() > 60:
                rows.append(dict(mech="FAILED_SWEEP_RATE", venue=venue, symbol=symbol, period=period,
                                  side=side, horizon_ms=2000, n_days=n_days,
                                  n=int(ok.sum()), low=float(np.nan), high=float(np.nan),
                                  spread=float(np.nan), fail_rate=float(np.mean(reverted[ok]))))

    # --- A3-redo: deep-book depletion churn -> forward return via canonical price ---
    if not depl.empty:
        depl = depl.sort_values("ts_ns").reset_index(drop=True)
        price_sorted = price.sort_values("ts_ns").reset_index(drop=True)
        for period in depl["period"].unique():
            dg = depl[depl.period == period]
            pg = price_sorted[price_sorted.period == period]
            if pg.empty or len(dg) < 60:
                continue
            pts = pg.ts_ns.values.astype(np.int64)
            pmid = pg["mid"].values
            n_days = dg["date"].nunique()
            for side in ["bid", "ask"]:
                sg = dg[dg.side == side]
                if len(sg) < 60:
                    continue
                ev_ts = sg.ts_ns.values.astype(np.int64)
                churn = sg.churn.values.astype(np.float64)
                sweep_dir = 1.0 if side == "bid" else -1.0
                for h in [250, 1000, 5000]:
                    fwd = fwd_return_asof(ev_ts, pts, pmid, h)
                    fwd_hazard_dir = fwd * (-sweep_dir)  # "hazard" = price keeps moving away after depletion
                    r = decile_spread(churn, fwd_hazard_dir, n_buckets=3)
                    split_kind = "tercile"
                    if r is None:
                        # churn is heavily zero-inflated (>90% of depletion
                        # events see zero prior touches) on this venue/side --
                        # fall back to a robust churn>0 vs churn==0 split.
                        r = binary_split(churn > 0, fwd_hazard_dir)
                        split_kind = "binary_churn_gt0"
                    if r:
                        rows.append(dict(mech="A3_QUEUE_DEPLETION_HAZARD", venue=venue, symbol=symbol,
                                          period=period, side=side, horizon_ms=h, n_days=n_days,
                                          split_kind=split_kind,
                                          churn_p90=float(np.percentile(churn, 90)),
                                          churn_p99=float(np.percentile(churn, 99)), **r))

    # --- liquidity vacuum: low deep depth5 regime -> forward |return| ---
    if not deep.empty:
        deep = deep.sort_values("ts_ns").reset_index(drop=True)
        for period, g in deep.groupby("period"):
            g = g.sort_values("ts_ns").reset_index(drop=True)
            ts = g.ts_ns.values.astype(np.int64)
            deep_mid = 0.5 * (g.best_bid.values + g.best_ask.values)
            total_depth5 = g.bid_depth5.values + g.ask_depth5.values
            n_days = g["date"].nunique()
            for h in [1000, 5000]:
                fwd = fwd_return_asof(ts, ts, deep_mid, h)
                r = decile_spread(total_depth5, np.abs(fwd), n_buckets=5)
                if r:
                    rows.append(dict(mech="LIQUIDITY_VACUUM", venue=venue, symbol=symbol, period=period,
                                      horizon_ms=h, n_days=n_days, **r))
            # book recovery: after a >=50% depth5 drop vs trailing 5s max, time-to-90%-recovery,
            # and whether fast recovery predicts continuation vs reversal at 5s
            trailing_max = pd.Series(total_depth5).rolling(20, min_periods=5).max().values  # ~20 grid pts = 5s
            shock = total_depth5 < 0.5 * trailing_max
            shock_idx = np.where(shock)[0]
            if len(shock_idx) > 40:
                recovered_ms = np.full(len(shock_idx), np.nan)
                target = 0.9 * trailing_max[shock_idx]
                for k, si in enumerate(shock_idx):
                    look = total_depth5[si:si + 80]  # up to 20s ahead
                    hit = np.where(look >= target[k])[0]
                    if len(hit) > 0:
                        recovered_ms[k] = (ts[min(si + hit[0], len(ts) - 1)] - ts[si]) / 1e6
                ok = ~np.isnan(recovered_ms)
                if ok.sum() > 30:
                    fwd5 = fwd_return_asof(ts[shock_idx], ts, deep_mid, 5000)
                    r = decile_spread(recovered_ms, fwd5, n_buckets=3) or dict(n=int(ok.sum()), low=None, high=None, spread=None)
                    r.pop("n", None)
                    rows.append(dict(mech="BOOK_RECOVERY_SPEED", venue=venue, symbol=symbol, period=period,
                                      horizon_ms=5000, n_days=n_days,
                                      n=int(ok.sum()), median_recovery_ms=float(np.nanmedian(recovered_ms)),
                                      **r))

    return rows


if __name__ == "__main__":
    all_rows = []
    for venue in VENUES:
        try:
            rows = run_venue(venue)
        except Exception as e:
            import traceback
            traceback.print_exc()
            rows = []
        all_rows.extend(rows)
        sys.stderr.write("venue %s -> %d rows (total %d)\n" % (venue, len(rows), len(all_rows)))
    out_path = sys.argv[1] if len(sys.argv) > 1 else "./single_venue_results.json"
    with open(out_path, "w") as f:
        json.dump(all_rows, f, default=str)
    print("wrote", len(all_rows), "rows")
