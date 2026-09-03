"""Statistique de référence : moyenne PONDÉRÉE PAR ÉVÉNEMENT + SE CLUSTER-ROBUSTE.

Pourquoi elle tranche le débat de pondération :
  - la moyenne par ÉVÉNEMENT est le bon estimateur du P&L attendu par trade (c'est ce
    qu'on encaisse si on prend chaque événement) ;
  - la moyenne par ÉPISODE est le bon estimateur de l'évidence indépendante ;
  - l'erreur-type CLUSTER-ROBUSTE sur les épisodes corrige l'inflation du t due aux
    jambes corrélées, SANS changer le point d'estimation.

Combiner les deux (point estimate événement + SE clusterisée) donne la statistique
que ni la découverte ni le protocole de déclustering ne peuvent contester : si le t
reste sous 1,645, la réclamation échoue quelle que soit la convention retenue.

Appliqué aux deux candidats cascade + à leurs bras de référence.
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


def ew_cluster(df: pd.DataFrame, label: str) -> dict:
    """Moyenne par événement, SE cluster-robuste sur L3, + bootstrap par bloc d'épisode."""
    if len(df) < 10:
        return {"label": label, "n": int(len(df))}
    x = df["fwd_4h"].to_numpy() * 1e4 - COST
    cl = df["L3"].to_numpy()
    m, se, t = vl.cluster_robust_t(x, cl)
    naive_se = x.std(ddof=1) / np.sqrt(len(x))
    boot = vl.block_bootstrap_mean(x, cl, n_resamples=5000)
    return {
        "label": label,
        "n_events": int(len(x)),
        "n_L3_episodes": int(len(pd.unique(cl))),
        "net14_event_weighted": round(float(m), 2),
        "se_naive": round(float(naive_se), 3),
        "se_cluster_robust": round(float(se), 3),
        "se_inflation_x": round(float(se / naive_se), 2),
        "t_naive": round(float(m / naive_se), 3),
        "t_cluster_robust": None if not np.isfinite(t) else round(float(t), 3),
        "bootstrap_ci95": [round(v, 2) for v in boot["ci95"]],
        "bootstrap_p05": round(boot["p05"], 2),
    }


def diff_ew_cluster(a: pd.DataFrame, b: pd.DataFrame, label: str) -> dict:
    """Différence de deux bras, pondérée par événement, SE clusterisée par épisode
    (régression de x sur une indicatrice de bras, SE cluster-robuste)."""
    df = pd.concat([a.assign(_arm=1), b.assign(_arm=0)])
    x = df["fwd_4h"].to_numpy() * 1e4 - COST
    d = df["_arm"].to_numpy().astype(float)
    cl = df["L3"].to_numpy()
    dbar, xbar = d.mean(), x.mean()
    dc = d - dbar
    denom = float((dc ** 2).sum())
    if denom <= 0:
        return {"label": label, "error": "no variation"}
    beta = float((dc * (x - xbar)).sum() / denom)
    resid = (x - xbar) - beta * dc
    s = pd.Series(dc * resid).groupby(pd.Series(cl)).sum().to_numpy()
    g = len(s)
    var = (g / (g - 1.0)) * float((s ** 2).sum()) / (denom ** 2)
    se = float(np.sqrt(var))
    return {
        "label": label,
        "difference_bps": round(beta, 2),
        "se_cluster_robust": round(se, 3),
        "t_cluster_robust": round(beta / se, 3) if se > 0 else None,
        "n_A": int(len(a)), "n_B": int(len(b)), "n_clusters": int(g),
    }


def main():
    a = ev2.add_declustering(ev2.population_A(since="2022-01-01"))
    res = {}

    # ── FAR_FROM_LOW ──────────────────────────────────────────────────────
    far = a["dist_low_24h"] >= 0.05
    res["FAR_FROM_LOW"] = {
        "far_live_0p05": ew_cluster(a[far], "far (seuil live 0.05)"),
        "near": ew_cluster(a[~far], "near"),
        "baseline_all_A": ew_cluster(a, "baseline inconditionnel"),
        "far_minus_near": diff_ew_cluster(a[far], a[~far], "far − near"),
        "far_minus_baseline": diff_ew_cluster(a[far], a, "far − baseline"),
    }

    # ── BTC_LEAD_ALT_CASCADE ──────────────────────────────────────────────
    flag = ev2.causal_shock_flag(a)
    u = a[flag.notna()].copy()
    u["shock"] = flag[flag.notna()]
    shock, nosh = u[u["shock"] == 1], u[u["shock"] == 0]
    res["BTC_LEAD_ALT_CASCADE"] = {
        "shock": ew_cluster(shock, "shock (q90 causal)"),
        "no_shock": ew_cluster(nosh, "no_shock"),
        "shock_minus_noshock": diff_ew_cluster(shock, nosh, "shock − no_shock"),
    }
    fdown = ev2.causal_shock_flag(a, signed="down")
    res["BTC_LEAD_ALT_CASCADE"]["down_shock"] = ew_cluster(a[fdown == 1], "down-shock")
    res["BTC_LEAD_ALT_CASCADE"]["down_minus_rest"] = diff_ew_cluster(
        a[fdown == 1], a[fdown == 0], "down-shock − reste")

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/event_weighted_cluster.json", "w") as f:
        json.dump(res, f, indent=2, default=str)

    for cand, block in res.items():
        print(f"\n=== {cand} ===")
        for k, v in block.items():
            if "net14_event_weighted" in v:
                print(f"  {k:22s} n={v['n_events']:6d} L3={v['n_L3_episodes']:5d} "
                      f"net14={v['net14_event_weighted']:8.2f} "
                      f"t_naive={v['t_naive']:6.2f} -> t_cluster={v['t_cluster_robust']:6.2f} "
                      f"(SE x{v['se_inflation_x']}) p05={v['bootstrap_p05']:8.2f}")
            elif "difference_bps" in v:
                print(f"  {k:22s} diff={v['difference_bps']:8.2f} "
                      f"t_cluster={v['t_cluster_robust']}")


if __name__ == "__main__":
    main()
