"""FAR_FROM_LOW — l'inversion de signe dépend-elle du choix du gap d'épisode ?

Le verdict de validation repose sur l'unité d'indépendance L3 (épisode cross-symbole
chaîné, gap 4 h préenregistré). Si le signe basculait à 3 h ou à 6 h, le verdict serait
un artefact de ce choix. On balaie donc le gap de 30 min à 24 h — c'est une sensibilité
sur l'UNITÉ D'INFÉRENCE, jamais sur le signal (aucun paramètre du mécanisme ne bouge).

On reporte aussi le bras de référence inconditionnel (toute la population A) : le vrai
test est « far vs baseline » et « near vs baseline », pas « far > 0 ».
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl      # noqa: E402
import exp_v2_cascade as ev2     # noqa: E402

OUT = "/home/qbee/futur/reports/edge_discovery/validation_2026-09/_lib/out"
COST = 14.0


def arm_stats(a: pd.DataFrame, mask: pd.Series, gap: pd.Timedelta) -> dict:
    """Moyenne d'épisode d'un bras, pour un gap de chaînage donné."""
    sub = a[mask].sort_values("event_time")
    if len(sub) < 10:
        return {}
    ep = vl.chain_episodes(sub["event_time"], gap)
    g = pd.Series(sub["fwd_4h"].to_numpy() * 1e4).groupby(ep).mean().to_numpy() - COST
    m, se, t = vl.cluster_robust_t(g, np.arange(len(g)))
    return {"n_ep": int(len(g)), "net14": round(float(m), 2),
            "t": None if not np.isfinite(t) else round(float(t), 2)}


def main():
    a = ev2.add_declustering(ev2.population_A(since="2022-01-01"))
    far = a["dist_low_24h"] >= 0.05
    rows = []
    print(f"{'gap':>8s} | {'far net14':>10s} {'t':>6s} {'n_ep':>6s} | "
          f"{'near net14':>10s} {'t':>6s} {'n_ep':>6s} | {'baseline':>9s} {'far-base':>9s}")
    for gap_label, gap in (("30min", pd.Timedelta(minutes=30)), ("1h", pd.Timedelta(hours=1)),
                           ("2h", pd.Timedelta(hours=2)), ("4h", pd.Timedelta(hours=4)),
                           ("6h", pd.Timedelta(hours=6)), ("12h", pd.Timedelta(hours=12)),
                           ("24h", pd.Timedelta(hours=24))):
        f = arm_stats(a, far, gap)
        n = arm_stats(a, ~far, gap)
        b = arm_stats(a, pd.Series(True, index=a.index), gap)
        rows.append({"gap": gap_label, "far": f, "near": n, "baseline": b,
                     "far_minus_baseline": round(f["net14"] - b["net14"], 2)})
        print(f"{gap_label:>8s} | {f['net14']:10.2f} {f['t']:6.2f} {f['n_ep']:6d} | "
              f"{n['net14']:10.2f} {n['t']:6.2f} {n['n_ep']:6d} | {b['net14']:9.2f} "
              f"{rows[-1]['far_minus_baseline']:9.2f}")

    # niveau événement, pour mémoire (convention de la réclamation)
    ev = lambda m: round(float((a[m]["fwd_4h"].mean() * 1e4) - COST), 2)   # noqa: E731
    print(f"\nniveau ÉVÉNEMENT (convention de la réclamation) : far={ev(far)} "
          f"near={ev(~far)} baseline={ev(pd.Series(True, index=a.index))}")
    print(f"événements par épisode : far={len(a[far])/rows[3]['far']['n_ep']:.2f} "
          f"near={len(a[~far])/rows[3]['near']['n_ep']:.2f}")

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/v2_ffl_gap_sensitivity.json", "w") as f:
        json.dump({"rows": rows,
                   "event_level": {"far": ev(far), "near": ev(~far)}}, f, indent=2, default=str)


if __name__ == "__main__":
    main()
