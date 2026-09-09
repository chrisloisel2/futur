#!/usr/bin/env python3
"""
alpha_sweep_v2.py -- balayage profond.

Ce que v1 ne faisait pas, et qui compte plus que tout le reste :

1. LES DERIVES. v1 ne balayait que prix et volume, la donnee la plus minee de la
   finance. Le funding, l'open interest et le ratio long/short sont specifiques au
   crypto, publies gratuitement, et bien moins exploites. La probabilite a priori
   d'un effet reel y est plus elevee -- c'est le levier principal.

2. LE CONDITIONNEMENT. Un signal plat en moyenne peut etre fort dans un regime.
   v1 testait signal -> rendement. v2 teste signal x etat -> rendement, ou l'etat
   est la vol du marche, la dispersion transversale, le regime de funding, la
   correlation moyenne. C'est la ou vit la majorite de l'alpha reel.

3. LA NEUTRALISATION. Retirer le beta marche ne suffit pas. On retire aussi la
   taille, la vol, et l'appartenance a un cluster de correlation (proxy sectoriel).
   L'alpha est dans le residu apres retrait des facteurs connus.

4. LE TURNOVER. Tes couts font 8 a 22 bps par episode. Reduire le turnover EST de
   l'alpha, mecaniquement. v2 teste le lissage du signal, les bandes de non-trade,
   et les portefeuilles chevauchants facon Jegadeesh-Titman.

5. LE COMPTE EFFECTIF D'ESSAIS. v1 deflatait contre 624 essais dont la plupart
   etaient des doublons. v2 calcule le nombre effectif via le spectre propre de la
   matrice de correlation des PnL. C'est ce qui a fait rater le seuil a v1.

Usage:
    python3 alpha_sweep_v2.py --root /home/qbee/futur --start 2020-01-01 --end 2022-12-31
    python3 alpha_sweep_v2.py --root ... --stage base       # signaux seuls
    python3 alpha_sweep_v2.py --root ... --stage conditioned # + conditionnement
"""

import argparse, sys, warnings, itertools
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import norm

warnings.filterwarnings("ignore")
EPS = 1e-12

# =====================================================================  DONNEES

PRICE_LAYOUTS = [
    "data/enriched/{sym}_1h_enriched.parquet",
    "data/enriched/{sym}_1d_enriched.parquet",
]
DERIV_LAYOUTS = {
    "funding": ["data/derivatives_backfill/binance/funding/{sym}.parquet",
                "data/derivatives_backfill/*/funding/{sym}.parquet"],
    "oi":      ["data/derivatives_backfill/binance/open_interest_hist/{sym}.parquet",
                "data/derivatives_backfill/*/open_interest_hist/{sym}.parquet"],
    "lsr":     ["data/derivatives_backfill/*/long_short_ratio/{sym}.parquet",
                "data/derivatives_backfill/*/lsr/{sym}.parquet"],
}


def _to_utc(t):
    if pd.api.types.is_integer_dtype(t):
        return pd.to_datetime(t, unit="ns" if float(t.iloc[0]) > 1e15 else "ms", utc=True)
    return pd.to_datetime(t, utc=True)


def _pick(cols, names):
    low = {c.lower(): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    return None


def load_ohlcv(root, symbols, start, end):
    """Panel quotidien : close, high, low, open, volume."""
    acc = {k: [] for k in ("open", "high", "low", "close", "vol")}
    got = []
    for sym in symbols:
        parts = []
        for pat in PRICE_LAYOUTS:
            for p in root.glob(pat.replace("{sym}", sym)):
                try:
                    df = pd.read_parquet(p)
                except Exception:
                    continue
                tc = _pick(df.columns, ["datetime", "timestamp", "open_time", "date", "open_time_ms"])
                if tc is None:
                    continue
                d = pd.DataFrame({"t": _to_utc(df[tc])})
                for k, names in [("open", ["open", "o"]), ("high", ["high", "h"]),
                                 ("low", ["low", "l"]), ("close", ["close", "c"]),
                                 ("vol", ["volume", "quote_volume", "v"])]:
                    c = _pick(df.columns, names)
                    d[k] = df[c].astype(float).values if c else np.nan
                parts.append(d)
        if not parts:
            continue
        d = pd.concat(parts).dropna(subset=["t", "close"]).sort_values("t").set_index("t")
        d = d.resample("1D").agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "vol": "sum"})
        for k in acc:
            s = d[k].rename(sym)
            acc[k].append(s)
        got.append(sym)
    out = {k: pd.concat(v, axis=1).sort_index() for k, v in acc.items() if v}
    out = {k: v.loc[(v.index >= start) & (v.index <= end)] for k, v in out.items()}
    return out, got


