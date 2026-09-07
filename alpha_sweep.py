#!/usr/bin/env python3
"""
alpha_sweep.py -- balayage systematique de l'espace d'alphas transversaux dollar-neutres.

Usage:
    python3 alpha_sweep.py --root /home/qbee/futur --start 2020-01-01 --end 2022-12-31
    python3 alpha_sweep.py --root /home/qbee/futur --discover-only      # verifie la donnee

Sort un CSV classe: chaque ligne = (signal, horizon, taille de panier) avec
  - edge BRUT et edge NET separement (le signe s'inverse sur le brut, jamais sur le net)
  - t brut, t net
  - nombre d'episodes NON RECOUVRANTS (rebalancement = horizon)
  - stabilite par sous-periode
  - seuil de multiplicite du balayage lui-meme

METHODOLOGIE, non negociable:
  - signaux strictement causaux (uniquement du passe)
  - rendements forward, jamais chevauchants: on rebalance a l'horizon
  - dollar-neutre: long top K, short bottom K, ponderation egale, somme nulle
  - couts appliques sur le turnover reel
  - univers causal: liquidite minimale calculee sur fenetre anterieure
"""

import argparse, sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- decouverte

CANDIDATE_LAYOUTS = [
    ("enriched_1h",   "data/enriched/{sym}_1h_enriched.parquet"),
    ("forward_bars",  "data/alpha_foundry_v5/forward_bars/bars_1m/*/symbol={sym}/*.parquet"),
    ("vision_1m",     "data/derivatives_backfill/*/symbol={sym}/*.parquet"),
    ("klines_flat",   "data/*/{sym}*.parquet"),
    ("klines_deep",   "data/*/*/{sym}*.parquet"),
]


def discover(root: Path, verbose=True):
    """Trouve ou vivent les barres et sous quelle forme. Renvoie (layout, dict sym->paths)."""
    found = {}
    for name, pattern in CANDIDATE_LAYOUTS:
        star = pattern.replace("{sym}", "*")
        try:
            hits = list(root.glob(star))
        except ValueError:
            continue
        if not hits:
            continue
        syms = set()
        for h in hits:
            s = h.stem.split("_")[0]
            for part in h.parts:
                if part.startswith("symbol="):
                    s = part.split("=", 1)[1]
            if s.endswith("USDT") or s.endswith("USD"):
                syms.add(s)
        if len(syms) >= 5:
            found[name] = (pattern, sorted(syms), len(hits))
    if verbose:
        print("=== DECOUVERTE ===")
        if not found:
            print("  aucune disposition reconnue sous", root)
            print("  -> passe --bars-glob 'chemin/vers/*.parquet' explicitement")
        for name, (pat, syms, n) in found.items():
            print(f"  {name:<15} {len(syms):>3} symboles, {n:>6} fichiers   [{pat}]")
    return found


def load_panel(root: Path, layout_pattern: str, symbols, start, end, verbose=True):
    """Charge un panel (date x symbole) de close et volume, resample quotidien UTC."""
    frames = []
    for i, sym in enumerate(symbols):
        star = layout_pattern.replace("{sym}", sym)
        paths = sorted(root.glob(star))
        if not paths:
            continue
        parts = []
        for p in paths:
            try:
                df = pd.read_parquet(p)
            except Exception:
                continue
            cols = {c.lower(): c for c in df.columns}
            tcol = next((cols[c] for c in
                         ("datetime", "timestamp", "open_time", "open_time_ms", "asof_ns", "date")
                         if c in cols), None)
            ccol = next((cols[c] for c in ("close", "close_price", "c") if c in cols), None)
            vcol = next((cols[c] for c in ("volume", "quote_volume", "v") if c in cols), None)
            if tcol is None or ccol is None:
                continue
            t = df[tcol]
            if pd.api.types.is_integer_dtype(t):
                unit = "ns" if float(t.iloc[0]) > 1e15 else "ms"
                t = pd.to_datetime(t, unit=unit, utc=True)
            else:
                t = pd.to_datetime(t, utc=True)
            sub = pd.DataFrame({"t": t, "close": df[ccol].astype(float)})
            sub["vol"] = df[vcol].astype(float) if vcol else np.nan
            parts.append(sub)
        if not parts:
            continue
        d = pd.concat(parts).dropna(subset=["t", "close"]).sort_values("t")
        d = d.set_index("t").resample("1D").agg({"close": "last", "vol": "sum"})
        d["symbol"] = sym
        frames.append(d.reset_index())
        if verbose and (i + 1) % 10 == 0:
            print(f"    ... {i+1}/{len(symbols)} symboles", file=sys.stderr)
    if not frames:
        raise SystemExit("Aucune donnee chargee. Verifie --bars-glob.")
    panel = pd.concat(frames)
    panel = panel[(panel.t >= start) & (panel.t <= end)]
    close = panel.pivot(index="t", columns="symbol", values="close").sort_index()
    vol = panel.pivot(index="t", columns="symbol", values="vol").sort_index()
    return close, vol


