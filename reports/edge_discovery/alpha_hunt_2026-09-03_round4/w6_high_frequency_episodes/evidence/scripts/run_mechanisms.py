#!/usr/bin/env python3
"""W6 round-4 PHASE 2: the preregistered mechanism grid, every cell scored with
the full sec.2 validation gate.

The grid is exactly PREREGISTRATION.md sec.6 (M1..M17). Nothing was added after
seeing a result. Three liquidity tiers (T_DEEP / T_LIQ primary / T_ALL) are run
because the preregistration fixed three tiers.

Cost convention
---------------
* Directional single-symbol mechanisms: one discrete entry + one discrete exit
  per episode  ->  flat 14 bps (28 under stress). No netting credit is taken.
* Cross-sectional hourly mechanisms: charged on REAL TURNOVER
  (briefing addendum sec.8 item 9): 7 bps one-way per unit of gross notional
  traded, cost_per_rebalance = 7 * mean(sum_i |w_i(t) - w_i(t-h)|), evaluated on
  the implementable NON-OVERLAPPING h-spaced rebalance schedule. A portfolio
  that fully rotates every period gives sum|dw| = 2 -> exactly 14 bps, so this
  can only ever be <= the flat convention, never above it. The flat-14 number is
  reported alongside as `net_bps_flat14` so the reader can undo the credit.

Usage:  W6_HOURLY='<scratch>/hourly/*.parquet' python run_mechanisms.py
"""
import sys, os, json, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_hf import (load_panel, episode_stats, decluster_nonoverlap,
                    COST_BPS, COST_STRESS_BPS)

HOURLY = os.environ.get("W6_HOURLY", "/tmp/w6/hourly/*.parquet")
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
T_ALL, T_LIQ, T_DEEP = 2e7, 2e8, 2e9
HORIZONS = [(1, "fwd_1h"), (4, "fwd_4h"), (12, "fwd_12h")]
MIN_RAW = 200            # preregistered minimum raw episodes for a directional cell
XS_MIN_NAMES = 30        # preregistered minimum cross-section width
XS_DECILE = 0.10
ONE_WAY_BPS = COST_BPS / 2.0        # 7 bps per unit of gross notional, one way


# ------------------------------------------------------------------ sec.6 grid
def directional_specs():
    S = []
    for th in (1.5, 2.5, 4.0):
        S.append((f"M1_RESID_REVERSION_1H_z{th}", "A_resid_move", "M2",
                  lambda d, th=th: (np.abs(d.z1.values) >= th, -np.sign(d.z1.values))))
        S.append((f"M2_RESID_CONTINUATION_1H_z{th}", "A_resid_move", "M1",
                  lambda d, th=th: (np.abs(d.z1.values) >= th, +np.sign(d.z1.values))))
    for th in (1.5, 2.5):
        S.append((f"M3_RESID_REVERSION_4H_z{th}", "A_resid_move", None,
                  lambda d, th=th: (np.abs(d.z4.values) >= th, -np.sign(d.z4.values))))
    for th in (0.30, 0.50):
        S.append((f"M4_FLOW_IMBALANCE_FADE_{th}", "B_flow", "M5",
                  lambda d, th=th: (np.abs(d.fi_1h.values) >= th, -np.sign(d.fi_1h.values))))
        S.append((f"M5_FLOW_IMBALANCE_FOLLOW_{th}", "B_flow", "M4",
                  lambda d, th=th: (np.abs(d.fi_1h.values) >= th, +np.sign(d.fi_1h.values))))
    for th in (0.01, 0.02):
        S.append((f"M6_OI_BUILD_FADE_{th}", "C_oi", None,
                  lambda d, th=th: ((d.doi_1h.values >= th) & (np.abs(d.z1.values) >= 1.0), -np.sign(d.z1.values))))
        S.append((f"M7_OI_FLUSH_BOUNCE_{th}", "C_oi", None,
                  lambda d, th=th: ((d.doi_1h.values <= -th) & (np.abs(d.z1.values) >= 1.0), -np.sign(d.z1.values))))
    for th in (3.0, 6.0):
        S.append((f"M8_VOLSHOCK_REVERSION_{th}x", "D_volshock", "M9",
                  lambda d, th=th: ((d.vs.values >= th) & (np.abs(d.z1.values) >= 1.5), -np.sign(d.z1.values))))
        S.append((f"M9_VOLSHOCK_CONTINUATION_{th}x", "D_volshock", "M8",
                  lambda d, th=th: ((d.vs.values >= th) & (np.abs(d.z1.values) >= 1.5), +np.sign(d.z1.values))))
    for th in (2.0, 3.0):
        S.append((f"M10_BASIS_Z_REVERSION_{th}", "E_basis", None,
                  lambda d, th=th: (np.abs(d.bz1.values) >= th, -np.sign(d.bz1.values))))
    S.append(("M11_FLOW_PRICE_DIVERGENCE", "F_divergence", None,
              lambda d: ((np.sign(d.fi_1h.values) != np.sign(d.z1.values)) &
                         (np.abs(d.fi_1h.values) >= 0.30) & (np.abs(d.z1.values) >= 1.0),
                         np.sign(d.fi_1h.values))))
    S.append(("M17_FUNDING_CROWDING_FADE", "H_funding_control", None,
              lambda d: (d.fpct.values >= 0.90, -np.sign(d.fr.values))))
    return S