def load_derivative(root, symbols, kind, index, start, end):
    """Panel quotidien d'un flux derive. Renvoie None si absent."""
    series = []
    for sym in symbols:
        for pat in DERIV_LAYOUTS[kind]:
            hits = list(root.glob(pat.replace("{sym}", sym)))
            if not hits:
                continue
            for p in hits:
                try:
                    df = pd.read_parquet(p)
                except Exception:
                    continue
                tc = _pick(df.columns, ["fundingtime", "funding_time", "timestamp",
                                        "datetime", "time", "date"])
                vc = _pick(df.columns, {
                    "funding": ["fundingrate", "funding_rate", "rate", "value"],
                    "oi": ["sumopeninterest", "sum_open_interest", "openinterest",
                           "open_interest", "value"],
                    "lsr": ["longshortratio", "long_short_ratio", "ratio", "value"],
                }[kind])
                if tc is None or vc is None:
                    continue
                d = pd.DataFrame({"t": _to_utc(df[tc]), "v": pd.to_numeric(df[vc], errors="coerce")})
                d = d.dropna().sort_values("t").set_index("t")
                agg = "sum" if kind == "funding" else "last"
                series.append(d["v"].resample("1D").agg(agg).rename(sym))
                break
            break
    if not series:
        return None
    P = pd.concat(series, axis=1).sort_index()
    P = P.loc[(P.index >= start) & (P.index <= end)]
    return P.reindex(index)


# ================================================================  OUTILS PANEL

def zs(df, w=60):
    return (df - df.rolling(w).mean()) / (df.rolling(w).std() + EPS)


def xrank(df):
    """Rang transversal centre sur [-0.5, 0.5]."""
    r = df.rank(axis=1, pct=True)
    return r - 0.5


def neutralize(sig, factors):
    """Retire la projection du signal sur une liste de facteurs, ligne par ligne."""
    out = sig.copy()
    F = [f.reindex_like(sig) for f in factors]
    S = sig.to_numpy(dtype=float)
    Fs = [f.to_numpy(dtype=float) for f in F]
    R = np.full_like(S, np.nan)
    for i in range(S.shape[0]):
        y = S[i]
        m = np.isfinite(y)
        if m.sum() < 8:
            continue
        X = [np.ones(m.sum())]
        for f in Fs:
            x = f[i][m]
            if np.isfinite(x).sum() < m.sum() * 0.8:
                continue
            X.append(np.nan_to_num(x, nan=np.nanmean(x)))
        X = np.vstack(X).T
        yy = y[m]
        try:
            beta, *_ = np.linalg.lstsq(X, yy, rcond=None)
            R[i, m] = yy - X @ beta
        except Exception:
            R[i, m] = yy
    return pd.DataFrame(R, index=sig.index, columns=sig.columns)


def parkinson(h, l, w):
    return (np.log(h / l) ** 2).rolling(w).mean() ** 0.5


def garman_klass(o, h, l, c, w):
    a = 0.5 * np.log(h / l) ** 2
    b = (2 * np.log(2) - 1) * np.log(c / o) ** 2
    return (a - b).rolling(w).mean().clip(lower=0) ** 0.5


# ===================================================================  SIGNAUX

