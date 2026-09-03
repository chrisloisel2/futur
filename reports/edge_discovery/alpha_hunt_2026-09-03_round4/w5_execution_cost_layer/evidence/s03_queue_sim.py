"""W5/s03 - queue-aware post-only simulator on REAL BBO + signed trades.

Fixes the structural defect of data/execution_probe (fill == price traversal, which excludes
every benign fill and forces a mechanically negative markout - see s02/H1).

Model (conservative by default):
  * place a post-only BUY at best bid / SELL at best ask every PLACE_INTERVAL seconds
  * queue_ahead := top-of-book qty at our price at placement time
  * an aggressive trade at price <= our bid (>= our ask) consumes queue_ahead; when it is
    exhausted we are filled at our limit
  * cancellations are assumed to occur BEHIND us (conservative: we never advance for free).
    The 'optimistic' variant additionally advances the queue pro-rata when the displayed size
    at our level shrinks without a trade.
  * TTL seconds, no re-quote.

Outputs one row per attempt with fill outcome and the quotes at every horizon, so that the
decision-relevant policy comparison can be made downstream:
      cost_immediate_taker      = s0/2 + taker_fee
      cost_post_then_cross(T)   = P(fill<=T)*(-s0/2 + maker_fee)
                                + (1-P)*[ (ask_T - m0)/m0*1e4 + taker_fee ]
"""
import gzip, json, os, sys, glob, math
import numpy as np, pandas as pd

PLACE_INTERVAL_NS = 30 * 10**9
TTL_NS            = 600 * 10**9
HORIZONS_S        = [1, 5, 10, 30, 60, 300, 600]
HORIZONS_NS       = [h * 10**9 for h in HORIZONS_S]

def stream(venue, symbol, kind, dates):
    for d in dates:
        pat = f"data/microstructure_reduced/raw/{kind}/venue={venue}/symbol={symbol}/date={d}/*.jsonl.gz"
        for f in sorted(glob.glob(pat)):
            with gzip.open(f, "rt") as fh:
                for line in fh:
                    try: yield json.loads(line)
                    except Exception: continue

def merged(venue, symbol, dates):
    """Merge bbo and trade streams on event_ts_ns (both are per-hour-file sorted)."""
    b = stream(venue, symbol, "bbo", dates)
    t = stream(venue, symbol, "trades", dates)
    nb = next(b, None); nt = next(t, None)
    while nb is not None or nt is not None:
        if nt is None or (nb is not None and nb["event_ts_ns"] <= nt["event_ts_ns"]):
            yield ("B", nb); nb = next(b, None)
        else:
            yield ("T", nt); nt = next(t, None)