XS_SPECS = [  # (id, feature, long_side) long_side=-1 -> long BOTTOM decile / short TOP
    ("M12_XS_RESID_REVERSAL_1H", "z1", -1),
    ("M13_XS_FLOW_REVERSAL_1H", "fi_1h", -1),
    ("M14_XS_OI_SHOCK", "doi_1h", -1),
    ("M15_XS_VOLSHOCK", "vs", +1),
    ("M16_XS_BASIS_REVERSAL", "bz1", -1),
]


# ------------------------------------------------------------------ verdicts
def assign_verdict(st):
    """PREREGISTRATION.md sec.7, applied mechanically. One conservatism added at
    recovery time and declared as such: the raw-episode net AND the day-mean net
    must BOTH be positive (a mechanism carried only by heavy days is not an
    edge you can trade daily). This is strictly stricter than sec.7."""
    if st.get("insufficient") or "net_bps" not in st:
        return "DATA_LIMITED", "fewer than the preregistered minimum episodes"
    net, s28 = st["net_bps"], st["net_bps_stress28"]
    netd = st.get("net_bps_daymean")
    if net <= 0 or (netd is not None and netd <= 0):
        return ("DEAD" if net < -5 else "WEAK"), f"net_bps={net:.1f} (daymean {netd:.1f})"
    if s28 <= 0:
        return "COST_FRAGILE", f"net14=+{net:.1f} but net28={s28:.1f}"
    eta = st.get("eta_forward_confirmation_years")
    if eta is None or not np.isfinite(eta) or eta > 3.0:
        return "UNCONFIRMABLE_IN_HORIZON", f"headline ETA={eta if eta else float('inf'):.2f} y > 3 y"
    ex = st.get("ex_best_year_net_bps")
    if ex is None or ex <= 0 or ex < 0.4 * net:
        return "REGIME_DEPENDENT", (f"ex-best-year net={ex if ex is not None else float('nan'):.1f} "
                                    f"vs full {net:.1f} (best year {st.get('best_year')})")
    t = abs(st.get("t_stat_declustered") or 0.0)
    ci = st.get("bootstrap_ci95_net_bps") or [None, None]
    ci_ok = ci[0] is not None and ci[0] > 0
    if t >= 3.0 and ci_ok:
        return "VALIDATED_FOR_FORWARD", f"|t_day|={t:.2f}, CI95 net=[{ci[0]:.1f},{ci[1]:.1f}], ETA={eta:.2f}y"
    miss = []
    if t < 3.0:
        miss.append(f"|t_day|={t:.2f} < 3.0")
    if not ci_ok:
        miss.append(f"bootstrap CI95 net includes 0 [{ci[0]},{ci[1]}]")
    return "PROMISING_NEEDS_VALIDATION", "; ".join(miss)