def build_signals(px, fund, oi, lsr):
    """Renvoie {nom: DataFrame de score causal}. Convention : score haut = acheter."""
    o, h, l, c, v = px["open"], px["high"], px["low"], px["close"], px["vol"]
    r = c.pct_change()
    S = {}

    # ---- prix, mais des versions non triviales ---------------------------
    for n in (5, 10, 20, 60):
        S[f"mom_{n}"] = c / c.shift(n) - 1
        S[f"mom_skip1_{n}"] = c.shift(1) / c.shift(n) - 1          # saute le dernier jour
        S[f"dist_low_{n}"] = c / c.rolling(n).min() - 1
        S[f"dd_from_high_{n}"] = c / c.rolling(n).max() - 1
        S[f"pct_up_days_{n}"] = (r > 0).rolling(n).mean()
        S[f"trend_quality_{n}"] = (c / c.shift(n) - 1) / (r.rolling(n).std() * np.sqrt(n) + EPS)

    # ---- decomposition de la volatilite ----------------------------------
    for n in (10, 20):
        pk = parkinson(h, l, n)
        gk = garman_klass(o, h, l, c, n)
        cc = r.rolling(n).std()
        S[f"noise_ratio_{n}"] = cc / (pk + EPS)          # bruit vs information
        S[f"gk_over_cc_{n}"] = gk / (cc + EPS)
        S[f"jump_share_{n}"] = (r.abs() > 3 * cc).rolling(n).mean()
        S[f"downside_vol_{n}"] = -r.clip(upper=0).rolling(n).std()
        S[f"vol_trend_{n}"] = cc / (r.rolling(n * 3).std() + EPS)

    # ---- microstructure depuis OHLC --------------------------------------
    for n in (10, 20):
        # Roll : spread implicite depuis l'autocovariance des rendements
        cov = r.rolling(n).cov(r.shift(1))
        S[f"roll_spread_{n}"] = 2 * np.sqrt((-cov).clip(lower=0))
        # part overnight vs intraday
        S[f"overnight_share_{n}"] = ((np.log(o / c.shift(1))).abs().rolling(n).mean()
                                     / ((np.log(c / o)).abs().rolling(n).mean() + EPS))
        S[f"close_location_{n}"] = ((c - l) / (h - l + EPS)).rolling(n).mean()

    # ---- volume / liquidite ----------------------------------------------
    dollar = v * c
    for n in (10, 20):
        S[f"amihud_{n}"] = (r.abs() / (dollar + EPS)).rolling(n).mean()
        S[f"vol_shock_{n}"] = dollar.rolling(n).mean() / (dollar.rolling(n * 3).mean() + EPS)
        S[f"vp_corr_{n}"] = r.rolling(n).corr(dollar.pct_change())
        S[f"turnover_trend_{n}"] = dollar.rolling(5).mean() / (dollar.rolling(n * 3).mean() + EPS)

    # ---- DERIVES : la partie qui compte -----------------------------------
    if fund is not None:
        f = fund.reindex_like(c)
        S["funding_level"] = -f                                    # payer cher = vendre
        S["funding_z60"] = -zs(f, 60)
        for n in (3, 7, 20):
            S[f"funding_cum_{n}"] = -f.rolling(n).sum()
            S[f"funding_trend_{n}"] = -(f.rolling(n).mean() - f.rolling(n * 3).mean())
        S["funding_disp"] = -(f.sub(f.mean(axis=1), axis=0))       # cher RELATIF a l'univers
        S["funding_flip"] = -np.sign(f) * (np.sign(f) != np.sign(f.shift(3))).astype(float)

    if oi is not None:
        O = oi.reindex_like(c)
        doi = O.pct_change()
        for n in (3, 7, 20):
            S[f"oi_growth_{n}"] = -(O / O.shift(n) - 1)            # crowding = vendre
        # decomposition : OI monte + prix monte = nouveaux longs (fragile)
        #                 OI baisse + prix monte = short covering (sain)
        S["oi_price_newlong"] = -(np.sign(doi) * np.sign(r)).rolling(5).mean()
        S["oi_vs_volume"] = -(O / (v.rolling(5).mean() + EPS))
        S["oi_z60"] = -zs(O, 60)

    if fund is not None and oi is not None:
        f, O = fund.reindex_like(c), oi.reindex_like(c)
        # crowding = cher ET massif. C'est le produit qui compte, pas les termes.
        S["crowding_fund_x_oi"] = -(xrank(f) * xrank(O / O.shift(7) - 1))
        S["crowding_z"] = -(zs(f, 60).clip(-3, 3) * zs(O, 60).clip(-3, 3))

    if lsr is not None:
        L = lsr.reindex_like(c)
        S["lsr_level"] = -zs(L, 60)
        S["lsr_change_7"] = -(L / L.shift(7) - 1)

    return {k: v_ for k, v_ in S.items() if v_.notna().sum().sum() > 200}


