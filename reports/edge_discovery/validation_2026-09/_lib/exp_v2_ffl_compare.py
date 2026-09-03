"""FAR_FROM_LOW — comparabilité stricte avec la réclamation et avec la spec LIVE.

Le gate principal (exp_v2_cascade.py) juge sur des moyennes d'ÉPISODE (L3) sur
2022+. La réclamation et le freeze_spec live, eux, publient des moyennes au niveau
ÉVÉNEMENT sur la population complète (2020+) et sur l'OOS 2025+. Ce script produit
les deux vues côte à côte pour que l'écart soit attribuable (fenêtre ? pondération ?
signe ?) et non un artefact de protocole.

Aucune spec n'est modifiée ici — c'est un diagnostic de comparabilité, pas un test.
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


def event_level(df: pd.DataFrame, cost: float = 14.0) -> dict:
    """Moyenne au niveau ÉVÉNEMENT (convention de la réclamation : pas de
    déclustering, t de Student naïf) — reproduit littéralement le chiffre publié."""
    x = df["fwd_4h"].to_numpy() * 1e4 - cost
    if len(x) < 2:
        return {"n": int(len(x))}
    return {
        "n": int(len(x)),
        "net14_event_level": round(float(x.mean()), 2),
        "t_naive": round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2),
        "pf": round(float(x[x > 0].sum() / -x[x < 0].sum()), 3) if (x < 0).any() else None,
    }


def episode_level(df: pd.DataFrame, cost: float = 14.0) -> dict:
    """Moyenne au niveau ÉPISODE L3 (protocole de validation)."""
    if df.empty:
        return {"n_L3": 0}
    g, _, l3 = ev2.episode_returns(df)
    x = g.to_numpy() - cost
    m, se, t = vl.cluster_robust_t(x, l3)
    return {"n_L3": int(len(x)), "net14_episode_level": round(float(m), 2),
            "t_L3": None if not np.isfinite(t) else round(float(t), 3)}


def main():
    res = {}
    for label, since in (("full_2020plus", "2020-01-01"), ("claim_window_2022plus", "2022-01-01")):
        a = ev2.add_declustering(ev2.population_A(since=since))
        far_live = a[a["dist_low_24h"] >= 0.05]          # seuil EXACT de la spec live
        near_live = a[a["dist_low_24h"] < 0.05]
        block = {
            "population_n": int(len(a)),
            "window": [str(a.event_time.min()), str(a.event_time.max())],
            "far_live_0p05": {**event_level(far_live), **episode_level(far_live)},
            "near_live_0p05": {**event_level(near_live), **episode_level(near_live)},
        }
        # slice OOS 2025+ (définition du freeze_spec)
        oos = a[a["event_time"] >= pd.Timestamp("2025-01-01", tz="UTC")]
        block["oos_2025plus"] = {
            "far": {**event_level(oos[oos["dist_low_24h"] >= 0.05]),
                    **episode_level(oos[oos["dist_low_24h"] >= 0.05])},
            "near": {**event_level(oos[oos["dist_low_24h"] < 0.05]),
                     **episode_level(oos[oos["dist_low_24h"] < 0.05])},
        }
        # année par année, niveau événement, bras far
        by_year = {}
        for y, g in far_live.groupby(far_live["event_time"].dt.year):
            by_year[int(y)] = event_level(g)["net14_event_level"]
        block["far_live_by_year_event_level"] = by_year
        res[label] = block
        print(f"\n── {label} (n={len(a)}, {block['window'][0][:10]}..{block['window'][1][:10]})")
        for k in ("far_live_0p05", "near_live_0p05"):
            b = block[k]
            print(f"   {k:18s} n={b['n']:6d} net14_event={b['net14_event_level']:8.2f} "
                  f"t_naive={b['t_naive']:6.2f} | L3={b['n_L3']:5d} "
                  f"net14_episode={b['net14_episode_level']:8.2f} t_L3={b['t_L3']}")
        o = block["oos_2025plus"]
        print(f"   OOS2025+ far  n={o['far']['n']:6d} net14_event={o['far'].get('net14_event_level')} "
              f"t={o['far'].get('t_naive')} | near n={o['near']['n']:6d} "
              f"net14_event={o['near'].get('net14_event_level')}")
        print(f"   far by year (event level): {by_year}")

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/v2_ffl_compare.json", "w") as f:
        json.dump(res, f, indent=2, default=str)


if __name__ == "__main__":
    main()