def run(venue, symbol, dates, optimistic=False):
    bid = ask = bq = aq = None
    last_place = 0
    active = []          # dicts
    done   = []
    for kind, ev in merged(venue, symbol, dates):
        ts = ev["event_ts_ns"]
        if kind == "B":
            nbid, nask = ev["bid_price"], ev["ask_price"]
            nbq, naq   = ev.get("bid_qty"), ev.get("ask_qty")
            if not nbid or not nask or nask <= nbid:
                continue
            # optimistic queue advance on displayed-size shrink without trade
            if optimistic and bid is not None:
                for o in active:
                    if o["fill_ts"] is None:
                        if o["side"] == "BUY" and nbid == o["lim"] and bid == o["lim"] and nbq is not None and bq is not None and nbq < bq:
                            o["queue"] = min(o["queue"], nbq)
                        if o["side"] == "SELL" and nask == o["lim"] and ask == o["lim"] and naq is not None and aq is not None and naq < aq:
                            o["queue"] = min(o["queue"], naq)
            bid, ask, bq, aq = nbid, nask, nbq, naq
            mid = (bid + ask) / 2.0
            # if best bid falls strictly below our resting BUY, our order would BE the best bid
            for o in active:
                if o["fill_ts"] is None:
                    if o["side"] == "BUY" and bid < o["lim"]:  o["queue"] = 0.0
                    if o["side"] == "SELL" and ask > o["lim"]: o["queue"] = 0.0
            _marks(active, done, ts, bid, ask, mid)
            if ts - last_place >= PLACE_INTERVAL_NS:
                last_place = ts
                for side, lim, q in (("BUY", bid, bq or 0.0), ("SELL", ask, aq or 0.0)):
                    active.append(dict(side=side, lim=lim, t0=ts, m0=mid,
                                       s0_bps=(ask - bid) / mid * 1e4, queue=float(q),
                                       q0=float(q), fill_ts=None, ttf=None,
                                       qh={}, mk={}, vol=0.0))
        else:  # trade
            px, qty, tside = ev["price"], ev["qty"], ev.get("side")
            for o in active:
                if o["fill_ts"] is not None: continue
                if o["side"] == "BUY" and tside == "sell" and px <= o["lim"]:
                    o["queue"] -= qty
                    if o["queue"] <= 0: o["fill_ts"] = ts; o["ttf"] = (ts - o["t0"]) / 1e9
                elif o["side"] == "SELL" and tside == "buy" and px >= o["lim"]:
                    o["queue"] -= qty
                    if o["queue"] <= 0: o["fill_ts"] = ts; o["ttf"] = (ts - o["t0"]) / 1e9
            if bid is not None:
                _marks(active, done, ts, bid, ask, (bid + ask) / 2.0)
    return pd.DataFrame(_flush(active, done))

def _marks(active, done, ts, bid, ask, mid):
    still = []
    for o in active:
        for h, hns in zip(HORIZONS_S, HORIZONS_NS):
            if h not in o["qh"] and ts - o["t0"] >= hns:
                o["qh"][h] = (bid, ask, mid)              # quotes at t0+h  (policy cost)
            if o["fill_ts"] is not None and h not in o["mk"] and ts - o["fill_ts"] >= hns:
                o["mk"][h] = mid                          # mid at fill+h   (markout)
        expired = ts - o["t0"] > TTL_NS
        if expired and len(o["qh"]) == len(HORIZONS_S) and (o["fill_ts"] is None or len(o["mk"]) == len(HORIZONS_S)):
            done.append(o)
        elif expired and ts - o["t0"] > TTL_NS + 600 * 10**9:
            done.append(o)
        else:
            still.append(o)
    active[:] = still

def _flush(active, done):
    rows = []
    for o in done:
        r = dict(side=o["side"], lim=o["lim"], t0=o["t0"], m0=o["m0"], s0_bps=o["s0_bps"],
                 q0=o["q0"], filled=o["fill_ts"] is not None, ttf=o["ttf"])
        sign = 1 if o["side"] == "BUY" else -1
        for h in HORIZONS_S:
            q = o["qh"].get(h)
            r[f"bid_{h}"] = q[0] if q else np.nan
            r[f"ask_{h}"] = q[1] if q else np.nan
            r[f"mid_{h}"] = q[2] if q else np.nan
            m = o["mk"].get(h)
            r[f"mko_{h}"] = (sign * (m / o["lim"] - 1) * 1e4) if m else np.nan
        rows.append(r)
    return rows

if __name__ == "__main__":
    venue, symbol = sys.argv[1], sys.argv[2]
    dates = sys.argv[3].split(",")
    opt = len(sys.argv) > 4 and sys.argv[4] == "opt"
    S = os.environ["W5_SCRATCH"]
    df = run(venue, symbol, dates, optimistic=opt)
    tag = "opt" if opt else "cons"
    out = f"{S}/qsim_{venue}_{symbol}_{tag}.parquet"
    df.to_parquet(out, index=False)
    print(f"{venue} {symbol} {tag}: n={len(df)} fill_rate={df.filled.mean():.3f} "
          f"med_ttf={df.ttf.median():.1f}s spread={df.s0_bps.mean():.3f}bps -> {out}")
