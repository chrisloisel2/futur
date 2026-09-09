#!/usr/bin/env python3
"""
alpha_sweep_v4.py -- le meme balayage, avec le harnais reparé.

Ce que v3 mesurait mal, et qui portait la conclusion :

  1. SOURCE. v3 lisait data/enriched (50 symboles) et open_interest_hist
     (30 jours glissants d'API REST, aucun chevauchement avec la fenetre).
     v4 lit um_klines_1d (696 symboles, taker_buy REEL) et
     binance_vision_metrics (OI + 4 ratios long/short, 5 min depuis 2021-12).
     La famille derives n'avait jamais ete testee.

  2. EXECUTION. v3 formait le score sur close(t) et encaissait
     close(t+1)/close(t). Le signal et l'execution partagent la meme barre.
     v4 expose --exec-lag ; par defaut 1 (score t, execution close(t+1),
     rendement close(t+2)/close(t+1)). Le mode 0 reste disponible POUR
     MESURER combien de l'edge vient de la barre partagee.

  3. CAPACITE. v3 renchérissait l'illiquide (x8 au plus). Un ordre de 12 500 $
     sur un carnet de 53 $ n'est pas cher, il est infaisable. v4 EXCLUT le
     symbole dont la position depasse --adv-frac de son ADV 20j.

  4. DEUX COTES. v3 choisissait le cote gagnant a posteriori puis le comparait
     a un seuil UNILATERAL, et reconstruisait le t du cote INV a partir de la
     SE de la serie NON retournee. v4 derive la serie retournee exactement
     (-gross - cost, terme a terme), calcule son t et sa stabilite pour de
     vrai, et deflate contre un seuil BILATERAL.

  5. AUTOCORRELATION. v3 passait lag=max(horizon,2) a Newey-West alors que le
     portefeuille detenu est la moyenne de horizon*hold cibles plus un lissage
     ewm de span smooth. v4 passe horizon*hold + smooth + 2.

  6. Meff. v3 prenait list(pnls)[:200] -- l'ordre d'insertion, soit les ~8
     premiers signaux sur 58. v4 utilise TOUTES les configurations, alignees
     sur l'index complet (jour non traite = PnL 0, ce qui est exact), et
     rapporte en plus un seuil EMPIRIQUE par placebo, qui ne suppose rien.

  7. HORS ECHANTILLON. v3 n'en avait pas. v4 a deux phases : `search` ecrit un
     pre-enregistrement, `confirm` ne rejoue QUE ce pre-enregistrement sur une
     fenetre jamais touchee, sans reglage.

Usage :
  python3 tools/alpha_sweep_v4.py --phase search  --start 2021-12-01 --end 2024-06-30
  python3 tools/alpha_sweep_v4.py --phase placebo --start 2021-12-01 --end 2024-06-30 --reps 20
  python3 tools/alpha_sweep_v4.py --phase confirm --prereg reports/.../prereg.json \
                                  --start 2024-07-01 --end 2026-06-30
"""

import argparse, hashlib, itertools, json, os, sys, time, warnings
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import norm

warnings.filterwarnings("ignore")
EPS = 1e-12

# La fenetre d'EXPLORATION : tout confirm en dehors est un regard SCELLE, qui
# exige un pre-enregistrement pousse sur le remote (cf. tools/look_ledger.py).
EXPLORATION = ("2021-12-01", "2024-06-30")


# =====================================================================  DONNEES

def load_cache(root, start, end):
    cdir = Path(root) / "data" / "_cache"
    meta = json.load(open(cdir / "panel_daily_meta.json"))
    z = np.load(cdir / "panel_daily.npz")
    idx = pd.DatetimeIndex(pd.to_datetime(meta["index"], utc=True))
    syms = meta["symbols"]
    m = (idx >= start) & (idx <= end)
    out = {}
    for k in meta["fields"]:
        out[k] = pd.DataFrame(z[k][m], index=idx[m], columns=syms).astype(float)
    # Panel SPOT apparie, dans un fichier separe : `panel_daily.npz` porte des
    # donnees deja jugees et son empreinte est au ledger. Optionnel : le balayage
    # tourne sans, la famille basis est simplement absente.
    sp = cdir / "panel_spot_daily.npz"
    if sp.exists():
        smeta = json.load(open(cdir / "panel_spot_daily_meta.json"))
        if smeta["symbols"] == syms and len(smeta["index"]) == len(idx):
            zs_ = np.load(sp)
            for k in smeta["fields"]:
                out[k] = pd.DataFrame(zs_[k][m], index=idx[m], columns=syms).astype(float)
        else:
            print("  !! panel spot desaligne du panel perp -- famille basis ignoree")
    return out


# ================================================================  OUTILS PANEL

def zs(df, w=60):
    return (df - df.rolling(w).mean()) / (df.rolling(w).std() + EPS)


def xrank(df):
    """Rang transversal centre sur [-0.5, 0.5]. Robuste aux valeurs extremes."""
    r = df.rank(axis=1, pct=True)
    return r - 0.5


def neutralize(sig, factors):
    """Retire la projection ligne par ligne. Le signal est deja rangé, donc la
    regression n'est pas dominee par une queue."""
    F = [f.reindex_like(sig).to_numpy(dtype=float) for f in factors]
    S = sig.to_numpy(dtype=float)
    R = np.full_like(S, np.nan)
    for i in range(S.shape[0]):
        y = S[i]
        m = np.isfinite(y)
        nm = int(m.sum())
        if nm < 12:
            continue
        cols = [np.ones(nm)]
        for f in F:
            x = f[i][m]
            if np.isfinite(x).sum() < nm * 0.8:
                continue
            cols.append(np.nan_to_num(x, nan=np.nanmean(x[np.isfinite(x)]) if np.isfinite(x).any() else 0.0))
        X = np.vstack(cols).T
        yy = y[m]
        try:
            beta, *_ = np.linalg.lstsq(X, yy, rcond=None)
            R[i, m] = yy - X @ beta
        except Exception:
            R[i, m] = yy
    return pd.DataFrame(R, index=sig.index, columns=sig.columns)


def parkinson(h, l, w):
    return (np.log((h / l).clip(lower=1 + EPS)) ** 2).rolling(w).mean() ** 0.5


