"""W5/s08 - VECTORISED queue-aware post-only simulator, v2 (final).

Supersedes s07: adds (a) a parametric queue-position haircut kappa so the optimism of the
simulator itself is bracketed rather than assumed, (b) an arrival-referenced quote grid long
enough to price the "post for T seconds then cross" policy AND mark it H seconds after the
crossing, (c) causal trailing shock/vol features so the urgency (H4) test can be run on REAL
BBO instead of only on the probe, (d) top-of-book notional for the capacity test (H3).

FILL RULES computed on the SAME attempts (this is the calibration device):
  trav   : the project's probe rule -- best ask < our bid limit (the book TRAVERSED our level).
           Reproduced verbatim from src/institutional/execution/maker_fill_probe.py::check_fill.
           Traversal is a SUFFICIENT condition for a real fill, so this is a LOWER bound on
           fill probability and, because it admits only fills where the book walked through us,
           it is a mechanically PESSIMISTIC estimator of markout (see s02/H1).
  k<K>   : queue-ahead = K * displayed size at our price at placement, consumed only by
           aggressive trades at or through our price; plus "alone at the touch" (the book's
           best bid fell strictly below our limit => the queue ahead of us is gone by
           definition => the next same-side aggression fills us).
             K=0.0 (k00)  we are FIRST in queue          (upper bound on maker economics)
             K=0.5  half the displayed size ahead   (mid case)
             K=1.0  we are LAST at the touch        (conservative baseline == 'real')
             K=2.0  twice the displayed size ahead  (haircut for hidden liquidity / latency /
                    orders that join ahead of us; the honest pessimistic case)

MARKOUTS are measured from the FILL PRICE at the FILL TIME. Benign fills (the price touches
our level and bounces) are therefore admitted -- the probe structurally cannot record those.

Usage:  W5_SCRATCH=... python s08_qsim_v2.py <venue> <symbol> <date>
"""
import os, sys, json
import numpy as np, pandas as pd

NS       = 10**9
TTL_S    = 600
PLACE_S  = 30
GRID_S   = [1, 5, 10, 30, 60, 120, 300, 600, 900]   # quotes at t0 + u  (policy / chase cost)
MKO_S    = [1, 10, 60, 300]                          # mid at fill + u  (adverse selection)
KAPPAS   = [("k00", 0.0), ("k05", 0.5), ("k10", 1.0), ("k20", 2.0)]
TAIL_S   = 1500                                      # need quotes out to t0+TTL+300


def load(S, v, s, d):
    b = pd.read_parquet(f"{S}/micro/bbo_{v}_{s}_{d}.parquet")
    t = pd.read_parquet(f"{S}/micro/trd_{v}_{s}_{d}.parquet")
    b = b[np.isfinite(b.bid) & np.isfinite(b.ask) & (b.ask > b.bid)].reset_index(drop=True)
    return b, t


def causal_shock(bts, mid):
    """Trailing |5-min return| in bps and trailing 10-min realised vol, on the 100ms bbo grid.
    Strictly backward looking: value at index i uses only quotes at or before bts[i]."""
    i5   = np.searchsorted(bts, bts - 300 * NS, "right") - 1
    i10  = np.searchsorted(bts, bts - 600 * NS, "right") - 1
    i5   = np.clip(i5, 0, None); i10 = np.clip(i10, 0, None)
    ret5 = (mid / mid[i5] - 1.0) * 1e4
    # realised vol proxy: mean abs 1s log-change over the trailing 10 min, cheap + causal
    i1   = np.clip(np.searchsorted(bts, bts - 1 * NS, "right") - 1, 0, None)
    r1   = np.abs(mid / mid[i1] - 1.0) * 1e4
    csum = np.concatenate([[0.0], np.cumsum(r1)])
    rv10 = (csum[np.arange(len(bts)) + 1] - csum[i10]) / np.maximum(np.arange(len(bts)) - i10, 1)
    return ret5, rv10


