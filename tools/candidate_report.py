#!/usr/bin/env python3
"""
candidate_report.py -- ce qu'un t-stat ne dit pas.

Prend une configuration (signal, neutralisation, horizon, panier, hold, smooth)
et rend la comptabilite complete : courbe d'equite, rendements MENSUELS, Sharpe,
perte maximale, rotation, exposition, decoupage par sous-periode, et le rendement
mensuel net attendu aux deux conventions d'exposition brute.

C'est la seule sortie qui repond a "3 a 4 % par mois" ou non.
"""
import argparse, json, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from alpha_sweep_v4 import (load_cache, causal_universe, symbol_cost_bps,
                            build_signals, xrank, prepare, backtest,
                            newey_west_t, NEUT_FN)


def curve(D, SIG, univ, ret1, COSTP, cfg, exec_lag, aum):
    s = NEUT_FN(D, cfg["neutral"])(xrank(SIG[cfg["signal"]]))
    prep = prepare(s, univ, cfg["smooth"])
    R1np = ret1.to_numpy(dtype=float)
    Cnp = np.nan_to_num(COSTP.reindex_like(ret1).to_numpy(dtype=float),
                        nan=float(np.nanmedian(COSTP.to_numpy(dtype=float))))
    out = backtest(prep, R1np, cfg["basket"], Cnp, hold=cfg["hold"],
                   smooth=cfg["smooth"], horizon=cfg["horizon"], exec_lag=exec_lag)
    if out is None:
        return None
    gross, cost, m = out
    sgn = -1.0 if cfg.get("dir") == "INV" else 1.0
    net = sgn * gross - cost
    idx = ret1.index
    return pd.DataFrame({"gross": sgn * gross, "cost": cost, "net": net},
                        index=idx)[m]


def describe(df, label, gross_exposure=2.0):
    """gross_exposure : les poids sont +-1/k sur k noms de chaque cote, donc le
    brut vaut 2x le capital. A 1x de brut, tout est divise par deux."""
    n = df["net"] / gross_exposure / 1e4          # rendement quotidien sur capital
    eq = (1 + n).cumprod()
    ann = (eq.iloc[-1] ** (365.25 / ((df.index[-1] - df.index[0]).days)) - 1)
    vol = n.std() * np.sqrt(365.25)
    sharpe = ann / vol if vol > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    mo = n.groupby([df.index.year, df.index.month]).apply(lambda x: (1 + x).prod() - 1)
    print(f"\n=== {label} (brut = {gross_exposure:.0f}x le capital) ===")
    print(f"  jours                  {len(n)}   [{df.index[0].date()} -> {df.index[-1].date()}]")
    print(f"  rendement annualise    {ann:+7.1%}")
    print(f"  volatilite annualisee  {vol:7.1%}")
    print(f"  Sharpe                 {sharpe:7.2f}")
    print(f"  perte maximale         {dd:+7.1%}")
    print(f"  rendement mensuel      median {mo.median():+.2%}   moyen {mo.mean():+.2%}")
    print(f"  mois positifs          {(mo > 0).sum()}/{len(mo)}  ({(mo>0).mean():.0%})")
    print(f"  pire mois              {mo.min():+.2%}    meilleur {mo.max():+.2%}")
    print(f"  brut/cout quotidien    {df.gross.mean():+.2f} / {df.cost.mean():.2f} bps  "
          f"-> net {df.net.mean():+.2f} bps  (cout = {df.cost.mean()/max(abs(df.gross.mean()),1e-9):.0%} du brut)")
    return mo, ann, sharpe, dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/qbee/futur")
    ap.add_argument("--prereg", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--exec-lag", type=int, default=1)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    pr = json.load(open(a.prereg))
    start, end = pd.Timestamp(a.start, tz="UTC"), pd.Timestamp(a.end, tz="UTC")
    D = load_cache(a.root, start, end)
    univ = causal_universe(D, pr["top_n"], pr["aum"], 8, pr["adv_frac"])
    ret1 = (D["px_close"].shift(-1) / D["px_close"] - 1).clip(-pr["winsor"], pr["winsor"])
    COSTP = symbol_cost_bps(D, pr["cost_bps"])
    SIG = build_signals(D)
    try:
        import deriv_signals
        from alpha_sweep_v4 import _tie_guard
        SIG.update({k: v for k, v in deriv_signals.build(D).items() if k not in SIG})
        SIG = _tie_guard(SIG)
    except Exception as e:
        print(f"  (bibliotheque derives non chargee : {e})")

    monthlies = {}
    for cfg in pr["configs"][:a.top]:
        if cfg["signal"] not in SIG:
            print(f"  {cfg['signal']} absent sur cette fenetre"); continue
        df = curve(D, SIG, univ, ret1, COSTP, cfg, a.exec_lag, pr["aum"])
        if df is None or len(df) < 100:
            print(f"  {cfg['signal']} : trop peu de jours"); continue
        lab = f"{cfg['signal']} | {cfg['neutral']} | h{cfg['horizon']} k{cfg['basket']} " \
              f"hd{cfg['hold']} sm{cfg['smooth']} {cfg['dir']}  {a.label}"
        mo, ann, sh, dd = describe(df, lab, gross_exposure=2.0)
        lag = cfg["horizon"] * max(cfg["hold"], 1) + cfg["smooth"] + 2
        print(f"  t de Newey-West (lag {lag}) : {newey_west_t(df.net.values, lag):+.2f}")
        monthlies[lab] = mo
        print("  mensuels : " + "  ".join(f"{v:+.1%}" for v in mo.values))

    if len(monthlies) > 1:
        M = pd.DataFrame(monthlies)
        print("\n=== correlation des rendements mensuels entre candidats ===")
        print(M.corr().round(2).to_string())
        eq = M.mean(axis=1)
        print(f"\n=== panier equipondere des {len(monthlies)} candidats ===")
        print(f"  mensuel median {eq.median():+.2%}  moyen {eq.mean():+.2%}  "
              f"mois positifs {(eq>0).sum()}/{len(eq)}")


if __name__ == "__main__":
    main()