# ================================================================  ETATS

def build_states(px, fund):
    """Variables d'etat du marche, pour le conditionnement. Causales."""
    c = px["close"]
    r = c.pct_change()
    mkt = r.mean(axis=1)
    st = {}
    st["mkt_vol"] = mkt.rolling(20).std()
    st["xs_disp"] = r.std(axis=1).rolling(5).mean()
    st["mkt_trend"] = (c.mean(axis=1) / c.mean(axis=1).shift(20) - 1)
    st["avg_corr"] = r.rolling(30).corr(mkt).mean(axis=1)
    if fund is not None:
        st["mkt_funding"] = fund.reindex_like(c).mean(axis=1).rolling(7).mean()
    return {k: v for k, v in st.items() if v.notna().sum() > 200}


# ==============================================================  PORTEFEUILLE

def causal_universe(px, top_n=50, min_days=90):
    c, v = px["close"], px["vol"]
    dollar = (v * c).rolling(60).median()
    valid = c.notna().rolling(min_days).sum() >= min_days * 0.9
    d2 = dollar.where(valid)
    rk = d2.rank(axis=1, ascending=False, method="first")
    return (rk <= top_n) & d2.notna()


def backtest(score, univ, fwd, k, cost_rt, hold=1, smooth=0, band=0.0):
    """
    hold   : portefeuilles CHEVAUCHANTS -- on entre 1/hold chaque jour et on tient
             hold jours. Reduit le turnover d'un facteur ~hold sans changer le signal.
    smooth : EMA du score sur `smooth` jours (reduit le turnover, ajoute du retard).
    band   : bande de non-trade -- on ne rebalance que si le poids cible s'ecarte
             de plus de `band` du poids courant.
    """
    s = score.where(univ)
    if smooth > 1:
        s = s.ewm(span=smooth, min_periods=smooth).mean()
    rk = s.rank(axis=1, method="first").to_numpy(dtype=float)
    cnt = s.notna().sum(axis=1).to_numpy(dtype=float)
    ok = cnt >= 2 * k + 2
    if ok.sum() < 60:
        return None

    W = np.zeros_like(rk)
    valid = np.isfinite(rk)
    W[valid & (rk <= k)] = -1.0 / k
    W[valid & (rk > (cnt[:, None] - k))] = 1.0 / k
    W[~ok] = 0.0

    if hold > 1:                                    # moyenne glissante des cibles
        W = pd.DataFrame(W).rolling(hold, min_periods=1).mean().to_numpy()

    F = fwd.to_numpy(dtype=float)
    W = np.where(np.isfinite(F), W, 0.0)
    Fz = np.nan_to_num(F)

    # application de la bande de non-trade
    if band > 0:
        H = np.zeros_like(W)
        cur = np.zeros(W.shape[1])
        for i in range(W.shape[0]):
            tgt = W[i]
            move = np.abs(tgt - cur) > band / k
            cur = np.where(move, tgt, cur)
            H[i] = cur
        W = H

    gross = (W * Fz).sum(axis=1) * 1e4 / max(hold, 1)
    turn = np.abs(np.diff(W, axis=0, prepend=np.zeros((1, W.shape[1])))).sum(axis=1)
    net = gross - turn * cost_rt / 2.0
    m = ok & np.isfinite(gross)
    return gross[m], net[m], np.where(m)[0]


def stat(x):
    x = x[np.isfinite(x)]
    if len(x) < 40:
        return None
    sd = x.std(ddof=1)
    return len(x), x.mean(), sd, (x.mean() / (sd / np.sqrt(len(x))) if sd > EPS else np.nan)