def run(S, v, s, d):
    b, t = load(S, v, s, d)
    bts = b.ts.values.astype(np.int64)
    bid = b.bid.values.astype(np.float64);  ask = b.ask.values.astype(np.float64)
    bq  = b.bq.values.astype(np.float64);   aq  = b.aq.values.astype(np.float64)
    blo = b.bid_lo.values.astype(np.float64); ahi = b.ask_hi.values.astype(np.float64)
    mid = (bid + ask) / 2.0
    ret5, rv10 = causal_shock(bts, mid)

    tts  = t.ts.values.astype(np.int64)
    tpx  = t.price.values.astype(np.float64)
    tqty = t.qty.values.astype(np.float64)
    tbuy = (t.side.values == "buy")
    isell = np.flatnonzero(~tbuy); ibuy = np.flatnonzero(tbuy)
    sell_ts, sell_px, sell_q = tts[isell], tpx[isell], tqty[isell]
    buy_ts,  buy_px,  buy_q  = tts[ibuy],  tpx[ibuy],  tqty[ibuy]

    t0s = np.arange(bts[0], bts[-1] - TAIL_S * NS, PLACE_S * NS)
    gi  = np.searchsorted(bts, t0s, "right") - 1
    gi  = gi[(gi >= 0) & (gi < len(bts))]
    # STALENESS GUARD: the microstructure collector has real outages (binance/okx/HL all lost
    # 15h on 2026-09-04). Without this guard a gap silently yields frozen quotes, which read as
    # "no fill, no traversal, no adverse selection" and would bias every statistic downward.
    # An attempt is admitted only if [t0, t0+TTL+300s] contains no quote gap longer than 30s.
    cg  = np.concatenate([[0], np.cumsum(np.diff(bts) > 30 * NS)])
    j0a = gi
    j1a = np.minimum(np.searchsorted(bts, bts[gi] + TAIL_S * NS, "right"), len(bts) - 1)
    gi  = gi[(cg[j1a] - cg[j0a]) == 0]

    rows = []
    for side in ("BUY", "SELL"):
        sgn = 1.0 if side == "BUY" else -1.0
        a_ts, a_px, a_q = (sell_ts, sell_px, sell_q) if side == "BUY" else (buy_ts, buy_px, buy_q)
        for i0 in gi:
            t0 = bts[i0]
            L  = bid[i0] if side == "BUY" else ask[i0]
            Q  = bq[i0] if side == "BUY" else aq[i0]
            if not np.isfinite(L) or not np.isfinite(Q) or Q <= 0:
                continue
            m0 = mid[i0]; s0 = (ask[i0] - bid[i0]) / m0 * 1e4
            tend = t0 + TTL_S * NS

            lo = np.searchsorted(a_ts, t0, "right"); hi = np.searchsorted(a_ts, tend, "right")
            px, qy, tz = a_px[lo:hi], a_q[lo:hi], a_ts[lo:hi]
            m = (px <= L) if side == "BUY" else (px >= L)
            cs = np.cumsum(qy[m]) if m.any() else np.zeros(0)
            tzm = tz[m] if m.any() else np.zeros(0, dtype=np.int64)

            # "alone at the touch": our price level no longer exists in the visible book
            j0, j1 = np.searchsorted(bts, t0, "right"), np.searchsorted(bts, tend, "right")
            gone = (blo[j0:j1] < L) if side == "BUY" else (ahi[j0:j1] > L)
            ig = np.flatnonzero(gone)
            f_alone = -1
            if ig.size:
                t_alone = bts[j0 + ig[0]]
                k = np.searchsorted(tz, t_alone, "left")
                if k < tz.size:
                    f_alone = tz[k]

            r = dict(side=side, t0=t0, m0=m0, s0_bps=s0, q0=Q, notional0=Q * L, lim=L,
                     shock5_bps=ret5[i0] * sgn, absshock5_bps=abs(ret5[i0]), rv10_bps=rv10[i0])

            # probe's traversal rule
            travc = (ask[j0:j1] < L) if side == "BUY" else (bid[j0:j1] > L)
            it = np.flatnonzero(travc)
            r["f_trav"] = int(bts[j0 + it[0]]) if it.size else -1

            for tag, K in KAPPAS:
                need = K * Q
                f_q = -1
                if cs.size:
                    k = np.searchsorted(cs, need, "left")
                    if k < cs.size:
                        f_q = tzm[k]
                cand = [x for x in (f_q, f_alone) if x > 0]
                r[f"f_{tag}"] = int(min(cand)) if cand else -1

            for u in GRID_S:                       # arrival-referenced quotes
                k = np.searchsorted(bts, t0 + u * NS, "right") - 1
                k = min(max(k, 0), len(bts) - 1)
                r[f"ask_{u}"] = ask[k]; r[f"bid_{u}"] = bid[k]; r[f"mid_{u}"] = mid[k]

            for nm in ["trav"] + [tg for tg, _ in KAPPAS]:     # markouts from the fill
                ft = r[f"f_{nm}"]
                for u in MKO_S:
                    if ft > 0:
                        k2 = np.searchsorted(bts, ft + u * NS, "right") - 1
                        k2 = min(max(k2, 0), len(bts) - 1)
                        r[f"mko_{nm}_{u}"] = sgn * (mid[k2] / L - 1.0) * 1e4
                    else:
                        r[f"mko_{nm}_{u}"] = np.nan
            rows.append(r)

    df = pd.DataFrame(rows)
    for nm in ["trav"] + [tg for tg, _ in KAPPAS]:
        df[f"fill_{nm}"] = df[f"f_{nm}"] > 0
        df[f"ttf_{nm}"]  = np.where(df[f"f_{nm}"] > 0, (df[f"f_{nm}"] - df.t0) / 1e9, np.nan)
    df["venue"], df["symbol"], df["date"] = v, s, d
    return df


if __name__ == "__main__":
    S = os.environ["W5_SCRATCH"]
    v, s, d = sys.argv[1], sys.argv[2], sys.argv[3]
    out = f"{S}/qsim2_{v}_{s}_{d}.parquet"
    if os.path.exists(out) and os.environ.get("W5_FORCE") != "1":
        print("skip", out); sys.exit(0)
    df = run(S, v, s, d)
    df.to_parquet(out, index=False, compression="zstd")
    print(f"{v} {s} {d} n={len(df)} spr={df.s0_bps.mean():.3f}bps "
          f"| fill trav={df.fill_trav.mean():.3f} k00={df.fill_k00.mean():.3f} "
          f"k10={df.fill_k10.mean():.3f} k20={df.fill_k20.mean():.3f} "
          f"| mko60 trav={df.mko_trav_60.mean():.3f} k10={df.mko_k10_60.mean():.3f} "
          f"k20={df.mko_k20_60.mean():.3f} -> {out}")
