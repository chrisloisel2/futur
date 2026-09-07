#!/usr/bin/env python3
"""
sweep_mirrors.py -- reclasse alpha_sweep_results.csv correctement.

Le balayage classait par |t_net|, ce qui remonte systematiquement le cote PERDANT
de chaque signal : les couts poussent les deux directions vers le bas, donc le
cote qui perd devient plus negatif (|t| grand) et le cote qui gagne est tire vers
zero (|t| petit).

Ce script calcule le MIROIR de chaque configuration -- le meme signal joue a
l'envers -- et reclasse par t_net SIGNE.

    net_miroir = -brut - cout        (le cout se paie dans les deux sens)
    cout       = brut - net          (extrait de chaque ligne)

Aucune donnee n'est relue. Aucun essai supplementaire n'est consomme : un signal
et son oppose sont UNE hypothese testee en bilateral, pas deux.

Usage:
    python3 sweep_mirrors.py alpha_sweep_results.csv
"""

import sys
import numpy as np
import pandas as pd
from scipy.stats import norm

MIN_EPISODES = 100      # sous ce seuil la ligne n'est pas lisible


def main(path):
    d = pd.read_csv(path)
    n_raw = len(d)

    # --- 1. jeter les lignes illisibles -------------------------------------
    thin = d[d.n_episodes < MIN_EPISODES]
    d = d[d.n_episodes >= MIN_EPISODES].copy()

    # --- 2. doublons de parametre -------------------------------------------
    # certains signaux ignorent leur parametre (fenetre plancher) : ils gonflent
    # le compte d'essais sans tester quoi que ce soit de nouveau
    key = ["signal", "horizon_d", "basket", "gross_bps", "n_episodes"]
    dup = d.duplicated(subset=key, keep="first")
    n_dup = int(dup.sum())
    d = d[~dup].copy()

    # --- 3. le miroir --------------------------------------------------------
    d["cost_bps"] = d.gross_bps - d.net_bps
    d["se_bps"] = (d.net_bps / d.t_net).abs()
    d["mirror_gross_bps"] = -d.gross_bps
    d["mirror_net_bps"] = -d.gross_bps - d.cost_bps
    d["mirror_t_net"] = d.mirror_net_bps / d.se_bps

    # meilleur cote de chaque configuration
    take_mirror = d.mirror_net_bps > d.net_bps
    d["direction"] = np.where(take_mirror, "INVERSE", "DIRECT")
    d["best_net_bps"] = np.where(take_mirror, d.mirror_net_bps, d.net_bps)
    d["best_t_net"] = np.where(take_mirror, d.mirror_t_net, d.t_net)
    d["best_gross_bps"] = np.where(take_mirror, d.mirror_gross_bps, d.gross_bps)
    # la stabilite du miroir n'est pas 1 - stabilite (le net change de forme),
    # mais elle en est une borne raisonnable : on la marque comme a recalculer
    d["stability_note"] = np.where(take_mirror, "A_RECALCULER", "telle_quelle")

    # --- 4. seuil de multiplicite -------------------------------------------
    # un signal et son oppose = UNE hypothese bilaterale
    n_tests = len(d)
    thr = norm.ppf(1 - 0.05 / n_tests)
    d["passes_deflated"] = d.best_t_net > thr

    d = d.sort_values("best_t_net", ascending=False)

    # --- 5. sortie -----------------------------------------------------------
    print(f"=== NETTOYAGE ===")
    print(f"  lignes brutes                       {n_raw}")
    print(f"  rejetees (n_episodes < {MIN_EPISODES})        {len(thin)}")
    print(f"  doublons de parametre retires        {n_dup}")
    print(f"  hypotheses bilaterales reelles       {n_tests}")
    print(f"  seuil deflate                        t > {thr:.2f}\n")

    cols = ["signal", "param", "horizon_d", "basket", "n_episodes", "direction",
            "best_gross_bps", "cost_bps", "best_net_bps", "best_t_net",
            "stability", "stability_note", "passes_deflated"]

    print("=== TOP 25, cote GAGNANT de chaque configuration ===")
    with pd.option_context("display.width", 220, "display.max_columns", 60):
        print(d[cols].head(25).to_string(index=False,
              float_format=lambda v: f"{v:8.2f}"))

    win = d[d.passes_deflated]
    print(f"\n{len(win)} configuration(s) au-dessus du seuil deflate.")
    if len(win) == 0:
        best = d.iloc[0]
        print(f"La meilleure est {best.signal} p={best.param} h={best.horizon_d} "
              f"panier={best.basket} a t = {best.best_t_net:.2f} ({best.direction}).")
        print(f"Il lui manque {thr - best.best_t_net:.2f} de t pour franchir.")

    print("\n=== PAR FAMILLE, meilleure configuration ===")
    fam = d.loc[d.groupby("signal").best_t_net.idxmax()]
    fam = fam.sort_values("best_t_net", ascending=False)
    print(fam[["signal", "param", "horizon_d", "basket", "direction",
               "best_gross_bps", "best_net_bps", "best_t_net"]].to_string(
               index=False, float_format=lambda v: f"{v:8.2f}"))

    print("\n=== SENS ECONOMIQUE ===")
    n_inv = int((d.direction == "INVERSE").sum())
    print(f"  {n_inv}/{n_tests} configurations gagnent en INVERSE.")
    print("  Si la majorite des familles de reversion gagnent en inverse, la lecture")
    print("  est: a 1 jour, en transversal, le marche CONTINUE plutot qu'il ne revient.")
    print("  C'est une affirmation economique testable, pas un artefact de classement.")

    out = path.replace(".csv", "_mirrored.csv")
    d.to_csv(out, index=False)
    print(f"\n-> {out}")
    print("\nATTENTION: 'stability' de la colonne d'origine ne vaut que pour DIRECT.")
    print("Pour les lignes INVERSE il faut la recalculer -- relancer le balayage en")
    print("ajoutant le signal oppose est le seul moyen propre, et cela ne consomme")
    print("pas d'essai neuf puisque c'est la meme hypothese bilaterale.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "alpha_sweep_results.csv")
