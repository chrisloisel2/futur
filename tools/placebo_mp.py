#!/usr/bin/env python3
"""
placebo_mp.py -- le seuil qui ne suppose rien, calcule sur 16 coeurs.

Bonferroni sur Meff depend d'un estimateur : ratio de participation et Li-Ji
donnaient 35,8 et 841 sur la meme matrice, soit des seuils de 3,29 et 4,01.
L'ecart est trop grand pour trancher par convention.

Le placebo ne suppose rien. Pour chaque tirage on permute les symboles du panel
de rendements JOUR PAR JOUR : le lien signal->rendement est detruit, mais la
distribution transversale, le rendement de marche et la dispersion quotidienne
sont conserves exactement. On rejoue la grille ENTIERE et on retient max(t).
La distribution de ces maxima EST le seuil.

La preparation (neutralisation + rang) ne depend pas des rendements : elle est
calculee une fois par signal, et les 20 tirages passent dessus.
"""
import argparse, itertools, json, os, sys, time
from pathlib import Path
from multiprocessing import Pool
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import alpha_sweep_v4 as A

G = {}


def _sig(D, deriv_set):
    S = A.build_signals(D)
    if deriv_set:
        import deriv_signals
        extra = deriv_signals.build(D)
        S.update({k: v for k, v in extra.items() if k not in S})
        S = A._tie_guard(S)
    return S


def init(root, start, end, top_n, aum, adv_frac, cost_bps, winsor, neutrals,
         exec_lag, reps, seed, deriv_set=False):
    D = A.load_cache(root, pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"))
    univ = A.causal_universe(D, top_n, aum, 8, adv_frac)
    ret1 = (D["px_close"].shift(-1) / D["px_close"] - 1).clip(-winsor, winsor)
    COSTP = A.symbol_cost_bps(D, cost_bps)
    rng = np.random.default_rng(seed)
    base = ret1.to_numpy(dtype=float)
    RP = []
    for _ in range(reps):
        P = base.copy()
        for i in range(P.shape[0]):
            row = P[i]
            fin = np.where(np.isfinite(row))[0]
            if len(fin) > 2:
                row[fin] = row[rng.permutation(fin)]
        RP.append(P)
    G.update(D=D, univ=univ, RP=RP, exec_lag=exec_lag, neutrals=neutrals, reps=reps,
             deriv_set=deriv_set,
             Cnp=np.nan_to_num(COSTP.reindex_like(ret1).to_numpy(dtype=float),
                               nan=float(np.nanmedian(COSTP.to_numpy(dtype=float)))),
             SIG=_sig(D, G.get("deriv_set", False)))


def work(sname):
    D, univ, RP, Cnp = G["D"], G["univ"], G["RP"], G["Cnp"]
    reps, exec_lag = G["reps"], G["exec_lag"]
    raw = G["SIG"][sname]
    maxt = np.full(reps, -np.inf)
    HORIZ, BASK, HOLD, SMOOTH = [1, 3, 5, 10], [8, 12], [1, 5], [0, 5]
    rs = A.xrank(raw)
    for nname in G["neutrals"]:
        s_ = A.NEUT_FN(D, nname)(rs)
        for sm in SMOOTH:
            prep = A.prepare(s_, univ, sm)
            seen = set()
            for hh, kk, hd in itertools.product(HORIZ, BASK, HOLD):
                if hd > 1 and hh > 3:
                    continue
                if (hh * hd, kk) in seen:
                    continue
                seen.add((hh * hd, kk))
                lag = int(hh) * int(max(hd, 1)) + int(sm) + 2
                for ri in range(reps):
                    out = A.backtest(prep, RP[ri], kk, Cnp, hold=hd, smooth=sm,
                                     horizon=hh, exec_lag=exec_lag)
                    if out is None:
                        continue
                    g, cst, m = out
                    if int(int(m.sum()) / max(hh * max(hd, 1), 1)) < 100:
                        continue
                    nd_, ni_ = (g - cst)[m], (-g - cst)[m]
                    nb = ni_ if ni_.mean() > nd_.mean() else nd_
                    tv = A.newey_west_t(nb, lag)
                    if np.isfinite(tv) and tv > maxt[ri]:
                        maxt[ri] = tv
            del prep
        del s_
    return sname, maxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/qbee/futur")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--exec-lag", type=int, default=1)
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--top-n", type=int, default=120)
    ap.add_argument("--aum", type=float, default=200_000.0)
    ap.add_argument("--adv-frac", type=float, default=0.01)
    ap.add_argument("--cost-bps", type=float, default=14.0)
    ap.add_argument("--winsor", type=float, default=0.50)
    ap.add_argument("--neutrals", default="raw,mkt,full,mom,allf")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", default="v4")
    ap.add_argument("--deriv-set", action="store_true")
    a = ap.parse_args()
    neutrals = [x for x in a.neutrals.split(",") if x]

    D = A.load_cache(a.root, pd.Timestamp(a.start, tz="UTC"), pd.Timestamp(a.end, tz="UTC"))
    names = list(_sig(D, a.deriv_set).keys())
    del D
    print(f"=== placebo : {len(names)} signaux x {a.reps} tirages sur {a.workers} coeurs ===",
          flush=True)

    args = (a.root, a.start, a.end, a.top_n, a.aum, a.adv_frac, a.cost_bps,
            a.winsor, neutrals, a.exec_lag, a.reps, a.seed, a.deriv_set)
    t0 = time.time()
    maxt = np.full(a.reps, -np.inf)
    done = 0
    with Pool(a.workers, initializer=init, initargs=args) as pool:
        for sname, mt in pool.imap_unordered(work, names):
            maxt = np.maximum(maxt, mt)
            done += 1
            if done % 10 == 0:
                print(f"  {done}/{len(names)}  max(t) courant med {np.median(maxt):.2f} "
                      f"p95 {np.percentile(maxt, 95):.2f}  ({time.time()-t0:.0f}s)", flush=True)

    mt = maxt[np.isfinite(maxt)]
    res = dict(reps=int(len(mt)), max_t=list(map(float, mt)),
               q50=float(np.percentile(mt, 50)), q95=float(np.percentile(mt, 95)),
               q99=float(np.percentile(mt, 99)),
               start=a.start, end=a.end, exec_lag=a.exec_lag, neutrals=neutrals)
    outdir = Path(a.root) / "reports" / "edge_discovery" / f"sweep_{a.tag}"
    outdir.mkdir(parents=True, exist_ok=True)
    f = outdir / f"placebo_{a.start}_{a.end}.json"
    json.dump(res, open(f, "w"), indent=2)
    print(f"\n=== SEUIL EMPIRIQUE ({len(mt)} tirages, {time.time()-t0:.0f}s) ===")
    print(f"  max(t) sous le nul : median {res['q50']:.2f}  p95 {res['q95']:.2f}  p99 {res['q99']:.2f}")
    print("  tirages : " + " ".join(f"{v:.2f}" for v in sorted(mt)))
    print(f"-> {f}")


if __name__ == "__main__":
    main()
