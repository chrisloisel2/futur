#!/usr/bin/env python3
"""
check_continuity.py -- la donnee manquante qui change le sens de la donnee restante.

Un trou signale comme trou est benin : on le voit. Un trou SILENCIEUX ne l'est
pas, parce que les barres survivantes se recollent et changent de sens. Le
2026-09-09, tout juillet 2026 manquait sur les 787 symboles de `um_klines_1d` :
le panel finissait bien au 2026-08-31, chaque fichier avait l'air complet, et un
rendement calcule du 2026-06-30 au 2026-08-01 aurait ete etiquete UN JOUR --
un rendement de 31 jours porte comme un rendement quotidien.

Ce test echoue si l'ecart entre deux barres consecutives depasse la cadence
attendue. A lancer AVANT toute reconstruction de cache.

    python3 tools/check_continuity.py data/derivatives_backfill/um_klines_1d
    python3 tools/check_continuity.py data/spot_backfill/spot_klines_1d --max-gap 1

Code de sortie 1 si un trou interne est trouve : utilisable en garde-fou.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


def trous(path, col, cadence_j, max_gap):
    """(n_barres, debut, fin, [(depuis, jusqu_a, ecart_j)])"""
    try:
        t = pq.read_table(path, columns=[col]).to_pandas()[col]
    except Exception as e:
        return None, None, None, [("LECTURE", str(e), -1)]
    t = pd.to_datetime(t, utc=True).drop_duplicates().sort_values()
    if len(t) < 2:
        return len(t), None, None, []
    d = t.diff().dt.total_seconds().div(86400.0)
    mauvais = d[d > max_gap * cadence_j + 1e-9]
    out = [(str(t.iloc[i - 1].date()), str(t.iloc[i].date()), float(d.iloc[i]))
           for i in [t.index.get_loc(j) for j in mauvais.index]]
    return len(t), t.min().date(), t.max().date(), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", help="repertoires de parquets a verifier")
    ap.add_argument("--col", default=None, help="colonne temporelle (auto : open_time/create_time/timestamp)")
    ap.add_argument("--cadence-jours", type=float, default=1.0)
    ap.add_argument("--max-gap", type=float, default=1.0,
                    help="ecart tolere, en multiples de la cadence (1 = aucun trou)")
    ap.add_argument("--limit", type=int, default=0, help="n'examiner que N fichiers (0 = tous)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    CANDIDATS = ["open_time", "create_time", "timestamp", "time"]
    rapport, total_fic, total_trous = {}, 0, 0
    for d in a.dirs:
        fs = sorted(glob.glob(os.path.join(d, "*.parquet")))
        if a.limit:
            fs = fs[: a.limit]
        if not fs:
            print(f"!! aucun parquet dans {d}")
            continue
        col = a.col
        if col is None:
            noms = pq.read_schema(fs[0]).names
            col = next((c for c in CANDIDATS if c in noms), noms[0])
        print(f"\n=== {d} : {len(fs)} fichiers, colonne '{col}' ===", flush=True)
        casses, pires = [], []
        for f in fs:
            n, deb, fin, tr = trous(f, col, a.cadence_jours, a.max_gap)
            total_fic += 1
            if tr:
                total_trous += 1
                sym = os.path.basename(f).split("_")[0]
                perdu = sum(g - a.cadence_jours for _, _, g in tr if g > 0)
                casses.append({"symbole": sym, "n_trous": len(tr), "jours_perdus": round(perdu, 1),
                               "debut": str(deb), "fin": str(fin), "exemples": tr[:3]})
                pires.append((perdu, sym, tr[0]))
        rapport[d] = {"n_fichiers": len(fs), "n_avec_trous": len(casses), "detail": casses}
        print(f"  fichiers avec trou interne : {len(casses)}/{len(fs)}")
        for perdu, sym, ex in sorted(pires, reverse=True)[:8]:
            print(f"    {sym:14s} {perdu:7.0f} j perdus   ex: {ex[0]} -> {ex[1]} ({ex[2]:.0f} j)")

    if a.out:
        Path(a.out).write_text(json.dumps(rapport, indent=2, ensure_ascii=False))
        print(f"\n-> {a.out}")
    print(f"\n==> {total_trous}/{total_fic} fichiers ont un trou interne")
    return 1 if total_trous else 0


if __name__ == "__main__":
    sys.exit(main())