def garman_klass(o, h, l, c, w):
    a = 0.5 * np.log((h / l).clip(lower=1 + EPS)) ** 2
    b = (2 * np.log(2) - 1) * np.log((c / o).clip(lower=EPS)) ** 2
    return (a - b).rolling(w).mean().clip(lower=0) ** 0.5


# ===================================================================  SIGNAUX

def _rank_dedup(S, sub=40000, agree=0.999, seed=0):
    """Le balayage ne voit JAMAIS un signal brut : il voit `xrank(signal)`, le
    rang transversal jour par jour. Deux signaux monotones l'un de l'autre A
    L'INTERIEUR de chaque journee donnent donc exactement le meme panier, le
    meme PnL et le meme t : c'est UN essai qui porte deux noms.

    Deux etages, parce qu'un seul ne suffit pas :

    1. empreinte exacte du rang -- replie les doublons parfaits, en O(n).
    2. accord sur les cellules COMMUNES -- `pos_crowd_vs_univ` et
       `lsr_globacct_x` sont identiques sur les 126 950 cellules qu'ils
       partagent et ne different que par la couverture de 19 jours sur 943.
       L'etage 1 les separe (le NaN entre dans l'empreinte), le PnL non : les
       deux lignes sortaient du balayage avec le meme t a la 2e decimale.
       On compare donc par paires, sur un echantillon fixe de cellules.

    Representant : le premier par ordre alphabetique. Deterministe."""
    names = [k for k in sorted(S) if S[k] is not None]
    R = {k: xrank(S[k]).to_numpy(dtype=np.float64) for k in names}

    parent = {k: k for k in names}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:                       # le representant reste le plus petit
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            parent[hi] = lo

    # ---- etage 1 : empreinte exacte
    seen = {}
    for k in names:
        h = hashlib.blake2b(np.ascontiguousarray(
            np.nan_to_num(R[k], nan=-9.0).round(9)).tobytes(), digest_size=16).hexdigest()
        if h in seen:
            union(seen[h], k)
        else:
            seen[h] = k

    # ---- etage 2 : accord sur les cellules communes, sur un echantillon fixe
    reps = sorted({find(k) for k in names})
    if len(reps) > 1:
        shape = R[reps[0]].shape
        rng = np.random.default_rng(seed)
        n_cells = shape[0] * shape[1]
        pos = (rng.choice(n_cells, size=min(sub, n_cells), replace=False)
               if n_cells > sub else np.arange(n_cells))
        V = {k: R[k].reshape(-1)[pos] for k in reps}
        F = {k: np.isfinite(V[k]) for k in reps}
        for i, a in enumerate(reps):
            if find(a) != a:
                continue
            for b in reps[i + 1:]:
                if find(b) != b:
                    continue
                both = F[a] & F[b]
                n = int(both.sum())
                if n < 500:                # trop peu de recouvrement pour trancher
                    continue
                same = np.abs(V[a][both] - V[b][both]) <= 1e-12
                if same.mean() >= agree:
                    union(a, b)

    out, dupes = {}, {}
    for k in names:
        r = find(k)
        if r == k:
            out[k] = S[k]
        else:
            dupes.setdefault(r, []).append(k)
    return out, {k: sorted(v) for k, v in dupes.items()}


def _tie_guard(S, thr=0.25):
    """rank(method='first') tranche les ex aequo par ORDRE ALPHABETIQUE du
    symbole. Un signal a point de masse n'a donc pas de jambe courte : il a une
    liste alphabetique. On l'ecarte."""
    out = {}
    for k, v in S.items():
        if v is None or v.notna().sum().sum() <= 500 or v.std(axis=1).notna().sum() <= 100:
            continue
        fr, arr = [], v.to_numpy(dtype=float)
        for i in range(0, arr.shape[0], 7):
            x = arr[i][np.isfinite(arr[i])]
            if len(x) < 20:
                continue
            _, cnt = np.unique(np.round(x, 10), return_counts=True)
            fr.append(cnt.max() / len(x))
        if (float(np.median(fr)) if fr else 1.0) > thr:
            continue
        out[k] = v
    return out


