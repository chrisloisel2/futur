#!/usr/bin/env python3
"""
cribles.py -- les cribles 5, 6 et 7, que le harnais ne calculait pas.

Un finaliste doit passer les HUIT cribles. Le balayage en fournit quatre
directement (payeur ecrit avant, edge >= 2x cout, n_indep >= 116, >= 3 des 4
sous-periodes) et le crible 8 se mesure sur la matrice de correlation des PnL.
Restaient trois trous, et tant qu'ils sont ouverts AUCUN candidat ne peut etre
promu -- quel que soit son t.

  crible 5  positif sur le tiers le plus liquide ET le moins liquide
  crible 6  monotone dans l'intensite du signal, pas seulement au decile extreme
  crible 7  survit a +-30 % sur chaque parametre

Aucun de ces trois ne lit la fenetre scellee : ils se calculent sur la periode
d'exploration, comme le reste du balayage.

Usage :
    python3 tools/cribles.py --signal lsr_globacct_x --neutral mkt \
        --horizon 3 --basket 8 --hold 1 --smooth 0 --dir DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha_sweep_v4 as A          # noqa: E402
import deriv_signals as DS          # noqa: E402

EPS = 1e-12


# ---------------------------------------------------------------- socle commun

def contexte(root, start, end, top_n, aum, adv_frac, cost_bps, winsor, basket):
    D = A.load_cache(root, start, end)
    SIG = A.build_signals(D)
    extra = DS.build(D)
    SIG.update({k: v for k, v in extra.items() if k not in SIG})
    SIG = A._tie_guard(SIG)
    SIG, _ = A._rank_dedup(SIG)
    c = D["px_close"]
    ret1 = (c.shift(-1) / c - 1.0).clip(-winsor, winsor)
    univ = A.causal_universe(D, top_n, aum, basket, adv_frac)
    COSTP = A.symbol_cost_bps(D, cost_bps)
    cost_np = np.nan_to_num(COSTP.reindex_like(ret1).to_numpy(float), nan=cost_bps * 4)
    return D, SIG, ret1, univ, cost_np


def net_t(D, SIG, ret1, univ, cost_np, cfg, exec_lag=1):
    """(net quotidien en bps, t de Newey-West, n_jours). None si injouable."""
    if cfg["signal"] not in SIG:
        return None
    s = A.NEUT_FN(D, cfg["neutral"])(A.xrank(SIG[cfg["signal"]]))
    prep = A.prepare(s, univ, int(cfg["smooth"]))
    out = A.backtest(prep, ret1.to_numpy(float), int(cfg["basket"]), cost_np,
                     hold=int(cfg["hold"]), smooth=int(cfg["smooth"]),
                     horizon=int(cfg["horizon"]), exec_lag=exec_lag)
    if out is None:
        return None
    gross, cost, m = out
    net = (gross - cost) if cfg["dir"] == "DIR" else (-gross - cost)
    net = net[m]
    if len(net) < 120:
        return None
    lag = max(int(cfg["horizon"]) * int(cfg["hold"]) + int(cfg["smooth"]), 2)
    return float(np.mean(net)), float(A.newey_west_t(net, lag)), int(len(net))


# ----------------------------------------------------- crible 5 : liquidite

def crible5(D, SIG, ret1, univ, cost_np, cfg):
    """Positif sur le tiers le plus liquide ET le moins liquide.

    On decoupe l'univers JOUR PAR JOUR en tercile d'ADV 20 j, et on rejoue la
    strategie a l'interieur de chaque tercile. Un edge qui ne vit que dans un
    tercile est soit du bruit, soit un effet de liquidite deguise."""
    adv = D["px_quote_volume"].rolling(20).median().where(univ)
    q = adv.rank(axis=1, pct=True)
    out = {}
    # le panier doit rester jouable dans un tiers d'univers : on le reduit
    sub = dict(cfg)
    sub["basket"] = max(3, int(cfg["basket"]) // 2)
    for nom, masque in (("illiquide", q <= 1 / 3), ("median", (q > 1 / 3) & (q <= 2 / 3)),
                        ("liquide", q > 2 / 3)):
        r = net_t(D, SIG, ret1, univ & masque.fillna(False), cost_np, sub)
        out[nom] = None if r is None else {"net_bps": round(r[0], 3),
                                           "t": round(r[1], 3), "n_jours": r[2]}
    bornes = [out["illiquide"], out["liquide"]]
    passe = all(b is not None and b["net_bps"] > 0 for b in bornes)
    return {"crible": 5, "passe": passe, "panier_reduit_a": sub["basket"],
            "detail": out,
            "verdict": ("positif aux deux extremes de liquidite" if passe else
                        "l'edge ne survit pas aux deux extremes de liquidite")}


# ---------------------------------------------------- crible 6 : monotonie

def crible6(D, SIG, ret1, univ, cfg, n_buckets=5, exec_lag=1):
    """Monotone dans l'intensite du signal, pas seulement au decile extreme.

    On ne prend plus le haut et le bas : on decoupe le score neutralise en
    `n_buckets` tranches transversales et on mesure le rendement moyen en exces
    de l'univers dans chacune. Un vrai mecanisme est ordonne ; un artefact ne
    l'est qu'aux bords. Test : rho de Spearman entre le rang de tranche et le
    rendement, plus la fraction de pas consecutifs qui vont dans le bon sens."""
    s = A.NEUT_FN(D, cfg["neutral"])(A.xrank(SIG[cfg["signal"]]))
    s = s.where(univ)
    if int(cfg["smooth"]) > 1:
        s = s.ewm(span=int(cfg["smooth"]), min_periods=int(cfg["smooth"])).mean()
    q = s.rank(axis=1, pct=True)
    # horizon : rendement cumule sur `horizon` jours, execute a t+exec_lag
    h = max(int(cfg["horizon"]), 1)
    c = D["px_close"]
    fwd = (c.shift(-h - exec_lag) / c.shift(-exec_lag) - 1.0) / h
    fwd = fwd.where(univ)
    exces = fwd.sub(fwd.mean(axis=1), axis=0)
    moyennes = []
    for i in range(n_buckets):
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        m = (q > lo) & (q <= hi) if i else (q >= 0) & (q <= hi)
        moyennes.append(float(exces.where(m).stack().mean() * 1e4))
    if cfg["dir"] == "INV":
        moyennes = moyennes[::-1]
    r = np.arange(n_buckets, dtype=float)
    v = np.array(moyennes)
    fini = np.isfinite(v)
    rho = float(pd.Series(v[fini]).corr(pd.Series(r[fini]), method="spearman")) if fini.sum() > 2 else np.nan
    pas = np.diff(v[fini])
    bons = float(np.mean(pas > 0)) if len(pas) else np.nan
    passe = bool(np.isfinite(rho) and rho >= 0.8 and np.isfinite(bons) and bons >= 0.75)
    return {"crible": 6, "passe": passe,
            "rendement_par_tranche_bps": [round(x, 3) for x in moyennes],
            "spearman_tranche_vs_rendement": None if not np.isfinite(rho) else round(rho, 3),
            "fraction_pas_croissants": None if not np.isfinite(bons) else round(bons, 3),
            "seuils": {"spearman": 0.8, "pas_croissants": 0.75},
            "verdict": ("ordonne dans l'intensite" if passe else
                        "l'effet n'est pas monotone : concentre aux bords")}


# --------------------------------------------- crible 7 : robustesse +-30 %

def _p30(v, lo=1):
    """Les valeurs a +-30 %, arrondies, distinctes, bornees en bas."""
    return sorted({max(lo, int(round(v * 0.7))), int(v), max(lo, int(round(v * 1.3)))})


def crible7(root, start, end, cfg, base, exec_lag=1):
    """Survit a +-30 % sur CHAQUE parametre, un a la fois.

    On refait le contexte quand le parametre touche l'univers (top_n, adv_frac,
    aum, cout) et seulement le backtest sinon. Le crible echoue des qu'UNE
    variation rend le net negatif : la surface doit etre plate, pas pointue."""
    res, echecs = {}, []
    grilles = {
        "top_n": _p30(base["top_n"], 20),
        "basket": _p30(cfg["basket"], 3),
        "hold": _p30(cfg["hold"], 1),
        "horizon": _p30(cfg["horizon"], 1),
        "cost_bps": [round(base["cost_bps"] * f, 2) for f in (0.7, 1.0, 1.3)],
        "adv_frac": [round(base["adv_frac"] * f, 5) for f in (0.7, 1.0, 1.3)],
    }
    if int(cfg["smooth"]) > 0:
        grilles["smooth"] = _p30(cfg["smooth"], 1)

    for par, valeurs in grilles.items():
        res[par] = []
        for v in valeurs:
            c2, b2 = dict(cfg), dict(base)
            (b2 if par in ("top_n", "cost_bps", "adv_frac") else c2)[par] = v
            D, SIG, ret1, univ, cost_np = contexte(
                root, start, end, b2["top_n"], b2["aum"], b2["adv_frac"],
                b2["cost_bps"], b2["winsor"], int(c2["basket"]))
            r = net_t(D, SIG, ret1, univ, cost_np, c2, exec_lag)
            ligne = {"valeur": v, "net_bps": None if r is None else round(r[0], 3),
                     "t": None if r is None else round(r[1], 3)}
            res[par].append(ligne)
            if r is None or r[0] <= 0:
                echecs.append(f"{par}={v}")
    return {"crible": 7, "passe": len(echecs) == 0, "echecs": echecs,
            "detail": res,
            "verdict": ("net positif partout a +-30 %" if not echecs else
                        f"le net devient negatif ou injouable en : {', '.join(echecs)}")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/qbee/futur")
    ap.add_argument("--start", default="2021-12-01")
    ap.add_argument("--end", default="2024-06-30",
                    help="la fenetre scellee 2024-07/2026-06 est INTERDITE ici")
    ap.add_argument("--signal", required=True)
    ap.add_argument("--neutral", default="mkt")
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--basket", type=int, default=8)
    ap.add_argument("--hold", type=int, default=1)
    ap.add_argument("--smooth", type=int, default=0)
    ap.add_argument("--dir", default="DIR", choices=["DIR", "INV"])
    ap.add_argument("--top-n", type=int, default=120)
    ap.add_argument("--aum", type=float, default=200_000.0)
    ap.add_argument("--adv-frac", type=float, default=0.01)
    ap.add_argument("--cost-bps", type=float, default=14.0)
    ap.add_argument("--winsor", type=float, default=0.50)
    ap.add_argument("--exec-lag", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-7", action="store_true", help="le crible 7 refait ~18 contextes")
    a = ap.parse_args()

    if str(a.end) > "2024-06-30":
        raise SystemExit("REFUS : la fenetre scellee 2024-07-01/2026-06-30 ne se lit pas ici.")

    cfg = {"signal": a.signal, "neutral": a.neutral, "horizon": a.horizon,
           "basket": a.basket, "hold": a.hold, "smooth": a.smooth, "dir": a.dir}
    base = {"top_n": a.top_n, "aum": a.aum, "adv_frac": a.adv_frac,
            "cost_bps": a.cost_bps, "winsor": a.winsor}

    print(f"=== cribles 5-6-7 : {a.signal} | {a.neutral} | h{a.horizon} k{a.basket} "
          f"hd{a.hold} sm{a.smooth} {a.dir} ===")
    print(f"    fenetre {a.start} -> {a.end}  (exploration seule)\n", flush=True)

    D, SIG, ret1, univ, cost_np = contexte(a.root, a.start, a.end, a.top_n, a.aum,
                                           a.adv_frac, a.cost_bps, a.winsor, a.basket)
    ref = net_t(D, SIG, ret1, univ, cost_np, cfg, a.exec_lag)
    if ref is None:
        raise SystemExit(f"configuration injouable : {cfg}")
    print(f"reference : net {ref[0]:.2f} bps/j, t {ref[1]:.3f}, {ref[2]} jours\n", flush=True)

    r5 = crible5(D, SIG, ret1, univ, cost_np, cfg)
    print(f"[crible 5 liquidite ] {'PASSE' if r5['passe'] else 'ECHOUE'} -- {r5['verdict']}")
    for k, v in r5["detail"].items():
        print(f"      {k:10s} " + ("injouable" if v is None else
              f"net {v['net_bps']:+8.2f} bps  t {v['t']:+6.2f}  ({v['n_jours']} j)"))

    r6 = crible6(D, SIG, ret1, univ, cfg, exec_lag=a.exec_lag)
    print(f"\n[crible 6 monotonie ] {'PASSE' if r6['passe'] else 'ECHOUE'} -- {r6['verdict']}")
    print(f"      tranches (bps) : {r6['rendement_par_tranche_bps']}")
    print(f"      spearman {r6['spearman_tranche_vs_rendement']}  "
          f"pas croissants {r6['fraction_pas_croissants']}")

    r7 = None
    if not a.skip_7:
        print("\n[crible 7 +-30 %   ] calcul...", flush=True)
        r7 = crible7(a.root, a.start, a.end, cfg, base, a.exec_lag)
        print(f"[crible 7 +-30 %   ] {'PASSE' if r7['passe'] else 'ECHOUE'} -- {r7['verdict']}")
        for par, lignes in r7["detail"].items():
            vals = "  ".join(f"{l['valeur']}:{'x' if l['net_bps'] is None else format(l['net_bps'], '+.1f')}"
                             for l in lignes)
            print(f"      {par:10s} {vals}")

    res = {"config": cfg, "base": base, "fenetre": [a.start, a.end],
           "reference": {"net_bps": round(ref[0], 3), "t": round(ref[1], 3), "n_jours": ref[2]},
           "crible5": r5, "crible6": r6, "crible7": r7,
           "passe_les_trois": bool(r5["passe"] and r6["passe"] and (r7 is None or r7["passe"]))}
    print(f"\n==> les trois cribles : {'PASSES' if res['passe_les_trois'] else 'NON PASSES'}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=2, ensure_ascii=False))
        print(f"-> {a.out}")


if __name__ == "__main__":
    main()
