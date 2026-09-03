"""W5/s07 - VECTORISED queue-aware post-only simulator on real BBO + signed trades.

Three fill definitions computed on the SAME attempts, so the project's probe can be
calibrated against a real fill model:
  fill_cons  : queue-ahead = displayed size at our price at placement, consumed ONLY by
               aggressive trades at/through our price. Cancellations assumed to sit BEHIND us
               (we never advance for free). Hard lower bound on fill probability.
  fill_real  : fill_cons, OR the best bid has dropped strictly below our limit (our order would
               then BE the best bid, so queue-ahead is 0 by definition) and a subsequent
               aggressive trade arrives. This is the realistic baseline.
  fill_trav  : the probe's rule (ask < limit for a BUY) - price traversal. Reproduced here only
               to measure the bias of data/execution_probe against fill_real.

Markouts are measured from the FILL PRICE at the FILL TIME, so benign fills (price bounces back)
are admitted - the probe structurally cannot record those.
"""
import os, sys, numpy as np, pandas as pd

TTL_S = 600
PLACE_S = 30
H = [1, 5, 10, 30, 60, 300]

def load(S, v, s, d):
    b = pd.read_parquet(f"{S}/micro/bbo_{v}_{s}_{d}.parquet")
    t = pd.read_parquet(f"{S}/micro/trd_{v}_{s}_{d}.parquet")
    return (b.ts.values.astype(np.int64), b.bid.values.astype(np.float64), b.ask.values.astype(np.float64),
            b.bq.values.astype(np.float64), b.aq.values.astype(np.float64),
            b.bid_lo.values.astype(np.float64), b.ask_hi.values.astype(np.float64),
            t.ts.values.astype(np.int64), t.price.values.astype(np.float64),
            t.qty.values.astype(np.float64), (t.side.values == "buy"))

def first_true_ts(ts, mask, lo, hi):
    """ts of first True in ts[lo:hi], else -1"""
    if hi <= lo: return -1
    idx = np.flatnonzero(mask[lo:hi])
    return ts[lo + idx[0]] if idx.size else -1

def run(S, v, s, d):
    bts, bid, ask, bq, aq, blo, ahi, tts, tpx, tqty, tbuy = load(S, v, s, d)
    NS = 10**9
    t_start, t_end = bts[0], bts[-1]
    grid = np.arange(t_start, t_end - TTL_S * NS, PLACE_S * NS)
    gi = np.searchsorted(bts, grid)                      # placement quote index
    gi = gi[gi < len(bts) - 1]
    rows = []
    tsell_i = np.flatnonzero(~tbuy); tbuy_i = np.flatnonzero(tbuy)
    for side in ("BUY", "SELL"):
        sgn = 1.0 if side == "BUY" else -1.0
        for i0 in gi:
            t0 = bts[i0]; L = bid[i0] if side == "BUY" else ask[i0]
            Q = (bq[i0] if side == "BUY" else aq[i0])
            if not np.isfinite(L) or not np.isfinite(Q) or Q <= 0: continue
            m0 = (bid[i0] + ask[i0]) / 2.0; s0 = (ask[i0] - bid[i0]) / m0 * 1e4
            tend = t0 + TTL_S * NS
            # --- trade-driven queue consumption
            src = tsell_i if side == "BUY" else tbuy_i
            lo = np.searchsorted(tts[src], t0, "right"); hi = np.searchsorted(tts[src], tend, "right")
            sel = src[lo:hi]
            px, qy, tz = tpx[sel], tqty[sel], tts[sel]
            m = (px <= L) if side == "BUY" else (px >= L)
            f_cons = -1
            if m.any():
                cs = np.cumsum(qy[m]); k = np.searchsorted(cs, Q, "left")
                if k < cs.size: f_cons = tz[m][k]
            # --- 'alone at the touch': our level no longer exists in the book
            blo_i, bhi_i = np.searchsorted(bts, t0, "right"), np.searchsorted(bts, tend, "right")
            cond = (blo[blo_i:bhi_i] < L) if side == "BUY" else (ahi[blo_i:bhi_i] > L)
            f_alone = -1
            idx = np.flatnonzero(cond)
            if idx.size:
                t_alone = bts[blo_i + idx[0]]
                j = np.flatnonzero(tz >= t_alone)
                if j.size: f_alone = tz[j[0]]
            f_real = min([x for x in (f_cons, f_alone) if x > 0], default=-1)
            # --- probe's traversal rule, same attempt
            condt = (ask[blo_i:bhi_i] < L) if side == "BUY" else (bid[blo_i:bhi_i] > L)
            it = np.flatnonzero(condt); f_trav = bts[blo_i + it[0]] if it.size else -1
            r = dict(side=side, t0=t0, m0=m0, s0_bps=s0, q0=Q, lim=L,
                     f_cons=f_cons, f_real=f_real, f_trav=f_trav)
            for h in H:                                        # quotes at t0+h (policy cost)
                k = np.searchsorted(bts, t0 + h * NS, "right") - 1
                r[f"ask_{h}"], r[f"bid_{h}"], r[f"mid_{h}"] = ask[k], bid[k], (bid[k] + ask[k]) / 2
                for nm, ft in (("real", f_real), ("trav", f_trav)):   # markout from fill price
                    if ft > 0:
                        k2 = np.searchsorted(bts, ft + h * NS, "right") - 1
                        if k2 < len(bts):
                            r[f"mko_{nm}_{h}"] = sgn * ((bid[k2] + ask[k2]) / 2 / L - 1) * 1e4
            rows.append(r)
    df = pd.DataFrame(rows)
    for c in ("cons", "real", "trav"):
        df[f"fill_{c}"] = df[f"f_{c}"] > 0
        df[f"ttf_{c}"] = np.where(df[f"f_{c}"] > 0, (df[f"f_{c}"] - df.t0) / 1e9, np.nan)
    df["venue"], df["symbol"], df["date"] = v, s, d
    return df

if __name__ == "__main__":
    S = os.environ["W5_SCRATCH"]
    v, s, d = sys.argv[1], sys.argv[2], sys.argv[3]
    df = run(S, v, s, d)
    df.to_parquet(f"{S}/qsim_{v}_{s}_{d}.parquet", index=False)
    print(f"{v} {s} {d} n={len(df)} spread={df.s0_bps.mean():.3f}bps | "
          f"fill_cons={df.fill_cons.mean():.3f} fill_real={df.fill_real.mean():.3f} "
          f"fill_trav={df.fill_trav.mean():.3f} | ttf_real_med={df.ttf_real.median():.1f}s | "
          f"mko_real_60={df.mko_real_60.mean():.3f} mko_trav_60={df.mko_trav_60.mean():.3f}")
