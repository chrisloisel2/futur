#!/usr/bin/env python3
"""
patch_v3.py -- corrige les trois bugs de alpha_sweep_v2.py.

    python3 patch_v3.py alpha_sweep_v2.py      # ecrit alpha_sweep_v3.py

CE QUI CHANGE

1. PORTEFEUILLES CHEVAUCHANTS CORRECTS (Jegadeesh-Titman).
   v2 appliquait un rendement forward de 10 jours chaque jour: recouvrement 10x,
   t gonfle de sqrt(10). v3 construit le portefeuille DETENU comme la moyenne des
   `horizon` portefeuilles cibles precedents, puis le multiplie par le rendement a
   1 JOUR. C'est la construction standard: memes positions, comptabilite coherente,
   et le rendement quotidien devient l'unite naturelle.

2. COUT COHERENT AVEC LA DETENTION.
   Le turnover est desormais celui du portefeuille DETENU, jour par jour, contre le
   rendement du meme jour. Un panier qui tourne integralement paie bien le cout
   demande. Le chevauchement reduit le turnover d'un facteur ~horizon -- c'est un
   vrai gain, pas un artefact comptable.

3. COUT PAR SYMBOLE.
   Un cout forfaitaire rend illisible tout signal qui selectionne l'illiquide --
   amihud le fait par construction. v3 module le cout par la liquidite relative:

       cout_i = cout_base * clip( (median_dollar_univers / dollar_i) ** 0.5, 1, 8 )

   Un symbole 100x moins liquide que la mediane paie 8x (plafonne). C'est grossier,
   mais infiniment moins faux qu'un forfait, et calibrable ensuite sur les
   profondeurs reelles mesurees par ton audit d'execution.

4. T-STAT NEWEY-WEST.
   Meme en rendement quotidien il reste de l'autocorrelation (le portefeuille detenu
   est lisse par construction). L'erreur-type est corrigee Newey-West avec un
   decalage de `horizon`, ce qui empeche le gonflement residuel.

5. EPISODES INDEPENDANTS RAPPORTES SEPAREMENT.
   `n_days` = nombre de jours. `n_indep` = n_days / horizon. La colonne qui compte
   pour juger la puissance est n_indep, jamais n_days.
"""

import sys, pathlib

BACKTEST_V3 = '''
def symbol_cost_bps(px, base_cost):
    """Cout par symbole, module par la liquidite relative. Causal (fenetre glissante)."""
    dollar = (px["vol"] * px["close"]).rolling(20).median()
    med = dollar.median(axis=1)
    ratio = dollar.div(med, axis=0).replace(0, np.nan)
    mult = (1.0 / ratio) ** 0.5
    return (base_cost * mult.clip(lower=1.0, upper=8.0)).fillna(base_cost * 8.0)


def backtest(score, univ, fwd1, k, cost_panel, hold=1, smooth=0, band=0.0, horizon=1):
    """
    Portefeuilles chevauchants corrects.

    fwd1       : rendement a UN JOUR (pas a l'horizon) -- c'est le correctif central
    horizon    : nombre de jours de detention; le portefeuille detenu est la moyenne
                 des `horizon` portefeuilles cibles precedents
    cost_panel : DataFrame de cout aller-retour EN BPS par (date, symbole)
    """
    s = score.where(univ)
    if smooth > 1:
        s = s.ewm(span=smooth, min_periods=smooth).mean()
    rk = s.rank(axis=1, method="first").to_numpy(dtype=float)
    cnt = s.notna().sum(axis=1).to_numpy(dtype=float)
    ok = cnt >= 2 * k + 2
    if ok.sum() < 120:
        return None

    T = np.zeros_like(rk)                       # portefeuille CIBLE du jour
    valid = np.isfinite(rk)
    T[valid & (rk <= k)] = -1.0 / k
    T[valid & (rk > (cnt[:, None] - k))] = 1.0 / k
    T[~ok] = 0.0

    # --- portefeuille DETENU : moyenne des `horizon` cibles precedentes ---
    span = max(int(horizon), 1) * max(int(hold), 1)
    if span > 1:
        W = pd.DataFrame(T).rolling(span, min_periods=1).mean().to_numpy()
    else:
        W = T.copy()

    if band > 0:
        H = np.zeros_like(W); cur = np.zeros(W.shape[1])
        for i in range(W.shape[0]):
            mv = np.abs(W[i] - cur) > band / k
            cur = np.where(mv, W[i], cur)
            H[i] = cur
        W = H

    F = fwd1.to_numpy(dtype=float)               # rendement a 1 JOUR
    W = np.where(np.isfinite(F), W, 0.0)
    Fz = np.nan_to_num(F)

    gross = (W * Fz).sum(axis=1) * 1e4           # bps par JOUR

    # --- cout : turnover du portefeuille DETENU x cout DU SYMBOLE ---
    dW = np.abs(np.diff(W, axis=0, prepend=np.zeros((1, W.shape[1]))))
    C = cost_panel.reindex_like(fwd1).to_numpy(dtype=float)
    C = np.nan_to_num(C, nan=np.nanmedian(C))
    cost = (dW * C / 2.0).sum(axis=1)            # bps par JOUR

    net = gross - cost
    m = ok & np.isfinite(gross)
    return gross[m], net[m], cost[m], int(m.sum())


def newey_west_t(x, lag):
    """t-stat robuste a l'autocorrelation. Le portefeuille detenu est lisse, donc
    le rendement quotidien est autocorrele meme sans recouvrement de label."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 60:
        return np.nan, n
    mu = x.mean()
    e = x - mu
    g0 = (e @ e) / n
    s = g0
    for L in range(1, min(int(lag), n - 1) + 1):
        gl = (e[L:] @ e[:-L]) / n
        s += 2.0 * (1.0 - L / (lag + 1.0)) * gl
    s = max(s, 1e-18)
    return mu / np.sqrt(s / n), n
'''