# ---------------------------------------------------------------- signaux
# Chaque signal renvoie un DataFrame (date x symbole) de scores CAUSAUX.
# Convention: score eleve = on achete. La reversion renvoie donc -rendement passe.

def sig_reversal(close, vol, n):
    return -(close / close.shift(n) - 1.0)

def sig_momentum(close, vol, n):
    return close / close.shift(n) - 1.0

def sig_residual_reversal(close, vol, n):
    r = close / close.shift(n) - 1.0
    return -(r.sub(r.mean(axis=1), axis=0))

def sig_vol_scaled_reversal(close, vol, n):
    r = close.pct_change()
    s = r.rolling(20).std()
    return -((close / close.shift(n) - 1.0) / s.replace(0, np.nan))

def sig_amihud(close, vol, n):
    r = (close.pct_change()).abs()
    illiq = (r / vol.replace(0, np.nan)).rolling(n).mean()
    return illiq  # prime d'illiquidite: on achete l'illiquide

def sig_reversal_x_illiq(close, vol, n):
    rev = -(close / close.shift(n) - 1.0)
    r = (close.pct_change()).abs()
    illiq = (r / vol.replace(0, np.nan)).rolling(20).mean()
    return rev * illiq.rank(axis=1, pct=True)

def sig_reversal_lowvolume(close, vol, n):
    rev = -(close / close.shift(n) - 1.0)
    vr = vol.rolling(n).sum() / vol.rolling(60).mean().replace(0, np.nan) / n
    return rev * (1.0 / vr.replace(0, np.nan)).rank(axis=1, pct=True)

def sig_dist_from_high(close, vol, n):
    return -(close / close.rolling(n).max() - 1.0)

def sig_dist_from_low(close, vol, n):
    return close / close.rolling(n).min() - 1.0

def sig_vol_of_vol(close, vol, n):
    r = close.pct_change()
    return -r.rolling(n).std().rolling(n).std()

def sig_skew(close, vol, n):
    return -close.pct_change().rolling(max(n, 10)).skew()

def sig_range_compression(close, vol, n):
    r = close.pct_change()
    return -(r.rolling(n).std() / r.rolling(60).std().replace(0, np.nan))

def sig_volume_shock(close, vol, n):
    return -(vol.rolling(n).mean() / vol.rolling(60).mean().replace(0, np.nan))

def sig_beta_residual_rev(close, vol, n):
    r = close.pct_change()
    mkt = r.mean(axis=1)
    beta = r.rolling(60).cov(mkt).div(mkt.rolling(60).var(), axis=0)
    resid = r.sub(beta.mul(mkt, axis=0))
    return -resid.rolling(n).sum()

SIGNALS = {
    "reversal":            sig_reversal,
    "momentum":            sig_momentum,
    "residual_reversal":   sig_residual_reversal,
    "vol_scaled_reversal": sig_vol_scaled_reversal,
    "amihud_illiq":        sig_amihud,
    "reversal_x_illiq":    sig_reversal_x_illiq,
    "reversal_lowvolume":  sig_reversal_lowvolume,
    "dist_from_high":      sig_dist_from_high,
    "dist_from_low":       sig_dist_from_low,
    "vol_of_vol":          sig_vol_of_vol,
    "return_skew":         sig_skew,
    "range_compression":   sig_range_compression,
    "volume_shock":        sig_volume_shock,
    "beta_residual_rev":   sig_beta_residual_rev,
}