# ------------------------------------------------------------------ runners
def run_directional(df, tag, results):
    for mid, fam, mirror, fn in directional_specs():
        mask, side = fn(df)
        mask = np.asarray(mask)
        for h, col in HORIZONS:
            m = mask & np.isfinite(df[col].values) & np.isfinite(side) & (side != 0)
            if m.sum() < MIN_RAW:
                results.append({"mechanism_id": mid, "family": fam, "horizon_h": h, "tier": tag,
                                "n_raw": int(m.sum()), "verdict": "DATA_LIMITED",
                                "verdict_reason": f"{int(m.sum())} raw episodes < {MIN_RAW}",
                                "construct": "directional_single_symbol_residual",
                                "mirror_of": mirror, "insufficient": True})
                continue
            sub = df[m]
            sr = (side[m].astype(np.float64) * sub[col].values.astype(np.float64)) * 1e4
            st = episode_stats(sub.ts.values, sub.day.values, sub.year.values,
                               sub.sym_code.values, sub.hour_idx.values,
                               sr, sub.dv_1h.values, h, xs_mode=False)
            st.update({"mechanism_id": mid, "family": fam, "horizon_h": h, "tier": tag,
                       "construct": "directional_single_symbol_residual",
                       "mirror_of": mirror,
                       "n_symbols": int(sub.symbol.nunique())})
            v, why = assign_verdict(st)
            st["verdict"], st["verdict_reason"] = v, why
            results.append(st)
            print(f"  {tag:6s} {mid:34s} h{h:<2d} n={st['n_raw']:>7d} L1={st['n_independent_L1']:>6d} "
                  f"net={st['net_bps']:+7.2f} n28={st['net_bps_stress28']:+7.2f} "
                  f"t={st['t_stat_declustered'] or float('nan'):+5.2f} "
                  f"eta={st['eta_forward_confirmation_years'] or float('inf'):8.2f}y  {v}", flush=True)


def xs_build(df, feat):
    """PIT cross-sectional ranking, computed once per feature (not per horizon).
    Returns (sub, lo_mask, hi_mask, hours) with legs of >=3 names."""
    ok = np.isfinite(df[feat].values)
    sub = df[ok]
    cnt = sub.groupby("hour_idx")[feat].transform("size").values
    sub = sub[cnt >= XS_MIN_NAMES]
    if len(sub) == 0:
        return None
    pr = sub.groupby("hour_idx")[feat].rank(pct=True, method="first").values
    lo = pr <= XS_DECILE
    hi = pr >= 1.0 - XS_DECILE
    return sub, lo, hi


