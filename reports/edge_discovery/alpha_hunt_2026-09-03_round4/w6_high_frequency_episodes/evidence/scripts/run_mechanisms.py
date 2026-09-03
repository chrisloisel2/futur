#!/usr/bin/env python3
"""W6 round-4 PHASE 2: the preregistered mechanism grid, every cell scored with
the full §2 validation gate. Nothing here is added after seeing a result: the
grid is exactly PREREGISTRATION.md §6.
"""
import sys, os, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_hf import load_panel, episode_stats, COST_BPS

HOURLY = os.environ.get("W6_HOURLY", "/tmp/w6/hourly/*.parquet")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
T_LIQ, T_ALL, T_DEEP = 2e8, 2e7, 2e9
HORIZONS = [(1, "fwd_1h"), (4, "fwd_4h"), (12, "fwd_12h")]


def directional_specs():
    """(id, family, trigger_fn -> (mask, side), note)"""
    S = []
    for th in (1.5, 2.5, 4.0):
        S.append((f"M1_RESID_REVERSION_1H_z{th}", "A_resid_move",
                  lambda d, th=th: (np.abs(d.z1.values) >= th, -np.sign(d.z1.values))))
        S.append((f"M2_RESID_CONTINUATION_1H_z{th}", "A_resid_move",
                  lambda d, th=th: (np.abs(d.z1.values) >= th, +np.sign(d.z1.values))))
    for th in (1.5, 2.5):
        S.append((f"M3_RESID_REVERSION_4H_z{th}", "A_resid_move",
                  lambda d, th=th: (np.abs(d.z4.values) >= th, -np.sign(d.z4.values))))
    for th in (0.30, 0.50):
        S.append((f"M4_FLOW_IMBALANCE_FADE_{th}", "B_flow",
                  lambda d, th=th: (np.abs(d.fi_1h.values) >= th, -np.sign(d.fi_1h.values))))
        S.append((f"M5_FLOW_IMBALANCE_FOLLOW_{th}", "B_flow",
                  lambda d, th=th: (np.abs(d.fi_1h.values) >= th, +np.sign(d.fi_1h.values))))
    for th in (0.01, 0.02):
        S.append((f"M6_OI_BUILD_FADE_{th}", "C_oi",
                  lambda d, th=th: ((d.doi_1h.values >= th) & (np.abs(d.z1.values) >= 1.0), -np.sign(d.z1.values))))
        S.append((f"M7_OI_FLUSH_BOUNCE_{th}", "C_oi",
                  lambda d, th=th: ((d.doi_1h.values <= -th) & (np.abs(d.z1.values) >= 1.0), -np.sign(d.z1.values))))
    for th in (3.0, 6.0):
        S.append((f"M8_VOLSHOCK_REVERSION_{th}x", "D_volshock",
                  lambda d, th=th: ((d.vs.values >= th) & (np.abs(d.z1.values) >= 1.5), -np.sign(d.z1.values))))
        S.append((f"M9_VOLSHOCK_CONTINUATION_{th}x", "D_volshock",
                  lambda d, th=th: ((d.vs.values >= th) & (np.abs(d.z1.values) >= 1.5), +np.sign(d.z1.values))))
    for th in (2.0, 3.0):
        S.append((f"M10_BASIS_Z_REVERSION_{th}", "E_basis",
                  lambda d, th=th: (np.abs(d.bz1.values) >= th, -np.sign(d.bz1.values))))
    S.append(("M11_FLOW_PRICE_DIVERGENCE", "F_divergence",
              lambda d: ((np.sign(d.fi_1h.values) != np.sign(d.z1.values)) &
                         (np.abs(d.fi_1h.values) >= 0.30) & (np.abs(d.z1.values) >= 1.0),
                         np.sign(d.fi_1h.values))))
    S.append(("M17_FUNDING_CROWDING_FADE", "H_funding_control",
              lambda d: (d.fpct.values >= 0.90, -np.sign(d.fr.values))))
    return S


XS_SPECS = [  # (id, feature, long_side)  long_side=-1 -> long BOTTOM decile / short TOP
    ("M12_XS_RESID_REVERSAL_1H", "z1", -1),
    ("M13_XS_FLOW_REVERSAL_1H", "fi_1h", -1),
    ("M14_XS_OI_SHOCK", "doi_1h", -1),
    ("M15_XS_VOLSHOCK", "vs", +1),
    ("M16_XS_BASIS_REVERSAL", "bz1", -1),
]