# ---------------------------------------------------------------- backtest

def causal_universe(close, vol, min_days=90, top_n=50):
    """Univers causal: les top_n par volume median sur 60j ANTERIEURS, recalcule mensuellement."""
    dollar = (vol * close).rolling(60).median()
    valid = (close.notna().rolling(min_days).sum() >= min_days * 0.9)
    d2 = dollar.where(valid)
    rk = d2.rank(axis=1, ascending=False, method="first")
    return (rk <= top_n) & d2.notna()


def prep_ranks(scores, univ):
    """Classe une fois pour toute la matrice. C'est ce qui rend le balayage tenable."""
    s = scores.where(univ)
    r = s.rank(axis=1, method="first")
    cnt = s.notna().sum(axis=1)
    return r.to_numpy(dtype=float), cnt.to_numpy(dtype=float)


def run_config(rank_np, cnt_np, fwd_np, dates_idx, horizon, k, cost_rt_bps):
    """Dollar-neutre, rebalancement = horizon, entierement vectorise."""
    sel = np.arange(0, rank_np.shape[0], horizon)
    dsel = np.asarray(dates_idx)[sel]
    R, C, F = rank_np[sel], cnt_np[sel], fwd_np[sel]
    ok = C >= (2 * k + 2)
    if ok.sum() < 20:
        return None
    R, C, F = R[ok], C[ok], F[ok]
    W = np.zeros_like(R)
    valid = np.isfinite(R)
    short = valid & (R <= k)
    long_ = valid & (R > (C[:, None] - k))
    W[short] = -1.0 / k
    W[long_] = 1.0 / k
    Fz = np.where(np.isfinite(F), F, 0.0)
    W = np.where(np.isfinite(F), W, 0.0)          # pas de rendement -> pas de position
    gross = (W * Fz).sum(axis=1) * 1e4
    dW = np.abs(np.diff(W, axis=0, prepend=np.zeros((1, W.shape[1]))))
    turn = dW.sum(axis=1)
    net = gross - turn * cost_rt_bps / 2.0
    keep = np.isfinite(gross)
    return gross[keep], net[keep], dsel[ok][keep]


def stats(x):
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return dict(n=n, mean=np.nan, t=np.nan, sd=np.nan)
    m, sd = x.mean(), x.std(ddof=1)
    return dict(n=n, mean=m, sd=sd, t=m / (sd / np.sqrt(n)) if sd > 0 else np.nan)


