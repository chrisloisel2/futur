#!/usr/bin/env python3
"""
deriv_signals.py -- l'espace des derives, cette fois vraiment fouille.

Le balayage v4 a montre que la seule chose qui survit sur ce marche est le
POSITIONNEMENT des comptes. Mais il ne testait qu'une poignee de constructions
naives sur une donnee qui venait d'etre branchee pour la premiere fois :
6 signaux d'open interest, 4 par ratio long/short, 8 de flux preneur.

Ici on prend les six series de derives au serieux :
    sum_open_interest, sum_open_interest_value,
    count_long_short_ratio           (tous les comptes -- la foule)
    count_toptrader_long_short_ratio (comptes du dernier decile -- corr 0.97 avec la foule)
    sum_toptrader_long_short_ratio   (POSITION des top traders -- corr 0.16, autre chose)
    sum_taker_long_short_vol_ratio   (flux agressif)
plus funding, mark_price, et le taker_buy des klines.

et on construit ce que la microstructure dit qui devrait marcher : desaccords
entre cohortes, acceleration du positionnement, capitulation, portage encombre,
divergence flux/prix, et dispersion transversale du positionnement.
"""
import numpy as np
import pandas as pd

EPS = 1e-12


def zs(df, w=60):
    return (df - df.rolling(w).mean()) / (df.rolling(w).std() + EPS)


def cmin(*xs):
    """Minimum terme a terme, en propageant les NaN (np.minimum, pas pandas.min
    qui les ignore et fabriquerait une conjonction a partir d'une seule jambe)."""
    out = xs[0].to_numpy(dtype=float)
    for x in xs[1:]:
        out = np.minimum(out, x.to_numpy(dtype=float))
    return pd.DataFrame(out, index=xs[0].index, columns=xs[0].columns)


def xr(df):
    return df.rank(axis=1, pct=True) - 0.5