def build_signals(D):
    """{nom: DataFrame de score causal}. Convention : score haut = acheter.
    La convention n'est qu'une convention : le test est BILATERAL."""
    o, h, l, c = D["px_open"], D["px_high"], D["px_low"], D["px_close"]
    qv = D["px_quote_volume"]
    cnt = D.get("px_count")
    tbq = D.get("px_taker_buy_quote_volume")
    r = c.pct_change()
    S = {}

    # ---- prix ------------------------------------------------------------
    for n in (5, 10, 20, 60):
        S[f"mom_{n}"] = c / c.shift(n) - 1
        S[f"mom_skip1_{n}"] = c.shift(1) / c.shift(n) - 1
        S[f"dist_low_{n}"] = c / c.rolling(n).min() - 1
        S[f"dd_from_high_{n}"] = c / c.rolling(n).max() - 1
        S[f"pct_up_days_{n}"] = (r > 0).rolling(n).mean()
        S[f"trend_quality_{n}"] = (c / c.shift(n) - 1) / (r.rolling(n).std() * np.sqrt(n) + EPS)

    # ---- volatilite ------------------------------------------------------
    for n in (10, 20):
        pk = parkinson(h, l, n)
        gk = garman_klass(o, h, l, c, n)
        cc = r.rolling(n).std()
        S[f"noise_ratio_{n}"] = cc / (pk + EPS)
        S[f"gk_over_cc_{n}"] = gk / (cc + EPS)
        S[f"jump_share_{n}"] = (r.abs() > 3 * cc).rolling(n).mean()
        S[f"downside_vol_{n}"] = -r.clip(upper=0).rolling(n).std()
        S[f"vol_trend_{n}"] = cc / (r.rolling(n * 3).std() + EPS)

    # ---- microstructure OHLC ---------------------------------------------
    for n in (10, 20):
        cov = r.rolling(n).cov(r.shift(1))
        S[f"roll_spread_{n}"] = 2 * np.sqrt((-cov).clip(lower=0))
        S[f"overnight_share_{n}"] = ((np.log((o / c.shift(1)).clip(lower=EPS))).abs().rolling(n).mean()
                                     / ((np.log((c / o).clip(lower=EPS))).abs().rolling(n).mean() + EPS))
        S[f"close_location_{n}"] = ((c - l) / (h - l + EPS)).rolling(n).mean()
        # meme signal, mais qui EXCLUT la barre du jour : le test du lookahead
        S[f"close_location_lag1_{n}"] = ((c - l) / (h - l + EPS)).shift(1).rolling(n).mean()

    # ---- volume / liquidite ----------------------------------------------
    for n in (10, 20):
        S[f"amihud_{n}"] = (r.abs() / (qv + EPS)).rolling(n).mean()
        S[f"vol_shock_{n}"] = qv.rolling(n).mean() / (qv.rolling(n * 3).mean() + EPS)
        S[f"vp_corr_{n}"] = r.rolling(n).corr(qv.pct_change())
        S[f"turnover_trend_{n}"] = qv.rolling(5).mean() / (qv.rolling(n * 3).mean() + EPS)

    # ---- FLUX PRENEUR : reel dans um_klines, placeholder dans enriched ----
    if tbq is not None and cnt is not None:
        imb = (2.0 * tbq / (qv + EPS) - 1.0).clip(-1, 1)          # -1 vendeur .. +1 acheteur
        S["taker_imb_1"] = imb
        for n in (5, 10, 20):
            S[f"taker_imb_{n}"] = imb.rolling(n).mean()
            S[f"taker_imb_z{n * 3}"] = zs(imb, n * 3)   # le nom dit la VRAIE fenetre
        # divergence : le flux pousse mais le prix ne suit pas
        S["taker_price_div_10"] = xrank(imb.rolling(10).mean()) - xrank(c / c.shift(10) - 1)
        ats = qv / (cnt + EPS)                                    # taille moyenne de transaction
        S["avg_trade_size_z60"] = zs(ats, 60)
        for n in (5, 20):
            S[f"ats_trend_{n}"] = ats.rolling(n).mean() / (ats.rolling(n * 3).mean() + EPS)
        S["trade_count_z60"] = zs(cnt, 60)

    # ---- FUNDING ---------------------------------------------------------
    f = D.get("fund")
    if f is not None and f.notna().sum().sum() > 1000:
        S["funding_level"] = -f
        S["funding_z60"] = -zs(f, 60)
        for n in (3, 7, 20):
            S[f"funding_cum_{n}"] = -f.rolling(n).sum()
            S[f"funding_trend_{n}"] = -(f.rolling(n).mean() - f.rolling(n * 3).mean())
        S["funding_disp"] = -(f.sub(f.mean(axis=1), axis=0))
        S["funding_flip"] = -np.sign(f) * (np.sign(f) != np.sign(f.shift(3))).astype(float)

    # ---- OPEN INTEREST : la famille que v3 n'a jamais chargee -------------
    O = D.get("met_oi")
    Ou = D.get("met_oi_usd")
    if O is not None and O.notna().sum().sum() > 1000:
        doi = O.pct_change()
        for n in (3, 7, 20):
            S[f"oi_growth_{n}"] = -(O / O.shift(n) - 1)
            S[f"oi_growth_x_{n}"] = -xrank(O / O.shift(n) - 1)
        S["oi_z60"] = -zs(O, 60)
        S["oi_price_newlong"] = -(np.sign(doi) * np.sign(r)).rolling(5).mean()
        S["oi_price_newlong_20"] = -(np.sign(doi) * np.sign(r)).rolling(20).mean()
        S["oi_vs_volume"] = -(Ou / (qv.rolling(5).mean() + EPS)) if Ou is not None else None
        ic = D.get("met_oi_intraday_chg")
        if ic is not None:
            S["oi_intraday_chg"] = -ic
            S["oi_intraday_chg_5"] = -ic.rolling(5).mean()
        # capitulation : OI s'effondre pendant que le prix chute -> short covering
        S["oi_capitulation"] = ((doi < -0.03) & (r < -0.03)).rolling(5).sum()

    # ---- RATIOS LONG/SHORT : jamais charges non plus ----------------------
    for tag, key in [("ttacct", "met_tt_acct"), ("ttpos", "met_tt_pos"),
                     ("globacct", "met_glob_acct"), ("takerls", "met_taker_ls")]:
        L = D.get(key)
        if L is None or L.notna().sum().sum() < 1000:
            continue
        S[f"lsr_{tag}_lvl"] = -zs(L, 60)
        S[f"lsr_{tag}_x"] = -xrank(L)
        for n in (3, 7, 20):
            S[f"lsr_{tag}_chg{n}"] = -(L / L.shift(n) - 1)
    # les pros contre la foule : l'ecart entre top traders et comptes globaux
    A, B = D.get("met_tt_pos"), D.get("met_glob_acct")
    if A is not None and B is not None:
        S["lsr_smart_dumb"] = xrank(A) - xrank(B)
        S["lsr_smart_dumb_chg7"] = xrank(A / A.shift(7)) - xrank(B / B.shift(7))

    # ---- INTERACTIONS ----------------------------------------------------
    if f is not None and O is not None and f.notna().sum().sum() > 1000 and O.notna().sum().sum() > 1000:
        S["crowding_fund_x_oi"] = -(xrank(f) * xrank(O / O.shift(7) - 1))
        S["crowding_z"] = -(zs(f, 60).clip(-3, 3) * zs(O, 60).clip(-3, 3))
    if tbq is not None and O is not None and O.notna().sum().sum() > 1000:
        imb = (2.0 * tbq / (qv + EPS) - 1.0).clip(-1, 1)
        S["oi_x_taker"] = xrank(O / O.shift(7) - 1) * xrank(imb.rolling(5).mean())

    out = {}
    for k, v in S.items():
        if v is None or v.notna().sum().sum() <= 500 or v.std(axis=1).notna().sum() <= 100:
            continue
        # GARDE-FOU EGALITES. rank(method="first") tranche les ex aequo par ordre
        # de colonne, c'est-a-dire par ORDRE ALPHABETIQUE du symbole. Un signal a
        # point de masse (jump_share : 86% du panel a la meme valeur ; funding_flip
        # 83% ; oi_capitulation 68%) ne forme donc pas un portefeuille : sa jambe
        # courte est la liste alphabetique. Ce n'est pas un signal faible, c'est un
        # signal qui n'existe pas.
        fr = []
        arr = v.to_numpy(dtype=float)
        for i in range(0, arr.shape[0], 7):
            x = arr[i][np.isfinite(arr[i])]
            if len(x) < 20:
                continue
            _, cnt = np.unique(np.round(x, 10), return_counts=True)
            fr.append(cnt.max() / len(x))
        tie = float(np.median(fr)) if fr else 1.0
        if tie > 0.25:
            continue
        out[k] = v
    return out