def subperiod_sign(x, dates, k=4):
    if len(x) < 4 * k:
        return np.nan
    idx = np.array_split(np.arange(len(x)), k)
    return float(np.mean([x[i].mean() > 0 for i in idx]))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--bars-glob", default=None, help="ex: 'data/enriched/{sym}_1h_enriched.parquet'")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2022-12-31")
    ap.add_argument("--cost-bps", type=float, default=14.0, help="cout aller-retour total")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--out", default="alpha_sweep_results.csv")
    ap.add_argument("--discover-only", action="store_true")
    a = ap.parse_args()

    root = Path(a.root)
    found = discover(root)
    if a.discover_only:
        return
    if a.bars_glob:
        pattern, symbols = a.bars_glob, None
        star = pattern.replace("{sym}", "*")
        symbols = sorted({p.stem.split("_")[0] for p in root.glob(star)})
    elif found:
        name = sorted(found, key=lambda k: -len(found[k][1]))[0]
        pattern, symbols, _ = found[name]
        print(f"\n-> disposition retenue: {name} ({len(symbols)} symboles)")
    else:
        raise SystemExit("Rien trouve. Utilise --bars-glob.")

    start = pd.Timestamp(a.start, tz="UTC")
    end = pd.Timestamp(a.end, tz="UTC")
    print(f"\n=== CHARGEMENT {a.start} -> {a.end} ===")
    close, vol = load_panel(root, pattern, symbols, start, end)
    print(f"  panel: {close.shape[0]} jours x {close.shape[1]} symboles")

    univ = causal_universe(close, vol, top_n=a.top_n)
    print(f"  univers median: {univ.sum(axis=1).median():.0f} symboles/jour")

    HORIZONS = [1, 2, 3, 5, 7, 14]
    BASKETS = [5, 10, 15, 20]
    PARAMS = [1, 2, 3, 5, 10, 20]

    rows = []
    total = 0
    dates_idx = list(range(len(close.index)))
    FWD = {h: (close.shift(-h) / close - 1.0).to_numpy(dtype=float) for h in HORIZONS}
    print(f"\n=== BALAYAGE ===")
    for sname, fn in SIGNALS.items():
        print(f"  {sname} ...", flush=True)
        for p in PARAMS:
            try:
                sc = fn(close, vol, p)
            except Exception:
                continue
            if sc.notna().sum().sum() < 100:
                continue
            rank_np, cnt_np = prep_ranks(sc, univ)
            for h in HORIZONS:
                for k in BASKETS:
                    if k * 2 + 2 > a.top_n:
                        continue
                    out = run_config(rank_np, cnt_np, FWD[h], dates_idx, h, k, a.cost_bps)
                    if out is None:
                        continue
                    g, n_, dts = out
                    if len(g) < 20:
                        continue
                    sg, sn = stats(g), stats(n_)
                    total += 1
                    rows.append(dict(
                        signal=sname, param=p, horizon_d=h, basket=k,
                        n_episodes=sg["n"],
                        gross_bps=sg["mean"], t_gross=sg["t"],
                        net_bps=sn["mean"], t_net=sn["t"],
                        sd_bps=sg["sd"],
                        stability=subperiod_sign(n_, dts),
                    ))
    res = pd.DataFrame(rows)
    if res.empty:
        raise SystemExit("Aucune configuration exploitable.")

    from scipy.stats import norm
    N = len(res)
    thr = norm.ppf(1 - 0.05 / N)
    res["passes_deflated"] = res.t_net.abs() > thr
    res["abs_t_net"] = res.t_net.abs()
    res = res.sort_values("abs_t_net", ascending=False)
    res.to_csv(a.out, index=False)

    print(f"\n{total} configurations testees.")
    print(f"Seuil deflate pour {N} essais : |t| > {thr:.2f}")
    print(f"Attendu sous le null a ce seuil : {N * 0.05 / N:.2f} faux positif\n")
    print("=== TOP 25 par |t_net| ===")
    cols = ["signal", "param", "horizon_d", "basket", "n_episodes",
            "gross_bps", "t_gross", "net_bps", "t_net", "stability", "passes_deflated"]
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print(res[cols].head(25).to_string(index=False, float_format=lambda v: f"{v:8.3f}"))
    print(f"\nResultats complets -> {a.out}")
    print("\nLIRE CE QUI SUIT AVANT D'INTERPRETER:")
    print("  * t_gross et t_net different: inverser le signe marche sur le BRUT, jamais sur le NET.")
    print("    Un signal a t_gross = -5.9 devient +5.9 en brut inverse, puis il faut RESOUSTRAIRE")
    print("    les couts. C'est fait automatiquement si tu ajoutes le signal inverse.")
    print("  * Ce balayage EST une campagne de multiplicite. Le seuil deflate ci-dessus n'est")
    print("    valide que si tu ne l'as lance QU'UNE FOIS, sur la periode d'exploration.")
    print("  * 'stability' = fraction des 4 sous-periodes ou le net est positif. Sous 0.75, jeter.")
    print("  * Ce qui sort d'ici est un CANDIDAT, jamais un resultat. Il doit ensuite passer")
    print("    la periode scellee, une seule fois, au seuil du nombre de finalistes promus.")


if __name__ == "__main__":
    main()
