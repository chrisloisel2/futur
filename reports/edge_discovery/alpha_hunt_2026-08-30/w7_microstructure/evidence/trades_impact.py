"""
W7 -- price-impact asymmetry and post-impact reversal, plus venue-toxicity
comparison. Reuses load_trades/build_grid_price/forward_return_at_grid from
trades_flow.py. One (venue,symbol,date) file processed at a time; only
summary rows retained.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import sys
import json

sys.path.insert(0, ".")
from trades_flow import load_trades, build_grid_price, forward_return_at_grid, VENUES, SYMBOLS, DATES, PERIOD

HORIZONS_MS = [250, 1000, 5000, 30000]
LARGE_PCTL = 99.0  # top 1% by notional = "large" trade


def process_one(venue, symbol, date):
    df = load_trades(venue, symbol, date)
    if df is None or len(df) < 1000:
        return []
    ts = df["event_ts_ns"].values.astype(np.int64)
    price = df["price"].values.astype(np.float64)
    qty = df["qty"].values.astype(np.float64)
    aggressor = df["aggressor"].values
    sign = np.where(aggressor == "buy", 1.0, -1.0)
    notional = price * qty

    grid_ts, grid_price = build_grid_price(ts, price)

    thresh = np.percentile(notional, LARGE_PCTL)
    large_mask = notional >= thresh
    if large_mask.sum() < 30:
        return []

    large_ts = ts[large_mask]
    large_sign = sign[large_mask]
    period = PERIOD[date]

    rows = []
    signed_by_h = {}
    for h in HORIZONS_MS:
        fwd = forward_return_at_grid(large_ts, grid_ts, grid_price, h)
        signed = fwd * large_sign  # return in the direction of the large trade
        ok = ~np.isnan(signed)
        signed_by_h[h] = signed
        if ok.sum() < 30:
            continue
        buy_ok = ok & (large_sign > 0)
        sell_ok = ok & (large_sign < 0)
        rows.append(dict(
            mech="PRICE_IMPACT_ASYMMETRY", venue=venue, symbol=symbol, period=period, horizon_ms=h,
            n=int(ok.sum()),
            buy_impact_bps=float(np.nanmean(fwd[buy_ok])) if buy_ok.sum() > 5 else None,
            sell_impact_bps=float(np.nanmean(fwd[sell_ok])) if sell_ok.sum() > 5 else None,
            n_buy=int(buy_ok.sum()), n_sell=int(sell_ok.sum()),
            signed_mean_bps=float(np.nanmean(signed[ok])),
        ))

    # post-impact reversal: later-horizon signed return minus immediate (250ms) signed return
    if 250 in signed_by_h and 5000 in signed_by_h:
        imm = signed_by_h[250]
        late = signed_by_h[5000]
        ok = ~np.isnan(imm) & ~np.isnan(late)
        if ok.sum() >= 30:
            rows.append(dict(
                mech="POST_IMPACT_REVERSAL", venue=venue, symbol=symbol, period=period, horizon_ms=5000,
                n=int(ok.sum()),
                immediate_signed_bps=float(np.nanmean(imm[ok])),
                later_signed_bps=float(np.nanmean(late[ok])),
                giveback_bps=float(np.nanmean(imm[ok]) - np.nanmean(late[ok])),
            ))
    if 250 in signed_by_h and 30000 in signed_by_h:
        imm = signed_by_h[250]
        late = signed_by_h[30000]
        ok = ~np.isnan(imm) & ~np.isnan(late)
        if ok.sum() >= 30:
            rows.append(dict(
                mech="POST_IMPACT_REVERSAL", venue=venue, symbol=symbol, period=period, horizon_ms=30000,
                n=int(ok.sum()),
                immediate_signed_bps=float(np.nanmean(imm[ok])),
                later_signed_bps=float(np.nanmean(late[ok])),
                giveback_bps=float(np.nanmean(imm[ok]) - np.nanmean(late[ok])),
            ))

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
    out_path = sys.argv[1] if len(sys.argv) > 1 else "./trades_impact_results.json"
    with open(out_path, "w") as f:
        json.dump(all_rows, f)
    print("wrote", len(all_rows), "rows to", out_path)