def run_directional(df, tag, results):
    for mid, fam, fn in directional_specs():
        mask, side = fn(df)
        mask = np.asarray(mask)
        for h, col in HORIZONS:
            m = mask & np.isfinite(df[col].values) & np.isfinite(side)
            if m.sum() < 200:
                results.append({"mechanism_id": mid, "family": fam, "horizon_h": h, "tier": tag,
                                "n_raw": int(m.sum()), "verdict": "DATA_LIMITED",
                                "note": "fewer than 200 raw episodes"})
                continue
            sub = df[m]
            sr = (side[m] * sub[col].values) * 1e4
            st = episode_stats(sub.ts.values, sub.ts.values.astype("datetime64[D]"),
                               sub.year.values, sub.sym_code.values, sub.hour_idx.values,
                               sr, sub.dv_1h.values, h, xs_mode=False)
            st.update({"mechanism_id": mid, "family": fam, "horizon_h": h, "tier": tag,
                       "construct": "directional_single_symbol_residual"})
            results.append(st)
            print(f"{tag} {mid} h{h}: n={st['n_raw']} L1={st.get('n_independent_L1')} "
                  f"net={st.get('net_bps'):.2f} t={st.get('t_stat_declustered'):.2f} "
                  f"eta_y={st.get('eta_forward_confirmation_years'):.2f}", flush=True)


def xs_episodes(df, feat, col, long_side, decile=0.10, min_n=30):
    sub = df[np.isfinite(df[feat].values) & np.isfinite(df[col].values)]
    n_h = sub.groupby("hour_idx")[feat].transform("size")
    sub = sub[n_h.values >= min_n]
    if len(sub) == 0:
        return None
    pr = sub.groupby("hour_idx")[feat].rank(pct=True, method="first")
    lo_m = (pr <= decile).values
    hi_m = (pr >= 1 - decile).values
    lo = sub[lo_m].groupby("hour_idx")[col].mean()
    hi = sub[hi_m].groupby("hour_idx")[col].mean()
    nb = sub[lo_m].groupby("hour_idx")[col].size()
    dv = sub[lo_m].groupby("hour_idx")["dv_1h"].sum()
    j = pd.concat([lo.rename("lo"), hi.rename("hi"), nb.rename("nb"), dv.rename("dv")], axis=1).dropna()
    j = j[j.nb >= 3]
    if len(j) == 0:
        return None
    spread = (j.lo - j.hi) if long_side < 0 else (j.hi - j.lo)
    ret_bps = (spread.values / 2.0) * 1e4          # one unit of GROSS notional
    hour_idx = j.index.values.astype(np.int64)
    ts = pd.to_datetime(hour_idx * 3600, unit="s", utc=True)
    return ts, hour_idx, ret_bps, j.dv.values, j.nb.values


def run_xs(df, tag, results):
    for mid, feat, long_side in XS_SPECS:
        for h, col in HORIZONS:
            r = xs_episodes(df, feat, col, long_side)
            if r is None:
                continue
            ts, hour_idx, ret_bps, dv, nb = r
            st = episode_stats(ts.values, ts.values.astype("datetime64[D]"),
                               ts.year.values.astype(np.int16), np.zeros(len(ts), np.int32),
                               hour_idx, ret_bps, dv, h, xs_mode=True)
            st.update({"mechanism_id": mid, "family": "G_cross_sectional_hourly",
                       "horizon_h": h, "tier": tag,
                       "construct": "hourly_decile_long_short_1unit_gross_notional",
                       "median_basket_size_per_leg": float(np.median(nb))})
            results.append(st)
            print(f"{tag} {mid} h{h}: n={st['n_raw']} net={st.get('net_bps'):.2f} "
                  f"t={st.get('t_stat_declustered'):.2f} eta_y={st.get('eta_forward_confirmation_years'):.2f}",
                  flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    df = load_panel(HOURLY, min_dv7d=T_ALL)
    print(f"panel rows={len(df):,}", flush=True)
    liq = df[df.dv_7d >= T_LIQ].reset_index(drop=True)
    print(f"T_LIQ rows={len(liq):,} symbols={liq.symbol.nunique()}", flush=True)
    results = []
    run_directional(liq, "T_LIQ", results)
    run_xs(liq, "T_LIQ", results)
    with open(os.path.join(OUT, "MECHANISMS_T_LIQ.json"), "w") as f:
        json.dump(results, f, indent=1, default=float)
    print("wrote", len(results), "cells")


if __name__ == "__main__":
    main()