def xs_turnover_cost(sub, lo, hi, long_side, hours_kept_idx, hour_of_row, hours_all):
    """Mean per-rebalance cost in bps on the NON-OVERLAPPING schedule.
    w = +0.5/k_long on long leg, -0.5/k_short on short leg (1 unit gross)."""
    sym = sub.sym_code.values
    nsym = int(sym.max()) + 1
    hpos = np.searchsorted(hours_all, hour_of_row)
    lo_leg, hi_leg = (lo, hi) if long_side < 0 else (hi, lo)
    k_lo = np.bincount(hpos[lo_leg], minlength=len(hours_all)).astype(np.float64)
    k_hi = np.bincount(hpos[hi_leg], minlength=len(hours_all)).astype(np.float64)
    W = np.zeros((len(hours_all), nsym), dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        W[hpos[lo_leg], sym[lo_leg]] = (0.5 / np.maximum(k_lo, 1))[hpos[lo_leg]]
        W[hpos[hi_leg], sym[hi_leg]] = -(0.5 / np.maximum(k_hi, 1))[hpos[hi_leg]]
    k = np.asarray(hours_kept_idx)
    if len(k) < 2:
        return COST_BPS, 2.0
    d = np.abs(W[k[1:]] - W[k[:-1]]).sum(axis=1)
    tno = float(np.mean(d))
    del W
    return ONE_WAY_BPS * tno, tno


def run_xs(df, tag, results):
    for mid, feat, long_side in XS_SPECS:
        built = xs_build(df, feat)
        if built is None:
            continue
        sub, lo, hi = built
        for h, col in HORIZONS:
            fin = np.isfinite(sub[col].values)
            s2 = sub[fin]; lo2, hi2 = lo[fin], hi[fin]
            if len(s2) == 0:
                continue
            g = s2.groupby("hour_idx")[col]
            mlo = s2[lo2].groupby("hour_idx")[col].mean()
            mhi = s2[hi2].groupby("hour_idx")[col].mean()
            nlo = s2[lo2].groupby("hour_idx")[col].size()
            nhi = s2[hi2].groupby("hour_idx")[col].size()
            dvl = s2[lo2].groupby("hour_idx")["dv_1h"].sum()
            j = pd.concat([mlo.rename("lo"), mhi.rename("hi"), nlo.rename("nlo"),
                           nhi.rename("nhi"), dvl.rename("dv")], axis=1).dropna()
            j = j[(j.nlo >= 3) & (j.nhi >= 3)].sort_index()
            if len(j) < MIN_RAW:
                continue
            spread = (j.lo - j.hi) if long_side < 0 else (j.hi - j.lo)
            ret_bps = (spread.values.astype(np.float64) / 2.0) * 1e4   # 1 unit gross notional
            hours = j.index.values.astype(np.int64)
            ts = pd.to_datetime(hours * 3600, unit="s", utc=True)
            keep = decluster_nonoverlap(hours, h)
            cost, tno = xs_turnover_cost(s2, lo2, hi2, long_side, np.flatnonzero(keep),
                                         s2.hour_idx.values, hours)
            st = episode_stats(ts.values, ts.values.astype("datetime64[D]"),
                               ts.year.values.astype(np.int16), np.zeros(len(ts), np.int32),
                               hours, ret_bps, j.dv.values, h, xs_mode=True,
                               cost_bps=cost, cost_bps_stress=2.0 * cost)
            st.update({"mechanism_id": mid, "family": "G_cross_sectional_hourly",
                       "horizon_h": h, "tier": tag,
                       "construct": "hourly_decile_long_short_1unit_gross_notional",
                       "mirror_of": None,
                       "median_basket_size_per_leg": float(np.median(j.nlo.values)),
                       "mean_turnover_per_rebalance": tno,
                       "net_bps_flat14": float(st_gross(ret_bps) - COST_BPS),
                       "net_bps_flat28": float(st_gross(ret_bps) - COST_STRESS_BPS),
                       "n_symbols": int(s2.symbol.nunique())})
            v, why = assign_verdict(st)
            st["verdict"], st["verdict_reason"] = v, why
            results.append(st)
            print(f"  {tag:6s} {mid:34s} h{h:<2d} n={st['n_raw']:>7d} L1={st['n_independent_L1']:>6d} "
                  f"cost={cost:5.2f} tno={tno:4.2f} net={st['net_bps']:+7.2f} "
                  f"n28={st['net_bps_stress28']:+7.2f} t={st['t_stat_declustered'] or float('nan'):+5.2f} "
                  f"eta={st['eta_forward_confirmation_years'] or float('inf'):8.2f}y  {v}", flush=True)


def st_gross(x):
    return float(np.mean(x))


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    df = load_panel(HOURLY, min_dv7d=T_ALL)
    print(f"panel rows={len(df):,} symbols={df.symbol.nunique()} ({time.time()-t0:.0f}s)", flush=True)
    results = []
    tiers = [("T_LIQ", T_LIQ), ("T_DEEP", T_DEEP), ("T_ALL", T_ALL)]
    for tag, thr in tiers:
        sub = df[df.dv_7d.values >= thr].reset_index(drop=True)
        print(f"\n===== TIER {tag}: rows={len(sub):,} symbols={sub.symbol.nunique()} =====", flush=True)
        run_directional(sub, tag, results)
        run_xs(sub, tag, results)
        del sub
        with open(os.path.join(OUT, "MECHANISMS.json"), "w") as f:
            json.dump(results, f, indent=1, default=float)
    print(f"\nwrote {len(results)} cells to {OUT}/MECHANISMS.json in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