def build_states(D):
    c = D["px_close"]
    r = c.pct_change()
    mkt = r.median(axis=1)
    st = {}
    st["mkt_vol"] = mkt.rolling(20).std()
    st["xs_disp"] = r.std(axis=1).rolling(5).mean()
    st["mkt_trend"] = mkt.rolling(20).mean()
    st["avg_corr"] = r.rolling(30).corr(mkt).mean(axis=1)
    f = D.get("fund")
    if f is not None and f.notna().sum().sum() > 1000:
        st["mkt_funding"] = f.mean(axis=1).rolling(7).mean()
    O = D.get("met_oi")
    if O is not None and O.notna().sum().sum() > 1000:
        st["mkt_oi_growth"] = (O.sum(axis=1) / O.sum(axis=1).shift(7) - 1)
    return {k: v for k, v in st.items() if v.notna().sum() > 200}


# ==============================================================  PORTEFEUILLE

def causal_universe(D, top_n, aum, basket, adv_frac, min_days=90):
    """Univers causal AVEC contrainte de capacite.

    Un symbole n'est eligible que si la position qu'on y mettrait tient dans
    `adv_frac` de son volume quotidien median. C'est une EXCLUSION, pas une
    surtaxe : un ordre de 12 500 $ sur un carnet de 53 $ n'est pas cher, il
    n'existe pas.
    """
    c, qv = D["px_close"], D["px_quote_volume"]
    adv = qv.rolling(20).median()
    alive = c.notna().rolling(min_days).sum() >= min_days * 0.9
    a2 = adv.where(alive)
    rk = a2.rank(axis=1, ascending=False, method="first")
    liquid = (rk <= top_n) & a2.notna()
    pos_notional = aum / (2.0 * basket)
    capacity = a2 >= (pos_notional / max(adv_frac, EPS))
    return liquid & capacity


def symbol_cost_bps(D, base_cost, clip_hi=4.0):
    adv = (D["px_quote_volume"]).rolling(20).median()
    med = adv.median(axis=1)
    ratio = adv.div(med, axis=0).replace(0, np.nan)
    mult = (1.0 / ratio) ** 0.5
    return (base_cost * mult.clip(lower=1.0, upper=clip_hi)).fillna(base_cost * clip_hi)


def prepare(score, univ, smooth):
    """Le rang ne depend que de (signal, neutralisation, smooth). On le calcule
    une fois au lieu de douze : sans cela le placebo est inabordable."""
    s = score.where(univ)
    if smooth > 1:
        s = s.ewm(span=smooth, min_periods=smooth).mean()
    rk = s.rank(axis=1, method="first").to_numpy(dtype=float)
    cnt = s.notna().sum(axis=1).to_numpy(dtype=float)
    return rk, cnt


def backtest(prep, ret1_np, k, cost_np, hold=1, smooth=0, horizon=1, exec_lag=1):
    """
    Renvoie (gross, cost, m) en bps par JOUR.

    ret1     : rendement close(t) -> close(t+1), indexe en t
    exec_lag : le score de t est execute a close(t+exec_lag).
    Les deux directions se derivent exactement de (gross, cost) :
        net_DIR = gross - cost ; net_INV = -gross - cost
    car inverser le score negate exactement les poids et laisse |dW| inchange.
    """
    rk, cnt = prep
    ok = cnt >= 2 * k + 2
    T = np.zeros_like(rk)
    valid = np.isfinite(rk)
    T[valid & (rk <= k)] = -1.0 / k
    T[valid & (rk > (cnt[:, None] - k))] = 1.0 / k
    T[~ok] = 0.0

    span = max(int(horizon), 1) * max(int(hold), 1)
    if span > 1:
        cs = np.cumsum(np.vstack([np.zeros((1, T.shape[1])), T]), axis=0)
        W = np.empty_like(T)
        for i in range(T.shape[0]):
            j = max(0, i - span + 1)
            W[i] = (cs[i + 1] - cs[j]) / (i - j + 1)
    else:
        W = T.copy()

    if exec_lag > 0:
        W = np.vstack([np.zeros((exec_lag, W.shape[1])), W[:-exec_lag]])
        okx = np.concatenate([np.zeros(exec_lag, bool), ok[:-exec_lag]])
    else:
        okx = ok
    if okx.sum() < 120:
        return None

    W = np.where(np.isfinite(ret1_np), W, 0.0)
    gross = (W * np.nan_to_num(ret1_np)).sum(axis=1) * 1e4
    dW = np.abs(np.diff(W, axis=0, prepend=np.zeros((1, W.shape[1]))))
    cost = (dW * cost_np / 2.0).sum(axis=1)

    m = okx & np.isfinite(gross)
    if m.sum() < 120:
        return None
    return gross, cost, m


def newey_west_t(x, lag):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 60:
        return np.nan
    mu = x.mean()
    e = x - mu
    s = (e @ e) / n
    for L in range(1, min(int(lag), n - 1) + 1):
        s += 2.0 * (1.0 - L / (lag + 1.0)) * ((e[L:] @ e[:-L]) / n)
    return mu / np.sqrt(max(s, 1e-18) / n)


def sub_stability(x, k=4):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 4 * k:
        return np.nan
    return float(np.mean([seg.mean() > 0 for seg in np.array_split(x, k)]))


