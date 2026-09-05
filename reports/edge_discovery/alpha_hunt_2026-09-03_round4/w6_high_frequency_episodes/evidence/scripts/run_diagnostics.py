#!/usr/bin/env python3
"""W6 round-4 PHASE 4: two diagnostics that decide how the grid should be read.

D1. BREADTH / CLUSTERING EFFICIENCY (briefing trap "many observations != many
    independent episodes"). For every scored cell compute the EFFECTIVE number
    of independent episodes contributed per calendar day:
        k_eff = (sd_episode_L1 / sd_daymean)^2
    and compare it with the raw episodes/day. redundancy = raw / k_eff is the
    factor by which naive counting overstates information. This is the number
    that governs ETA, and it is what the project keeps rediscovering.

D2. ORTHOGONALITY of the best cell (M8_VOLSHOCK_REVERSION_6x) against the
    project's ALREADY-KNOWN liquidation-cascade edge (LIQ_CASCADE_REPEAT_V1,
    briefing sec.4). Measures what share of M8 episodes sit inside a +/-1h and
    +/-12h window of a liq_cascade / cascade event on the same symbol, and
    re-scores M8 on the DISJOINT subset.
"""
import sys, os, json, glob
import numpy as np, pandas as pd, duckdb
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_hf import load_panel, episode_stats

HOURLY = os.environ.get("W6_HOURLY", "/tmp/w6/hourly/*.parquet")
RES = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
EVENTS = "/home/qbee/futur/data/events"


def d1_breadth():
    cells = json.load(open(os.path.join(RES, "RESULTS_CELLS.json")))
    rows = []
    for c in cells:
        if c.get("insufficient") or not c.get("sd_daymean_bps"):
            continue
        sde, sdd = c.get("sd_episode_L1_bps"), c["sd_daymean_bps"]
        nd = c["n_independent_L2_days"]
        raw_per_day = c["n_raw"] / max(nd, 1)
        keff = (sde / sdd) ** 2 if sde and sdd else np.nan
        rows.append({"mechanism_id": c["mechanism_id"], "family": c["family"],
                     "horizon_h": c["horizon_h"], "tier": c["tier"],
                     "raw_episodes_per_day": raw_per_day,
                     "sd_episode_bps": sde, "sd_daymean_bps": sdd,
                     "k_eff_independent_per_day": keff,
                     "redundancy_factor": raw_per_day / keff if keff and keff > 0 else np.nan,
                     "net_bps_min_1y": c["net_bps_min_for_1y_confirm"],
                     "eta_years": c.get("eta_forward_confirmation_years")})
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(RES, "D1_BREADTH_EFFICIENCY.csv"), index=False)
    print("=== D1 BREADTH: raw episodes/day vs EFFECTIVE independent episodes/day ===")
    g = d.groupby(["family", "horizon_h"]).agg(
        raw_per_day=("raw_episodes_per_day", "median"),
        k_eff=("k_eff_independent_per_day", "median"),
        redundancy=("redundancy_factor", "median"),
        sd_episode=("sd_episode_bps", "median"),
        sd_daymean=("sd_daymean_bps", "median"),
        net_min_1y=("net_bps_min_1y", "median")).reset_index()
    pd.set_option("display.width", 220)
    print(g.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return d


def d2_orthogonality():
    print("\n=== D2 ORTHOGONALITY of M8_VOLSHOCK_REVERSION_6x vs known cascade edge ===")
    con = duckdb.connect(); con.execute("SET TimeZone='UTC'")
    con.execute("PRAGMA threads=2"); con.execute("PRAGMA memory_limit='1500MB'")
    ev = []
    for nm in ("liq_cascade", "cascade"):
        for p in glob.glob(f"{EVENTS}/{nm}*.parquet"):
            e = con.execute(f"SELECT symbol, event_time FROM read_parquet('{p}') "
                            "WHERE event_time IS NOT NULL").df()
            e["src"] = nm
            ev.append(e)
            print(f"  loaded {os.path.basename(p)}: {len(e):,} events, {e.symbol.nunique()} symbols")
    ev = pd.concat(ev, ignore_index=True)
    ev["event_time"] = pd.to_datetime(ev.event_time, utc=True)
    ev["hour_idx"] = ev.event_time.values.astype("datetime64[h]").astype(np.int64)
    ev_syms = set(ev.symbol.unique())
    ekey = set(zip(ev.symbol.values, ev.hour_idx.values))

    df = load_panel(HOURLY, min_dv7d=2e7)
    m = (df.vs.values >= 6.0) & (np.abs(df.z1.values) >= 1.5) & np.isfinite(df.fwd_12h.values)
    sub = df[m].reset_index(drop=True)
    side = -np.sign(sub.z1.values)
    print(f"  M8 episodes (T_ALL, h12): {len(sub):,}  on {sub.symbol.nunique()} symbols")
    cov = sub.symbol.isin(ev_syms).values
    print(f"  share of M8 episodes on a symbol COVERED by the events corpus (49 syms): "
          f"{cov.mean():.1%}  (n={cov.sum():,})")

    out = {"m8_episodes": int(len(sub)), "coverage_share": float(cov.mean())}
    for win in (1, 12):
        hit = np.zeros(len(sub), dtype=bool)
        sy = sub.symbol.values; hi = sub.hour_idx.values
        for off in range(-win, win + 1):
            hit |= np.fromiter(((s, h + off) in ekey for s, h in zip(sy, hi)),
                               dtype=bool, count=len(sub))
        on_cov = hit[cov].mean() if cov.sum() else np.nan
        print(f"  +/-{win:>2}h of a cascade event: overall {hit.mean():.1%} | "
              f"restricted to covered symbols {on_cov:.1%}")
        out[f"overlap_pm{win}h_all"] = float(hit.mean())
        out[f"overlap_pm{win}h_covered_symbols"] = float(on_cov)
        if win == 12 and cov.sum() > 0:
            # re-score M8 on covered symbols only, cascade-overlapping vs disjoint
            for label, mm in (("cascade_overlapping", cov & hit), ("cascade_disjoint", cov & ~hit)):
                if mm.sum() < 200:
                    print(f"    {label}: n={mm.sum()} too small"); continue
                s = sub[mm]
                sr = (side[mm].astype(np.float64) * s.fwd_12h.values.astype(np.float64)) * 1e4
                st = episode_stats(s.ts.values, s.day.values, s.year.values, s.sym_code.values,
                                   s.hour_idx.values, sr, s.dv_1h.values, 12)
                print(f"    {label:22s} n={st['n_raw']:>6d} L1={st['n_independent_L1']:>5d} "
                      f"net={st['net_bps']:+7.2f} n28={st['net_bps_stress28']:+7.2f} "
                      f"t_day={st['t_stat_declustered']:+5.2f} "
                      f"eta={st['eta_forward_confirmation_years'] or float('inf'):.2f}y")
                out[label] = {k: st.get(k) for k in
                              ("n_raw", "n_independent_L1", "net_bps", "net_bps_stress28",
                               "t_stat_declustered", "eta_forward_confirmation_years")}
    with open(os.path.join(RES, "D2_ORTHOGONALITY_M8.json"), "w") as f:
        json.dump(out, f, indent=1, default=float)
    return out


if __name__ == "__main__":
    d1_breadth()
    d2_orthogonality()