def build(D):
    c, h, l = D["px_close"], D["px_high"], D["px_low"]
    qv = D["px_quote_volume"]
    r = c.pct_change()
    S = {}

    OI = D.get("met_oi")
    OIV = D.get("met_oi_usd")
    GA = D.get("met_glob_acct")     # la foule, par compte
    TA = D.get("met_tt_acct")       # top traders, par compte
    TP = D.get("met_tt_pos")        # top traders, par POSITION -- la serie orthogonale
    TK = D.get("met_taker_ls")      # flux agressif
    F = D.get("fund")
    MK = D.get("mark")
    TBQ = D.get("px_taker_buy_quote_volume")
    CNT = D.get("px_count")

    def ok(x):
        return x is not None and x.notna().sum().sum() > 5000

    # ---- 1. DESACCORDS ENTRE COHORTES -----------------------------------
    # Le seul couple reellement independant est (position des top traders,
    # comptes de la foule) : correlation transversale 0.16. Tout le reste est
    # la meme serie. C'est donc la que peut vivre une information.
    if ok(TP) and ok(GA):
        S["dis_pos_vs_crowd"] = xr(TP) - xr(GA)
        S["dis_pos_vs_crowd_z"] = zs(TP, 60) - zs(GA, 60)
        for n in (3, 7, 20):
            S[f"dis_pos_vs_crowd_chg{n}"] = xr(TP / TP.shift(n)) - xr(GA / GA.shift(n))
        # la foule bouge, les gros ne suivent pas
        S["crowd_moves_alone_7"] = -(xr(GA / GA.shift(7)) - xr(TP / TP.shift(7)))
    if ok(TP) and ok(TA):
        S["dis_pos_vs_acct"] = xr(TP) - xr(TA)          # taille vs nombre chez les memes gens
        S["dis_pos_vs_acct_chg7"] = xr(TP / TP.shift(7)) - xr(TA / TA.shift(7))
    if ok(TK) and ok(GA):
        S["dis_flow_vs_stock"] = xr(TK) - xr(GA)        # ce qui s'echange vs ce qui est detenu

    # ---- 2. ACCELERATION ET EXTREMES DU POSITIONNEMENT -------------------
    for tag, X in [("crowd", GA), ("ttpos", TP), ("ttacct", TA), ("takerls", TK)]:
        if not ok(X):
            continue
        S[f"pos_{tag}_x"] = -xr(X)
        S[f"pos_{tag}_z"] = -zs(X, 60)
        S[f"pos_{tag}_z20"] = -zs(X, 20)
        for n in (3, 7, 20):
            S[f"pos_{tag}_chg{n}"] = -xr(X / X.shift(n) - 1)
        S[f"pos_{tag}_accel"] = -(xr(X / X.shift(3) - 1) - xr(X.shift(3) / X.shift(10) - 1))
        # extreme absolu : percentile dans sa PROPRE histoire, pas transversal
        S[f"pos_{tag}_own_pct"] = -(X.rolling(250, min_periods=90).rank(pct=True) - 0.5)
        # dispersion : le positionnement est-il inhabituel par rapport a l'univers du jour
        S[f"pos_{tag}_vs_univ"] = -(X.sub(X.median(axis=1), axis=0)).div(
            X.std(axis=1).replace(0, np.nan) + EPS, axis=0)

    # ---- 3. OPEN INTEREST : construction vs liquidation ------------------
    if ok(OI):
        d1 = OI.pct_change()
        for n in (3, 7, 20):
            S[f"oi_chg{n}_x"] = -xr(OI / OI.shift(n) - 1)
        S["oi_z60"] = -zs(OI, 60)
        S["oi_own_pct"] = -(OI.rolling(250, min_periods=90).rank(pct=True) - 0.5)
        # les quatre quadrants (dOI, dPrix). C'est la lecture classique :
        #   OI+ P+ nouveaux longs | OI+ P- nouveaux shorts
        #   OI- P+ rachat de shorts | OI- P- liquidation de longs
        up, dn = (r > 0), (r < 0)
        oiu, oid = (d1 > 0), (d1 < 0)
        for lab, mask in [("newlong", oiu & up), ("newshort", oiu & dn),
                          ("shortcov", oid & up), ("longliq", oid & dn)]:
            for n in (5, 20):
                S[f"oiq_{lab}_{n}"] = mask.rolling(n).mean()
        # intensite signee, pas juste la frequence
        S["oi_flow_5"] = (np.sign(d1) * np.sign(r) * d1.abs()).rolling(5).mean()
        S["oi_flow_20"] = (np.sign(d1) * np.sign(r) * d1.abs()).rolling(20).mean()
        # OI qui monte sans que le prix bouge = accumulation silencieuse
        S["oi_silent_build_10"] = (d1.rolling(10).mean()
                                   / (r.abs().rolling(10).mean() + EPS))
        ic = D.get("met_oi_intraday_chg")
        if ok(ic):
            S["oi_intraday_x"] = -xr(ic)
            S["oi_intraday_5"] = -ic.rolling(5).mean()
    if ok(OIV) and ok(qv):
        S["oi_turnover"] = -(qv / (OIV + EPS))          # rotation du stock ouvert
        S["oi_turnover_z"] = -zs(qv / (OIV + EPS), 60)

    # ---- 4. FUNDING : le prix du portage, et son encombrement ------------
    if ok(F):
        S["fund_lvl_x"] = -xr(F)
        S["fund_z60"] = -zs(F, 60)
        for n in (3, 7, 20):
            S[f"fund_cum{n}_x"] = -xr(F.rolling(n).sum())
        S["fund_accel"] = -(F.rolling(3).mean() - F.rolling(20).mean())
        S["fund_vol_20"] = -F.rolling(20).std()          # instabilite du portage
        S["fund_persist_20"] = -(np.sign(F).rolling(20).mean())
        if ok(OI):
            # le portage encombre : cher ET massif ET en construction
            S["carry_crowded"] = -(xr(F) * xr(OI / OI.shift(7) - 1))
            S["carry_crowded_z"] = -(zs(F, 60).clip(-3, 3) * zs(OI, 60).clip(-3, 3))
            S["carry_paid_per_oi"] = -(F.rolling(7).sum() * OI / (OI.rolling(60).mean() + EPS))
        if ok(GA):
            # la foule est longue ET paie pour l'etre
            S["crowd_pays_7"] = -(xr(GA) * xr(F.rolling(7).sum()))
    if ok(MK) and ok(F):
        # RENOMME. Ces trois signaux s'appelaient `basis_*` alors qu'ils ne
        # mesurent PAS le basis : `mark / close - 1` est l'ecart du dernier
        # echange du perp a son PROPRE prix de marque, un artefact de
        # microstructure. Mesure sur les 40 273 cellules communes :
        #     corr de rang avec le vrai basis (perp/spot - 1) : -0,11
        #     corr de rang avec le funding : -0,058  (le vrai basis : +0,682)
        #     ecart-type 324,6 bps  (le vrai basis : 36,9 bps)
        # Le nom promettait une prime, le contenu livrait du bruit neuf fois
        # plus volatil. La famille basis n'avait donc jamais ete testee.
        mdev = (MK / c - 1.0)
        S["mark_dev_x"] = -xr(mdev)
        S["mark_dev_z60"] = -zs(mdev, 60)
        S["mark_dev_chg7"] = -xr(mdev - mdev.shift(7))

    # ---- 4bis. BASIS REEL : perp contre spot apparie --------------------
    # Hypotheses pre-ecrites : reports/loop/hypotheses/H-BASIS.md
    B = D.get("basis")
    if ok(B):
        # H-BASIS-1 : la prime de portage encombree. Le long a levier paie le
        # cash-and-carry ; on achete ce qui est bon marche a porter.
        S["basis_x"] = -xr(B)
        S["basis_z60"] = -zs(B, 60)
        S["basis_z20"] = -zs(B, 20)
        S["basis_own_pct"] = -(B.rolling(250, min_periods=90).rank(pct=True) - 0.5)
        S["basis_vs_univ"] = -(B.sub(B.median(axis=1), axis=0))

        # H-BASIS-3 : la compression du portage. Un desengagement du levier est
        # mecanique, pas informationnel : ce qui est ferme sous contrainte est
        # ferme trop bas. On achete ce qui vient de se comprimer.
        for n in (3, 7, 20):
            S[f"basis_chg{n}"] = -xr(B - B.shift(n))
        S["basis_accel"] = -(B.rolling(3).mean() - B.rolling(20).mean())
        S["basis_vol20"] = -B.rolling(20).std()

        # H-BASIS-2 : le desaccord entre ce qu'on PAIE et comment on est POSITIONNE.
        # Le basis dit le prix du levier, le ratio long/short dit qui le detient.
        if ok(GA):
            S["dis_basis_vs_crowd"] = xr(B) - xr(GA)
            S["dis_basis_vs_crowd_7"] = xr(B.rolling(7).mean()) - xr(GA.rolling(7).mean())
        if ok(TP):
            S["dis_basis_vs_ttpos"] = xr(B) - xr(TP)
        if ok(F):
            # basis et funding mesurent la meme prime : leur ecart est ce que
            # l'un dit et que l'autre ne dit pas.
            S["dis_basis_vs_fund"] = xr(B) - xr(F)
        if ok(OI):
            # portage cher ET stock en construction : l'encombrement, version basis
            S["basis_crowded"] = -(xr(B) * xr(OI / OI.shift(7) - 1))

        # ce que le spot debloque aussi : ou se fait le volume, perp ou spot ?
        SQV = D.get("spot_quote_volume")
        if ok(SQV):
            S["perp_spot_volratio"] = -xr(qv / (SQV + EPS))
            S["perp_spot_volratio_chg7"] = -xr((qv / (SQV + EPS)).pct_change(7))

    # ---- 5. FLUX PRENEUR ------------------------------------------------
    if ok(TBQ):
        imb = (2.0 * TBQ / (qv + EPS) - 1.0).clip(-1, 1)
        for n in (3, 10, 20, 60):
            S[f"tak_imb{n}_x"] = xr(imb.rolling(n).mean())
        S["tak_imb_z60"] = zs(imb, 60)
        S["tak_persist_20"] = np.sign(imb).rolling(20).mean()
        # divergence : le flux pousse, le prix ne suit pas -> le prix a tort
        for n in (5, 20):
            S[f"tak_div_{n}"] = xr(imb.rolling(n).mean()) - xr(c / c.shift(n) - 1)
        if ok(CNT):
            ats = qv / (CNT + EPS)
            S["ats_z60"] = zs(ats, 60)
            S["ats_x"] = xr(ats)
            S["ats_trend_20"] = ats.rolling(5).mean() / (ats.rolling(60).mean() + EPS)
            # gros tickets acheteurs : taille moyenne haute ET desequilibre acheteur
            S["big_buyers_10"] = xr(ats) * xr(imb.rolling(10).mean())
        if ok(OI):
            # le flux construit-il du stock, ou se contente-t-il de tourner ?
            S["flow_builds_oi_10"] = (xr(imb.rolling(10).mean())
                                      * xr(OI / OI.shift(10) - 1))
        if ok(GA):
            S["flow_vs_crowd_10"] = xr(imb.rolling(10).mean()) - xr(GA)

    # ---- 6. CAPITULATION ET SQUEEZE -------------------------------------
    if ok(OI) and ok(GA):
        d1 = OI.pct_change()
        vol20 = r.rolling(20).std()
        # BUG CORRIGE. Ces deux mecanismes etaient ecrits comme des PRODUITS de
        # termes signes :
        #     squeeze = (-A) * (-B) * (+C) = A*B*C
        #     capit   = (+A) * (-B) * (-C) = A*B*C
        # Retourner DEUX des trois facteurs ne change rien : les deux "mecanismes
        # opposes" etaient litteralement le meme signal, verifie identique au
        # 8e chiffre sur les 60 configurations partagees. Un produit ne peut pas
        # distinguer (-,-,+) de (+,-,-). Il faut une CONJONCTION.
        # `cmin` (minimum terme a terme des rangs centres) est une conjonction
        # continue : haute seulement si les trois jambes sont hautes, et sans
        # point de masse -- donc utilisable comme score transversal a deux jambes.
        drain = -xr(OI / OI.shift(5) - 1)          # l'open interest se vide
        r3 = xr(r.rolling(3).sum())
        S["squeeze_setup"] = cmin(-xr(GA), drain, r3)    # foule courte, prix monte
        S["long_capit"] = cmin(xr(GA), drain, -r3)       # foule longue, prix baisse
        # stress : mouvement extreme ET purge d'OI
        S["stress_purge_10"] = ((r.abs() > 2 * vol20) & (d1 < -0.02)).rolling(10).mean()

    # ---- 7. RETRAIT DES FACTEURS DE PRIX, EXPLICITE ---------------------
    # Un signal de positionnement correle a 0.47 au momentum 60j n'est pas une
    # decouverte tant qu'on n'a pas retire le momentum. On fournit donc les
    # versions deja orthogonalisees aux rendements passes, en transversal.
    if ok(GA):
        base = -xr(GA)
        for n in (20, 60):
            mm = xr(c / c.shift(n) - 1)
            num = (base * mm).sum(axis=1)
            den = (mm * mm).sum(axis=1).replace(0, np.nan)
            S[f"pos_crowd_ex_mom{n}"] = base.sub(mm.mul(num / den, axis=0))

    return {k: v for k, v in S.items() if v is not None}