def effective_tests(M):
    """Nombre effectif d'essais. M : (n_configs, n_jours), alignee, jour non
    traite = 0 (exact : le PnL de ce jour EST nul). Aucun echantillonnage."""
    if M.shape[0] < 3:
        return float(M.shape[0]), float(M.shape[0])
    sd = M.std(axis=1)
    keep = sd > 1e-12
    M = M[keep]
    if M.shape[0] < 3:
        return 1.0, 1.0
    C = np.corrcoef(M)
    C = np.nan_to_num(C, nan=0.0)
    ev = np.linalg.eigvalsh(C)
    ev = np.clip(ev, 0, None)
    p = M.shape[0]
    part = float(ev.sum() ** 2 / (ev ** 2).sum())              # ratio de participation
    lj = float(sum(min(e, 1.0) + (e - np.floor(e) if e >= 1 else 0.0) for e in ev))  # Li-Ji
    return part, min(max(lj, 1.0), float(p))


# =====================================================================  GRILLE

_T0 = [time.time()]
_NEUT_SIG_CACHE = {}
_PREP_CACHE = {}


def run_grid(D, SIG, ST, univ, ret1, COSTP, args, prereg=None, ret_override=None):
    """Renvoie (rows, pnl_matrix, keys). Si prereg est fourni, ne joue QUE ces
    configurations -- c'est ce qui rend la phase de confirmation honnete."""
    R1 = ret_override if ret_override is not None else ret1
    n_days_total = len(R1.index)
    rows, pnl_rows, keys = [], [], []

    R1np = R1.to_numpy(dtype=float)
    Cnp = np.nan_to_num(COSTP.reindex_like(R1).to_numpy(dtype=float),
                        nan=float(np.nanmedian(COSTP.to_numpy(dtype=float))))
    UNV = univ

    def one(sname, prep, nname, hh, kk, hd, sm, state="-", regime="-", force_dir=None):
        out = backtest(prep, R1np, kk, Cnp, hold=hd, smooth=sm,
                       horizon=hh, exec_lag=args.exec_lag)
        if out is None:
            return
        gross, cost, m = out
        net_dir = (gross - cost)[m]
        net_inv = (-gross - cost)[m]
        nd = int(m.sum())
        lag = int(hh) * int(max(hd, 1)) + int(sm) + 2
        t_dir, t_inv = newey_west_t(net_dir, lag), newey_west_t(net_inv, lag)
        if not np.isfinite(t_dir):
            return
        # DEFAUT CORRIGE. Choisir le cote sur les donnees qu'on est en train de
        # juger rend l'essai BILATERAL : on prend le meilleur de deux paris. En
        # exploration c'est legitime (et le seuil du placebo en tient compte),
        # mais en phase `confirm` cela ANNULE la direction pre-enregistree et
        # fait payer 0,22 de seuil (2,7344 -> 2,9552 a n=16) sans rien donner.
        # Mesure : 2 des 9 lignes du confirm v4 avaient change de cote par
        # rapport a leur propre pre-enregistrement (amihud_20 DIR->INV,
        # dd_from_high_10 INV->DIR). Quand une direction est pre-specifiee,
        # elle est desormais IMPOSEE, et le test redevient unilateral.
        if force_dir in ("DIR", "INV"):
            best_inv = (force_dir == "INV")
        else:
            best_inv = net_inv.mean() > net_dir.mean()
        d = "INV" if best_inv else "DIR"
        nb, tb = (net_inv, t_inv) if best_inv else (net_dir, t_dir)
        full = np.zeros(n_days_total)
        full[np.where(m)[0]] = nb
        pnl_rows.append(full)
        key = f"{sname}|{nname}|h{hh}|k{kk}|hd{hd}|sm{sm}|{state}|{regime}"
        keys.append(key)
        rows.append(dict(key=key, signal=sname, neutral=nname, horizon=hh, basket=kk,
                         hold=hd, smooth=sm, state=state, regime=regime, dir=d,
                         n_days=nd, n_indep=int(nd / max(hh * max(hd, 1) + sm, 1)),   # I14 : le lissage ewm(span=sm) ajoute ~sm jours de memoire, comme nw_lag le compte deja
                         gross_dly=gross[m].mean(), cost_dly=cost[m].mean(),
                         net_dly=nb.mean(), net_per_hold=nb.mean() * hh,
                         t=tb, t_dir=t_dir, t_inv=t_inv,
                         dir_impose=(force_dir if force_dir in ("DIR", "INV") else ""),
                         stab=sub_stability(nb), nw_lag=lag))

    if prereg is not None:
        for cfg in prereg:
            sname = cfg["signal"]
            if sname not in SIG:
                continue
            s_ = NEUT_FN(D, cfg["neutral"])(xrank(SIG[sname]))
            if cfg.get("state", "-") != "-":
                q = ST[cfg["state"]].rolling(250, min_periods=60).rank(pct=True)
                lo, hi = (0.0, 0.33) if cfg["regime"] == "bas" else (0.67, 1.0)
                mask = ((q >= lo) & (q <= hi)).reindex(s_.index).fillna(False)
                s_ = s_.where(pd.DataFrame(np.repeat(mask.to_numpy()[:, None], s_.shape[1], axis=1),
                                           index=s_.index, columns=s_.columns))
            prep = prepare(s_, UNV, cfg["smooth"])
            one(sname, prep, cfg["neutral"], cfg["horizon"], cfg["basket"],
                cfg["hold"], cfg["smooth"], cfg.get("state", "-"), cfg.get("regime", "-"),
                force_dir=cfg.get("dir"))
        return rows, np.array(pnl_rows), keys

    HORIZ, BASK, HOLD, SMOOTH = [1, 3, 5, 10], [8, 12], [1, 5], [0, 5]
    for si, (sname, raw) in enumerate(SIG.items()):
        for nname in args.neutrals:
            ck = (sname, nname)
            if ck not in _NEUT_SIG_CACHE:
                _NEUT_SIG_CACHE[ck] = NEUT_FN(D, nname)(xrank(raw))
            s_ = _NEUT_SIG_CACHE[ck]
            for sm in SMOOTH:
                pk = (sname, nname, sm)
                if pk not in _PREP_CACHE:
                    _PREP_CACHE[pk] = prepare(s_, UNV, sm)
                prep = _PREP_CACHE[pk]
                seen_span = set()
                for hh, kk, hd in itertools.product(HORIZ, BASK, HOLD):
                    if hd > 1 and hh > 3:
                        continue
                    # le portefeuille ne depend que de span = horizon*hold :
                    # (h=1,hd=5) et (h=5,hd=1) sont LE MEME test, pas deux
                    sp = (hh * hd, kk)
                    if sp in seen_span:
                        continue
                    seen_span.add(sp)
                    one(sname, prep, nname, hh, kk, hd, sm)
        _NEUT_SIG_CACHE.clear(); _PREP_CACHE.clear()   # sinon ~9 Go et le noyau tue
        if (si + 1) % 20 == 0:
            print(f"  {si+1}/{len(SIG)}  ({len(rows)} configs, {time.time()-_T0[0]:.0f}s)", flush=True)
    return rows, np.array(pnl_rows), keys


