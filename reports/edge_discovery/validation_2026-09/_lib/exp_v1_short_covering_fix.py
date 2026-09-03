"""SHORT_COVERING — reprise de l'inférence avec une unité L3 non dégénérée.

ÉCART AU PRÉENREGISTREMENT, déclaré et justifié. Le prereg fixait
L3 = « épisode cross-symbole chaîné, gap < 4 h ». Cette unité convient à une population
d'ÉVÉNEMENTS RARES (cascades : 26 750 événements → 2 926 épisodes). Elle est DÉGÉNÉRÉE sur
une population de BARRES DENSES : 48 symboles à la maille horaire produisent presque toujours
un signal dans les 4 h, si bien que 22 330 signaux se chaînent en **5 épisodes**. Une SE
cluster-robuste sur G = 5 groupes n'a aucune validité asymptotique.

Ce n'est pas un ajustement de paramètre du signal (aucun seuil ne bouge) mais la correction
d'une unité d'inférence inadaptée à la forme de la population. Les deux unités de remplacement
sont fixées AVANT de relire les résultats, et les deux sont reportées :
  L3' = jour calendaire UTC (tous symboles)   — l'analogue du L2 de la famille événementielle
  L3'' = semaine calendaire UTC               — plus conservateur, absorbe l'autocorrélation
                                                intra-semaine des régimes de squeeze
Le verdict exige que le contraste A − B tienne sur les DEUX.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl                 # noqa: E402
import exp_v1_short_covering as sc          # noqa: E402

OUT = "/home/qbee/futur/reports/edge_discovery/validation_2026-09/_lib/out"
COST, STRESS = 14.0, 28.0


def diff_clustered(d: pd.DataFrame, arm: np.ndarray, cl: np.ndarray, fwd: str) -> dict:
    """A − B pondéré par événement, SE cluster-robuste sur `cl` (régression sur indicatrice)."""
    x = d[fwd].to_numpy() * 1e4
    a = arm.astype(float)
    abar, xbar = a.mean(), x.mean()
    ac = a - abar
    den = float((ac ** 2).sum())
    beta = float((ac * (x - xbar)).sum() / den)
    resid = (x - xbar) - beta * ac
    s = pd.Series(ac * resid).groupby(pd.Series(cl)).sum().to_numpy()
    g = len(s)
    var = (g / (g - 1.0)) * float((s ** 2).sum()) / (den ** 2)
    se = float(np.sqrt(var))
    return {"difference_bps": round(beta, 2), "se": round(se, 3),
            "t_cluster_robust": round(beta / se, 3) if se > 0 else None,
            "n_clusters": int(g)}


def arm_stats(d: pd.DataFrame, arm: np.ndarray, cl: np.ndarray, fwd: str,
              cost: float = COST) -> dict:
    x = d.loc[arm, fwd].to_numpy() * 1e4 - cost
    c = cl[arm]
    m, se, t = vl.cluster_robust_t(x, c)
    boot = vl.block_bootstrap_mean(x, c, n_resamples=5000)
    return {"n": int(len(x)), "n_clusters": int(len(pd.unique(c))),
            "net14": round(float(m), 2), "net28": round(float(x.mean() - (STRESS - COST)), 2),
            "t_cluster_robust": None if not np.isfinite(t) else round(float(t), 3),
            "bootstrap_p05": round(boot["p05"], 2)}


def main():
    raw = sc.build_hourly()
    df = sc.add_features(raw)
    df = df[df["ts"] >= pd.Timestamp("2022-01-01", tz="UTC")]
    df = df[df["px_p"].notna() & df["oi_p"].notna() & df["fwd_4h"].notna()].reset_index(drop=True)
    print(f"population: {len(df)} barres, {df.symbol.nunique()} symboles", flush=True)

    ts = pd.to_datetime(df["ts"], utc=True)
    units = {
        "L3_day": ts.dt.floor("D").astype("int64").to_numpy(),
        "L3_week": ts.dt.to_period("W").astype(str).to_numpy(),
        "L3_month": (ts.dt.year * 100 + ts.dt.month).to_numpy(),
    }
    A = ((df["px_p"] >= 0.90) & (df["oi_p"] <= 0.10)).to_numpy()

    res = {
        "_deviation_from_prereg": (
            "L3 préenregistré (épisode chaîné gap<4h) dégénéré sur ce panel dense : "
            "22330 signaux -> 5 clusters. Remplacé par jour/semaine/mois calendaires, "
            "fixés avant relecture des résultats."),
        "_population": {"n_bars": int(len(df)), "n_symbols": int(df.symbol.nunique()),
                        "n_arm_A": int(A.sum()),
                        "arm_A_share": round(float(A.mean()), 5),
                        "window": [str(ts.min()), str(ts.max())]},
    }

    print(f"\n{'unité':10s} {'nA':>7s} {'clusters':>9s} | {'A net14':>9s} {'t_A':>7s} "
          f"{'p05':>8s} | {'A−B':>8s} {'t_A−B':>7s}")
    for name, cl in units.items():
        st = arm_stats(df, A, cl, "fwd_4h")
        di = diff_clustered(df, A, cl, "fwd_4h")
        res[name] = {"arm_A": st, "A_minus_B": di}
        print(f"{name:10s} {st['n']:7d} {st['n_clusters']:9d} | {st['net14']:9.2f} "
              f"{str(st['t_cluster_robust']):>7s} {st['bootstrap_p05']:8.2f} | "
              f"{di['difference_bps']:8.2f} {str(di['t_cluster_robust']):>7s}")

    # année par année sur l'excess A − B, cluster = jour
    print("\nA − B par année (cluster = jour) :")
    by_year = {}
    for y, g in df.groupby(ts.dt.year):
        idx = g.index.to_numpy()
        a = A[idx]
        if a.sum() < 50:
            continue
        d = diff_clustered(g.reset_index(drop=True), a,
                           pd.to_datetime(g["ts"], utc=True).dt.floor("D").astype("int64").to_numpy(),
                           "fwd_4h")
        by_year[int(y)] = d
        print(f"  {y}: A−B={d['difference_bps']:8.2f} t={d['t_cluster_robust']} "
              f"(clusters={d['n_clusters']})")
    res["A_minus_B_by_year"] = by_year
    res["n_years_positive"] = sum(1 for v in by_year.values() if v["difference_bps"] > 0)

    # bras A brut vs zéro sur les mêmes unités (critère 4 du prereg)
    res["arm_A_vs_zero_note"] = (
        "Le bras A seul est à net14 %.2f (cluster=jour, t=%s) : le contraste A−B est positif "
        "mais le PRODUIT lui-même ne bat pas zéro de façon significative." % (
            res["L3_day"]["arm_A"]["net14"], res["L3_day"]["arm_A"]["t_cluster_robust"]))

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/v1_short_covering_fixed.json", "w") as f:
        json.dump(res, f, indent=2, default=str)
    print("\nécrit:", f"{OUT}/v1_short_covering_fixed.json")


if __name__ == "__main__":
    main()
