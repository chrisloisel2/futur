"""W8 / audit - resolves the disagreement between d1's two placebos.

d1-P2 (hour x month stratified) drew from the WHOLE cell, and the (hour_utc x month) cells are
small (median 25 events); the real selection takes a large share of some of them, so P2 partly
redraws the real signal's own episodes and inherits their return. d3 repeats P2 drawing ONLY
from NON-SELECTED events of each cell (a genuinely disjoint placebo), and reports the measured
overlap of the contaminated version. If the diagnosis is right, the disjoint placebo collapses
towards the selection-weighted cell mean that d2 removes.
"""
import json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from a1_track_a_ensemble import causal_quantile_threshold, decluster_L1, _utc, COST_RT, OUTDIR
from d1_placebo_hour_stratified import build_scores, daily_stats

N_DRAWS = 400
RNG = np.random.default_rng(20260906)
VAR = {"EW_WALKFORWARD": 0.90, "CONFIDENCE_IC_WF": 0.80}


def main():
    df, mo, y, scores = build_scores()
    hour = df["event_time"].dt.hour.values
    out = {}
    for name, q in VAR.items():
        sc = scores[name]
        thr = causal_quantile_threshold(sc, mo, q)
        pop = np.isfinite(thr)
        real_mask = np.isfinite(sc) & pop & (sc >= thr)
        real = daily_stats(df, y, real_mask)
        net = y - COST_RT
        cells = pd.DataFrame({"h": hour, "m": mo, "i": np.arange(len(df)),
                              "sel": real_mask})[pop]
        need = cells[cells["sel"]].groupby(["h", "m"]).size()
        pool_all = {k: v["i"].values for k, v in cells.groupby(["h", "m"])}
        pool_free = {k: v.loc[~v["sel"], "i"].values for k, v in cells.groupby(["h", "m"])}
        # contamination of the d1-P2 design: expected share of a draw that lands on real events
        ov = [(min(int(c), len(pool_all[k])) * (need.get(k, 0) / max(len(pool_all[k]), 1)))
              for k, c in need.items() if k in pool_all]
        overlap = float(np.sum(ov) / need.sum())
        # selection-weighted cell mean (what d2's hour-bar control removes)
        mu = cells.assign(x=net[pop]).groupby(["h", "m"])["x"].mean()
        swcm = float(np.average(mu.reindex(need.index).values, weights=need.values))

        draws, short = [], 0
        for _ in range(N_DRAWS):
            pick = []
            for k, c in need.items():
                fp = pool_free.get(k, np.array([], int))
                n = min(int(c), len(fp))
                short += int(c) - n
                if n > 0:
                    pick.append(RNG.choice(fp, size=n, replace=False))
            m = np.zeros(len(df), bool)
            m[np.concatenate(pick)] = True
            draws.append(daily_stats(df, y, m))
        dn = np.array([d["day_net_bps"] for d in draws])
        en = np.array([d["episode_net_bps"] for d in draws])
        out[name] = {
            "real_day_net_bps": real["day_net_bps"],
            "real_episode_net_bps": real["episode_net_bps"],
            "d1_P2_contamination_expected_overlap_share": overlap,
            "selection_weighted_cell_mean_bps": swcm,
            "disjoint_placebo_day_net_bps": {"mean": float(dn.mean()), "sd": float(dn.std(ddof=1)),
                                             "p95": float(np.percentile(dn, 95)),
                                             "max": float(dn.max())},
            "disjoint_placebo_episode_net_bps": {"mean": float(en.mean()),
                                                 "sd": float(en.std(ddof=1))},
            "p_value_one_sided": float(np.mean(dn >= real["day_net_bps"])),
            "share_of_real_day_edge_explained": float(dn.mean() / real["day_net_bps"]),
            "cells_short_of_free_events_per_draw": short / N_DRAWS}
        r = out[name]
        print(f"\n=== A::{name} q{int(q*100)} (disjoint hour x month placebo) ===")
        print(f"  REAL day_net {real['day_net_bps']:+7.2f} | episode {real['episode_net_bps']:+7.2f}")
        print(f"  d1-P2 expected overlap with the real selection: {100*overlap:.1f}%  <-- the flaw")
        print(f"  selection-weighted cell mean (what d2 removes): {swcm:+7.2f} bps")
        print(f"  DISJOINT placebo day_net mean {dn.mean():+7.2f} sd {dn.std(ddof=1):.2f} "
              f"max {dn.max():+7.2f}  p={r['p_value_one_sided']:.4f}")
        print(f"  share of real day-level edge explained: {100*r['share_of_real_day_edge_explained']:.1f}%")
    with open(os.path.join(OUTDIR, "d3_placebo_disjoint_results.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("\nwrote d3_placebo_disjoint_results.json")


if __name__ == "__main__":
    main()