_NEUT_CACHE = {}


def NEUT_FN(D, name):
    if name == "raw":
        return lambda s: s
    # Le cache DOIT etre indexe par la fenetre. Sans cela, un processus qui
    # charge deux panels successifs reutilise les facteurs du premier ; le
    # reindex_like du second les rend tout NaN, la neutralisation degenere en
    # simple centrage, et les t sont faux sans qu'aucune erreur ne soit levee.
    ck = (D["px_close"].index[0], D["px_close"].index[-1], D["px_close"].shape)
    if _NEUT_CACHE.get("_key") != ck:
        _NEUT_CACHE.clear()
        _NEUT_CACHE["_key"] = ck
    if "factors" not in _NEUT_CACHE:
        c = D["px_close"]
        r = c.pct_change()
        mkt = r.median(axis=1)
        _NEUT_CACHE["factors"] = {
            "beta": r.rolling(60).corr(mkt),
            "size": np.log(D["px_quote_volume"].rolling(20).mean() + 1),
            "vol": r.rolling(20).std(),
            # le rendement passe : sans lui, tout signal correle au momentum
            # se fait passer pour une decouverte
            "r20": xrank(c / c.shift(20) - 1),
            "r60": xrank(c / c.shift(60) - 1),
            "r5": xrank(c / c.shift(5) - 1),
        }
    F = _NEUT_CACHE["factors"]
    sets = {
        "mkt":   [F["beta"]],
        "sz_vl": [F["size"], F["vol"]],
        "full":  [F["beta"], F["size"], F["vol"]],
        "mom":   [F["r5"], F["r20"], F["r60"]],
        "allf":  [F["beta"], F["size"], F["vol"], F["r5"], F["r20"], F["r60"]],
    }
    return lambda s: neutralize(s, sets[name])