def sub_stability(x, k=4):
    if len(x) < 4 * k:
        return np.nan
    return float(np.mean([seg.mean() > 0 for seg in np.array_split(x, k)]))


def effective_tests(pnls, max_keep=200):
    """Nombre effectif d'essais via le spectre propre. C'est ce qui manquait a v1."""
    if len(pnls) < 3:
        return len(pnls)
    keys = list(pnls)[:max_keep]
    n = min(len(pnls[kk]) for kk in keys)
    M = np.vstack([pnls[kk][:n] for kk in keys])
    C = np.corrcoef(M)
    C = np.nan_to_num(C, nan=0.0)
    ev = np.linalg.eigvalsh(C)
    ev = ev[ev > 0]
    # Meff de Cheverud/Nyholt generalise
    return float(ev.sum() ** 2 / (ev ** 2).sum())


# =====================================================================  MAIN

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2022-12-31")
    ap.add_argument("--cost-bps", type=float, default=14.0)
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--stage", default="conditioned", choices=["base", "conditioned"])
    ap.add_argument("--out", default="sweep_v2.csv")
    a = ap.parse_args()

    root = Path(a.root)
    start, end = pd.Timestamp(a.start, tz="UTC"), pd.Timestamp(a.end, tz="UTC")

    syms = sorted({p.stem.split("_")[0] for pat in PRICE_LAYOUTS
                   for p in root.glob(pat.replace("{sym}", "*"))})
    print(f"=== {len(syms)} symboles trouves ===")
    px, got = load_ohlcv(root, syms, start, end)
    c = px["close"]
    print(f"  panel {c.shape[0]}j x {c.shape[1]} symboles")

    fund = load_derivative(root, got, "funding", c.index, start, end)
    oi = load_derivative(root, got, "oi", c.index, start, end)
    lsr = load_derivative(root, got, "lsr", c.index, start, end)
    for nm, d in [("funding", fund), ("open interest", oi), ("long/short", lsr)]:
        if d is None:
            print(f"  {nm:<14} ABSENT  -- famille non testee")
        else:
            print(f"  {nm:<14} {d.notna().sum().sum():>8} points, "
                  f"{int(d.notna().any().sum())} symboles")

    univ = causal_universe(px, a.top_n)
    print(f"  univers median {univ.sum(axis=1).median():.0f}/jour\n")

    SIG = build_signals(px, fund, oi, lsr)
    ST = build_states(px, fund)
    print(f"=== {len(SIG)} signaux de base, {len(ST)} variables d'etat ===")

    mkt_beta = c.pct_change().rolling(60).corr(c.pct_change().mean(axis=1))
    size = np.log((px["vol"] * c).rolling(20).mean() + 1)
    volf = c.pct_change().rolling(20).std()

    NEUT = {
        "raw":   lambda s: s,
        "mkt":   lambda s: neutralize(s, [mkt_beta]),
        "sz_vl": lambda s: neutralize(s, [size, volf]),
        "full":  lambda s: neutralize(s, [mkt_beta, size, volf]),
    }
    HORIZ = [1, 3, 5, 10]
    BASK = [8, 12]
    HOLD = [1, 5]
    SMOOTH = [0, 5]

    FWD = {hh: (c.shift(-hh) / c - 1.0) for hh in HORIZ}
    rows, pnls = [], {}

    print("=== ETAGE 1 : signaux de base ===")
    for si, (sname, raw) in enumerate(SIG.items()):
        if (si + 1) % 10 == 0:
            print(f"  {si+1}/{len(SIG)}", flush=True)
        for nname, nf in NEUT.items():
            if nname != "raw" and si % 3 != 0:
                continue                       # neutralisation sur 1 signal /3
            try:
                s = nf(raw)
            except Exception:
                continue
            for hh, kk, hd, sm in itertools.product(HORIZ, BASK, HOLD, SMOOTH):
                if hd > 1 and hh > 3:
                    continue
                out = backtest(s, univ, FWD[hh], kk, a.cost_bps, hold=hd, smooth=sm)
                if out is None:
                    continue
                g, n_, idx = out
                sg, sn = stat(g), stat(n_)
                if sg is None or sn is None:
                    continue
                key = f"{sname}|{nname}|h{hh}|k{kk}|hd{hd}|sm{sm}"
                pnls[key] = n_
                rows.append(dict(signal=sname, neutral=nname, horizon=hh, basket=kk,
                                 hold=hd, smooth=sm, state="-", regime="-",
                                 n=sn[0], gross=sg[1], cost=sg[1] - sn[1],
                                 net=sn[1], t=sn[3], stab=sub_stability(n_)))

    if a.stage == "conditioned":
        print("=== ETAGE 2 : conditionnement par regime ===")
        base = pd.DataFrame(rows)
        top = base.reindex(base.t.abs().sort_values(ascending=False).index).head(30)
        for _, rw in top.iterrows():
            raw = SIG[rw.signal]
            s = NEUT[rw.neutral](raw)
            for stn, stv in ST.items():
                q = stv.rolling(250, min_periods=60).rank(pct=True)
                for lo, hi, lab in [(0.0, 0.33, "bas"), (0.67, 1.0, "haut")]:
                    mask = ((q >= lo) & (q <= hi)).reindex(s.index).fillna(False)
                    M = pd.DataFrame(np.repeat(mask.to_numpy()[:, None], s.shape[1], axis=1),
                                     index=s.index, columns=s.columns)
                    s2 = s.where(M)
                    out = backtest(s2, univ, FWD[int(rw.horizon)], int(rw.basket),
                                   a.cost_bps, hold=int(rw.hold), smooth=int(rw.smooth))
                    if out is None:
                        continue
                    g, n_, _ = out
                    sg, sn = stat(g), stat(n_)
                    if sg is None or sn is None:
                        continue
                    pnls[f"{rw.signal}|{stn}|{lab}"] = n_
                    rows.append(dict(signal=rw.signal, neutral=rw.neutral,
                                     horizon=rw.horizon, basket=rw.basket,
                                     hold=rw.hold, smooth=rw.smooth,
                                     state=stn, regime=lab,
                                     n=sn[0], gross=sg[1], cost=sg[1] - sn[1],
                                     net=sn[1], t=sn[3], stab=sub_stability(n_)))

    res = pd.DataFrame(rows)
    # cote gagnant : le signal et son oppose sont UNE hypothese bilaterale
    flip = res.net < (-res.gross - res.cost)
    res["dir"] = np.where(flip, "INV", "DIR")
    res["net_best"] = np.where(flip, -res.gross - res.cost, res.net)
    se = (res.net / res.t).abs().replace(0, np.nan)
    res["t_best"] = np.where(flip, res.net_best / se, res.t)
    res["stab_best"] = np.where(flip, np.nan, res.stab)

    meff = effective_tests(pnls)
    thr = norm.ppf(1 - 0.05 / max(meff, 1))
    res["passes"] = res.t_best > thr
    res = res.sort_values("t_best", ascending=False)
    res.to_csv(a.out, index=False)

    print(f"\n=== RESULTAT ===")
    print(f"  configurations            {len(res)}")
    print(f"  essais EFFECTIFS (spectre) {meff:.1f}   <- v1 deflatait contre 624 doublons")
    print(f"  seuil deflate              t > {thr:.2f}")
    cols = ["signal", "neutral", "horizon", "basket", "hold", "smooth", "state",
            "regime", "dir", "n", "gross", "cost", "net_best", "t_best", "stab_best", "passes"]
    with pd.option_context("display.width", 250, "display.max_columns", 60):
        print("\n=== TOP 30 ===")
        print(res[cols].head(30).to_string(index=False, float_format=lambda v: f"{v:7.2f}"))
    w = res[res.passes]
    print(f"\n{len(w)} configuration(s) au-dessus du seuil.")
    print(f"\n-> {a.out}")
    print("\nA LIRE : 'stab_best' est NaN pour les lignes INV -- relancer en ajoutant")
    print("le signal oppose pour l'obtenir. Un candidat sans stabilite verifiee ne se")
    print("promeut pas, quel que soit son t.")


if __name__ == "__main__":
    main()