def main(src):
    p = pathlib.Path(src)
    s = p.read_text()

    a = s.index("def backtest(")
    b = s.index("def stat(")
    s = s[:a] + BACKTEST_V3.strip() + "\n\n\n" + s[b:]

    # fwd a 1 jour au lieu de fwd a l'horizon
    s = s.replace('FWD = {hh: (c.shift(-hh) / c - 1.0) for hh in HORIZ}',
                  'FWD1 = c.shift(-1) / c - 1.0\n'
                  '    COSTP = symbol_cost_bps(px, a.cost_bps)\n'
                  '    print(f"  cout par symbole : median {COSTP.median(axis=1).median():.1f} bps, "\n'
                  '          f"p95 {COSTP.stack().quantile(0.95):.1f} bps")')

    s = s.replace(
        'out = backtest(s, univ, FWD[hh], kk, a.cost_bps, hold=hd, smooth=sm)',
        'out = backtest(s, univ, FWD1, kk, COSTP, hold=hd, smooth=sm, horizon=hh)')
    s = s.replace(
        'out = backtest(s2, univ, FWD[int(rw.horizon)], int(rw.basket),\n'
        '                                   a.cost_bps, hold=int(rw.hold), smooth=int(rw.smooth))',
        'out = backtest(s2, univ, FWD1, int(rw.basket), COSTP,\n'
        '                                   hold=int(rw.hold), smooth=int(rw.smooth),\n'
        '                                   horizon=int(rw.horizon))')

    # exploitation des sorties: 4-uple + Newey-West + n_indep
    s = s.replace('''                g, n_, idx = out
                sg, sn = stat(g), stat(n_)
                if sg is None or sn is None:
                    continue
                key = f"{sname}|{nname}|h{hh}|k{kk}|hd{hd}|sm{sm}"
                pnls[key] = n_
                rows.append(dict(signal=sname, neutral=nname, horizon=hh, basket=kk,
                                 hold=hd, smooth=sm, state="-", regime="-",
                                 n=sn[0], gross=sg[1], cost=sg[1] - sn[1],
                                 net=sn[1], t=sn[3], stab=sub_stability(n_)))''',
'''                g, n_, cst, nd = out
                tnw, _ = newey_west_t(n_, lag=max(hh, 2))
                if not np.isfinite(tnw):
                    continue
                key = f"{sname}|{nname}|h{hh}|k{kk}|hd{hd}|sm{sm}"
                pnls[key] = n_
                rows.append(dict(signal=sname, neutral=nname, horizon=hh, basket=kk,
                                 hold=hd, smooth=sm, state="-", regime="-",
                                 n_days=nd, n_indep=int(nd / max(hh, 1)),
                                 gross_dly=g.mean(), cost_dly=cst.mean(),
                                 net_dly=n_.mean(), net_per_hold=n_.mean() * hh,
                                 t=tnw, stab=sub_stability(n_)))''')

    s = s.replace('''                    g, n_, _ = out
                    sg, sn = stat(g), stat(n_)
                    if sg is None or sn is None:
                        continue
                    pnls[f"{rw.signal}|{stn}|{lab}"] = n_
                    rows.append(dict(signal=rw.signal, neutral=rw.neutral,
                                     horizon=rw.horizon, basket=rw.basket,
                                     hold=rw.hold, smooth=rw.smooth,
                                     state=stn, regime=lab,
                                     n=sn[0], gross=sg[1], cost=sg[1] - sn[1],
                                     net=sn[1], t=sn[3], stab=sub_stability(n_)))''',
'''                    g, n_, cst, nd = out
                    tnw, _ = newey_west_t(n_, lag=max(int(rw.horizon), 2))
                    if not np.isfinite(tnw):
                        continue
                    pnls[f"{rw.signal}|{stn}|{lab}"] = n_
                    rows.append(dict(signal=rw.signal, neutral=rw.neutral,
                                     horizon=rw.horizon, basket=rw.basket,
                                     hold=rw.hold, smooth=rw.smooth,
                                     state=stn, regime=lab,
                                     n_days=nd, n_indep=int(nd / max(int(rw.horizon), 1)),
                                     gross_dly=g.mean(), cost_dly=cst.mean(),
                                     net_dly=n_.mean(), net_per_hold=n_.mean() * int(rw.horizon),
                                     t=tnw, stab=sub_stability(n_)))''')

    # cote gagnant sur la nouvelle comptabilite
    s = s.replace('''    flip = res.net < (-res.gross - res.cost)
    res["dir"] = np.where(flip, "INV", "DIR")
    res["net_best"] = np.where(flip, -res.gross - res.cost, res.net)
    se = (res.net / res.t).abs().replace(0, np.nan)
    res["t_best"] = np.where(flip, res.net_best / se, res.t)
    res["stab_best"] = np.where(flip, np.nan, res.stab)''',
'''    flip = res.net_dly < (-res.gross_dly - res.cost_dly)
    res["dir"] = np.where(flip, "INV", "DIR")
    res["net_best"] = np.where(flip, -res.gross_dly - res.cost_dly, res.net_dly)
    se = (res.net_dly / res.t).abs().replace(0, np.nan)
    res["t_best"] = np.where(flip, res.net_best / se, res.t)
    res["net_hold_best"] = res.net_best * res.horizon
    res["stab_best"] = np.where(flip, np.nan, res.stab)
    res = res[res.n_indep >= 100]''')

    s = s.replace('''    cols = ["signal", "neutral", "horizon", "basket", "hold", "smooth", "state",
            "regime", "dir", "n", "gross", "cost", "net_best", "t_best", "stab_best", "passes"]''',
'''    cols = ["signal", "neutral", "horizon", "basket", "hold", "smooth", "state",
            "regime", "dir", "n_days", "n_indep", "gross_dly", "cost_dly",
            "net_best", "net_hold_best", "t_best", "stab_best", "passes"]''')

    out = src.replace("v2", "v3")
    pathlib.Path(out).write_text(s)
    print(f"ecrit -> {out}")
    print("\nCE QUI A CHANGE:")
    print("  * rendement a 1 JOUR + portefeuille detenu chevauchant (fin du recouvrement)")
    print("  * turnover et cout du portefeuille DETENU, coherents avec le rendement")
    print("  * cout PAR SYMBOLE, module par la liquidite (1x a 8x le cout de base)")
    print("  * t-stat Newey-West, decalage = horizon")
    print("  * n_indep rapporte a cote de n_days; les lignes sous 100 sont jetees")
    print("\nAttends-toi a ce que les t s'effondrent d'un facteur ~3 sur les horizons longs,")
    print("et a ce que la famille amihud disparaisse. C'est le but.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "alpha_sweep_v2.py")