# =====================================================================  MAIN

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/qbee/futur")
    ap.add_argument("--phase", default="search", choices=["search", "placebo", "confirm"])
    ap.add_argument("--start", default="2021-12-01")
    ap.add_argument("--end", default="2024-06-30")
    ap.add_argument("--cost-bps", type=float, default=14.0)
    ap.add_argument("--top-n", type=int, default=120)
    ap.add_argument("--aum", type=float, default=200_000.0)
    ap.add_argument("--adv-frac", type=float, default=0.01)
    ap.add_argument("--exec-lag", type=int, default=1)
    ap.add_argument("--winsor", type=float, default=0.50, help="borne du rendement quotidien")
    ap.add_argument("--neutrals", default="raw,mkt,full,mom,allf")
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prereg", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tag", default="v4")
    ap.add_argument("--exclude", default="",
                    help="signaux DEJA testes hors echantillon : ils ont consomme\n                          leur fenetre et ne peuvent plus etre pre-enregistres")
    ap.add_argument("--deriv-set", action="store_true",
                    help="ajoute la bibliotheque etendue de signaux derives")
    a = ap.parse_args()
    a.neutrals = [x for x in a.neutrals.split(",") if x]

    start, end = pd.Timestamp(a.start, tz="UTC"), pd.Timestamp(a.end, tz="UTC")
    t0 = time.time()
    D = load_cache(a.root, start, end)
    c = D["px_close"]
    print(f"=== panel {c.shape[0]}j x {c.shape[1]} symboles  [{a.start} -> {a.end}] ===")
    for nm, k in [("funding", "fund"), ("open interest", "met_oi"),
                  ("L/S top acct", "met_tt_acct"), ("L/S top pos", "met_tt_pos"),
                  ("L/S global", "met_glob_acct"), ("taker L/S", "met_taker_ls"),
                  ("taker buy $", "px_taker_buy_quote_volume")]:
        v = D.get(k)
        if v is None:
            print(f"  {nm:<14} ABSENT")
        else:
            print(f"  {nm:<14} {int(v.notna().sum().sum()):>9} points, "
                  f"{int(v.notna().any().sum()):>3} symboles")

    univ = causal_universe(D, a.top_n, a.aum, 8, a.adv_frac)
    print(f"  univers median {univ.sum(axis=1).median():.0f}/jour  "
          f"(top_n={a.top_n}, capacite: position {a.aum/16:,.0f}$ <= {a.adv_frac:.1%} ADV)")
    if univ.sum(axis=1).median() < 20:
        print("  !! univers trop etroit -- relacher --adv-frac ou --top-n")

    ret1 = c.shift(-1) / c - 1.0
    if a.winsor > 0:
        ret1 = ret1.clip(-a.winsor, a.winsor)
    COSTP = symbol_cost_bps(D, a.cost_bps)
    _cu = COSTP.where(univ).stack()
    print(f"  cout SUR L'UNIVERS : median {_cu.median():.1f} bps, p95 {_cu.quantile(0.95):.1f} bps, "
          f"max {_cu.max():.1f} bps  |  exec_lag={a.exec_lag}")
    _adv = D["px_quote_volume"].rolling(20).median().where(univ).stack()
    print(f"  ADV univers : median {_adv.median()/1e6:.1f} M$, min {_adv.min()/1e6:.1f} M$  "
          f"-> position = {a.aum/16/_adv.median()*100:.3f}% de l'ADV mediane")

    SIG = build_signals(D)
    if a.deriv_set:
        import deriv_signals
        extra = deriv_signals.build(D)
        n0 = len(SIG)
        SIG.update({k: v for k, v in extra.items() if k not in SIG})
        SIG = _tie_guard(SIG)
        print(f"  + bibliotheque derives : {len(extra)} construits, "
              f"{len(SIG)-n0} retenus apres garde-fou et doublons")
    SIG, DUPES = _rank_dedup(SIG)
    if DUPES:
        _nd = sum(len(v) for v in DUPES.values())
        print(f"  doublons de RANG : {_nd} noms replies sur {len(DUPES)} classes "
              f"(meme rang transversal => meme panier => UN essai)")
        for _rep, _dr in list(sorted(DUPES.items()))[:8]:
            print(f"      {_rep} == {', '.join(_dr)}")
    ST = build_states(D)
    print(f"=== {len(SIG)} signaux (apres garde-fou egalites), {len(ST)} etats  "
          f"({time.time()-t0:.0f}s) ===", flush=True)

    outdir = Path(a.root) / "reports" / "edge_discovery" / f"sweep_{a.tag}"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "rank_duplicates.json").write_text(
        json.dumps({"n_classes": len(DUPES),
                    "n_names_folded": sum(len(v) for v in DUPES.values()),
                    "n_signals_kept": len(SIG),
                    "classes": DUPES}, indent=2))

    # ------------------------------------------------------------- PLACEBO
    if a.phase == "placebo":
        # La preparation (neutralisation + rang) ne depend PAS des rendements.
        # On la calcule une fois et on fait passer les 20 tirages dessus, au lieu
        # de rejouer 20 grilles completes -- ou de tout mettre en cache (9 Go, tue).
        rng = np.random.default_rng(a.seed)
        RP = []
        base = ret1.to_numpy(dtype=float)
        for _ in range(a.reps):
            P = base.copy()
            for i in range(P.shape[0]):
                row = P[i]
                fin = np.where(np.isfinite(row))[0]
                if len(fin) > 2:
                    row[fin] = row[rng.permutation(fin)]
            RP.append(P)
        Cnp = np.nan_to_num(COSTP.reindex_like(ret1).to_numpy(dtype=float),
                            nan=float(np.nanmedian(COSTP.to_numpy(dtype=float))))
        maxt = np.full(a.reps, -np.inf)
        HORIZ, BASK, HOLD, SMOOTH = [1, 3, 5, 10], [8, 12], [1, 5], [0, 5]
        t0p = time.time()
        for si, (sname, raw) in enumerate(SIG.items()):
            for nname in a.neutrals:
                s_ = NEUT_FN(D, nname)(xrank(raw))
                for sm in SMOOTH:
                    prep = prepare(s_, univ, sm)
                    seen = set()
                    for hh, kk, hd in itertools.product(HORIZ, BASK, HOLD):
                        if hd > 1 and hh > 3:
                            continue
                        if (hh * hd, kk) in seen:
                            continue
                        seen.add((hh * hd, kk))
                        lag = int(hh) * int(max(hd, 1)) + int(sm) + 2
                        for ri in range(a.reps):
                            out = backtest(prep, RP[ri], kk, Cnp, hold=hd, smooth=sm,
                                           horizon=hh, exec_lag=a.exec_lag)
                            if out is None:
                                continue
                            g, cst, m = out
                            nd = int(m.sum())
                            if int(nd / max(hh * max(hd, 1), 1)) < 100:
                                continue
                            nd_ = (g - cst)[m]
                            ni_ = (-g - cst)[m]
                            nb = ni_ if ni_.mean() > nd_.mean() else nd_
                            tv = newey_west_t(nb, lag)
                            if np.isfinite(tv) and tv > maxt[ri]:
                                maxt[ri] = tv
                    del prep
                del s_
            if (si + 1) % 10 == 0:
                print(f"  {si+1}/{len(SIG)}  max(t) courant : "
                      f"med {np.median(maxt):.2f}  p95 {np.percentile(maxt,95):.2f}"
                      f"  ({time.time()-t0p:.0f}s)", flush=True)
        mt = maxt[np.isfinite(maxt)]
        res = dict(reps=int(len(mt)), max_t=list(map(float, mt)),
                   q50=float(np.percentile(mt, 50)), q95=float(np.percentile(mt, 95)),
                   q99=float(np.percentile(mt, 99)),
                   start=a.start, end=a.end, exec_lag=a.exec_lag,
                   neutrals=a.neutrals)
        json.dump(res, open(outdir / f"placebo_{a.start}_{a.end}.json", "w"), indent=2)
        print(f"\n=== SEUIL EMPIRIQUE ===")
        print(f"  max(t) sous le nul, {len(mt)} tirages : "
              f"median {res['q50']:.2f}, p95 {res['q95']:.2f}, p99 {res['q99']:.2f}")
        print(f"  tirages : " + " ".join(f"{v:.2f}" for v in sorted(mt)))
        print(f"-> {outdir / f'placebo_{a.start}_{a.end}.json'}")
        return

    # -------------------------------------------------- SEARCH / CONFIRM
    prereg = None
    if a.phase == "confirm":
        prereg = json.load(open(a.prereg))["configs"]
        print(f"=== CONFIRMATION : {len(prereg)} configurations pre-enregistrees, "
              f"aucun reglage ===", flush=True)
        # PRECONDITION DU REGARD, pas trace du regard. L'audit du 2026-09-09 a
        # cherche les regards passes sur la fenetre scellee et n'a trouve AUCUN
        # enregistrement : le compte de 16 essais est une RECONSTRUCTION depuis
        # les artefacts restes sur disque. Ce qui a tourne sans laisser de fichier
        # n'est pas compte, donc les seuils sont des bornes inferieures.
        # Desormais : on ecrit AVANT de calculer, et si on ne peut pas ecrire, on
        # ne calcule pas. Hors de la fenetre d'exploration, on exige en plus que
        # le pre-enregistrement soit COMMITE ET POUSSE -- l'horodatage du remote
        # est le seul temoin qu'on ne peut pas antidater.
        import look_ledger
        scelle = not (a.start >= EXPLORATION[0] and a.end <= EXPLORATION[1])
        try:
            ent = look_ledger.record(
                "confirm", (a.start, a.end), prereg, prereg=a.prereg,
                require_witness=scelle,
                note=f"tag={a.tag} exec_lag={a.exec_lag} top_n={a.top_n} "
                     f"cost_bps={a.cost_bps} aum={a.aum}")
        except look_ledger.LedgerError as e:
            print(f"\n!! REGARD REFUSE : {e}")
            print("   Rien n'a ete calcule. La fenetre n'a pas ete ouverte.")
            return
        print(f"    ledger : regard seq={ent['seq']} inscrit "
              f"({'SCELLE, temoin exige' if scelle else 'exploration'}), "
              f"chaine {ent['hash'][:12]}", flush=True)

    rows, M, keys = run_grid(D, SIG, ST, univ, ret1, COSTP, a, prereg=prereg)
    res = pd.DataFrame(rows)
    if not len(res):
        print("aucune configuration valide"); return
    keep = res.n_indep >= 100
    res, M = res[keep].reset_index(drop=True), M[np.asarray(keep)]
    print(f"=== {len(res)} configurations retenues (n_indep>=100)  "
          f"({time.time()-t0:.0f}s) ===", flush=True)

    part, lj = effective_tests(M)
    meff = max(part, lj)   # conservateur ; le seuil qui tranche reste l'empirique
    thr_two = norm.ppf(1 - 0.025 / max(meff, 1.0))     # BILATERAL : le cote est choisi apres coup
    res["meff"] = meff
    res["thr"] = thr_two
    # Le meilleur des deux cotes est deja retenu. Un t NEGATIF signifie que meme
    # le meilleur cote perd de l'argent -- typiquement gross~0 et cost~25 bps, ou
    # le t ne mesure que la constante de cout. Seul un t positif est un candidat.
    res["passes"] = res.t > thr_two
    res = res.sort_values("t", ascending=False)

    pfile = outdir / f"placebo_{a.start}_{a.end}.json"
    emp = None
    if pfile.exists():
        emp = json.load(open(pfile))
        res["passes_placebo"] = res.t > emp["q95"]

    out = a.out or str(outdir / f"{a.phase}_{a.start}_{a.end}_lag{a.exec_lag}.csv")
    res.to_csv(out, index=False)

    print(f"\n=== RESULTAT ({a.phase}) ===")
    print(f"  configurations jouees      {len(res)}")
    print(f"  essais effectifs           participation {part:.1f} | Li-Ji {lj:.1f} -> retenu {meff:.1f}")
    print(f"  seuil BILATERAL Bonferroni |t| > {thr_two:.2f}")
    if emp:
        print(f"  seuil EMPIRIQUE (placebo)  |t| > {emp['q95']:.2f}  (p95 de max|t| sur {emp['reps']} tirages)")
    cols = ["signal", "neutral", "horizon", "basket", "hold", "smooth", "dir",
            "n_days", "n_indep", "gross_dly", "cost_dly", "net_dly", "net_per_hold",
            "t", "stab", "nw_lag", "passes"] + (["passes_placebo"] if emp else [])
    with pd.option_context("display.width", 260, "display.max_columns", 60):
        print("\n=== TOP 30 ===")
        print(res[cols].head(30).to_string(index=False, float_format=lambda v: f"{v:7.2f}"))
    w = res[res.passes] if not emp else res[res.passes & res.passes_placebo]
    print(f"\n{len(w)} configuration(s) au-dessus du/des seuil(s).")

    if a.phase == "search":
        # Deux rangs, declares d'avance :
        #   PRIMAIRE   : passe le seuil empirique (ou Bonferroni si pas de placebo)
        #                ET stabilite >= 0.75. C'est l'hypothese pre-enregistree.
        #   SECONDAIRE : meilleure ligne de chaque signal distinct restant, dans la
        #                limite de 8. Exploratoire : chaque ligne compte comme un
        #                test de plus, et est etiquetee comme telle.
        thr_use = (emp or {}).get("q95", thr_two)
        pos = res[(res.t > 0) & (res.stab >= 0.75)].copy()
        # Un signal deja juge hors echantillon a DEPENSE sa fenetre. Le rejouer
        # n'est plus une confirmation, c'est une lecture de reponse connue.
        burned = [x for x in a.exclude.split(",") if x]
        if burned:
            n0 = len(pos)
            pos = pos[~pos.signal.isin(burned)]
            print(f"  {n0-len(pos)} lignes ecartees : signaux ayant deja consomme "
                  f"la fenetre hors echantillon ({', '.join(burned)})")
        prim = pos[pos.t > thr_use].drop_duplicates(subset=["signal"], keep="first")
        rest = pos[~pos.index.isin(prim.index)].drop_duplicates(subset=["signal"], keep="first").head(8)
        prim["tier"], rest["tier"] = "primaire", "secondaire"
        keepc = pd.concat([prim, rest])
        pr = dict(created=a.start + "/" + a.end, exec_lag=a.exec_lag, aum=a.aum,
                  adv_frac=a.adv_frac, top_n=a.top_n, cost_bps=a.cost_bps,
                  winsor=a.winsor, neutrals=a.neutrals,
                  meff_participation=float(part), meff_liji=float(lj),
                  thr_bonferroni=float(thr_two), thr_used=float(thr_use),
                  placebo_q95=(emp or {}).get("q95"),
                  n_configs_joues=int(len(res)),
                  configs=[{k: (int(v) if isinstance(v, (np.integer,)) else
                               (float(v) if isinstance(v, (np.floating,)) else v))
                            for k, v in r.items()
                            if k in ("signal", "neutral", "horizon", "basket", "hold",
                                     "smooth", "state", "regime", "dir", "tier",
                                     "t", "net_dly", "stab")}
                           for _, r in keepc.iterrows()])
        pf = outdir / "prereg.json"
        json.dump(pr, open(pf, "w"), indent=2)
        print(f"\n=== PRE-ENREGISTREMENT scelle : {int((keepc.tier=='primaire').sum())} primaire(s), "
              f"{int((keepc.tier=='secondaire').sum())} secondaire(s) ===")
        for _, r in keepc.iterrows():
            print(f"    [{r.tier:<10}] {r.signal:<22} {r.neutral:<6} h{int(r.horizon)} k{int(r.basket)} "
                  f"hd{int(r.hold)} sm{int(r.smooth)} {r['dir']}  t={r.t:+.2f} net={r.net_dly:.1f}bps")
        print(f"-> {pf}")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
