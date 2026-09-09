#!/usr/bin/env python3
"""
forward_test_crowd_positioning.py -- LE test forward pre-enregistre, execute UNE fois.

Hypothese : reports/loop/prereg/FORWARD_CROWD_POSITIONING_V1.md, scellee sur la
branche orpheline `prereg/forward-crowd-positioning-v1`. Ce fichier est le code
dont le SHA-256 figure dans le pre-enregistrement : toute modification le rend
caduc, et le script le verifie lui-meme avant de calculer.

Ce qu'il fait, et rien d'autre :
  1. inscrit le regard au ledger AVANT tout calcul (fail-closed, temoin exige)
  2. refuse de tourner si les pins de code ne correspondent plus au prereg
  3. refuse de tourner si la fenetre ne commence pas a la date de scellement
     ou n'a pas encore MIN_DAYS jours utilisables (pas de coup d'oeil anticipe)
  4. construit le portefeuille exactement comme le prereg le decrit
  5. compare le t de Newey-West au seuil unilateral a n=1, ecrit le verdict

`--placebo` permute les RENDEMENTS entre symboles a l'interieur de chaque
journee (comme le placebo du harnais) : le lien signal->rendement est detruit,
la persistance du score et donc la rotation sont conservees, aucune statistique
signal->rendement n'est revelee. Il ne tourne que sur des fenetres PASSEES :
la fenetre forward ne se regarde pas avant la date du test, placebo compris.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import alpha_sweep_v4 as A      # noqa: E402  (pinne par SHA dans le prereg)
import look_ledger as L         # noqa: E402  (pinne par SHA dans le prereg)

PREREG = ROOT / "reports" / "loop" / "prereg" / "FORWARD_CROWD_POSITIONING_V1.md"
BRANCH = "prereg/forward-crowd-positioning-v1"

# ---- la configuration, derivee du mecanisme (justifiee dans le prereg) -------
SEAL_DATE = "2026-09-09"     # la fenetre commence au scellement, pas avant
MIN_DAYS = 819               # 2,24 ans : puissance 80 % a S=1,66, seuil 1,645
AUM = 200_000.0
ADV_FRAC = 0.01              # position <= 1 % de l'ADV 20 j
COST_BPS = 14.0              # hypothese declaree (I7 ouvert)
EXEC_LAG = 1                 # score en t, execute a close(t+1)
DECILE = 10                  # les extremes : decile haut contre decile bas
MIN_K = 5                    # taille minimale d'un panier
WINSOR = 0.50
NW_LAG = 2
ALPHA_ONE_SIDED = 0.05       # n = 1 hypothese sur cette fenetre
PINNED = ["tools/forward_test_crowd_positioning.py", "tools/alpha_sweep_v4.py",
          "tools/build_daily_cache.py", "tools/look_ledger.py",
          "src/institutional/live_alpha_lab/preregistration.py"]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pins_now() -> dict:
    return {f: sha256(ROOT / f) for f in PINNED}


def pins_in_prereg() -> dict:
    """Le bloc ```json pins``` du pre-enregistrement."""
    m = re.search(r"```json pins\n(.*?)\n```", PREREG.read_text(), re.S)
    if not m:
        raise SystemExit("prereg sans bloc de pins : refus")
    return json.loads(m.group(1))


def build(D, placebo=False, seed=0):
    """Rendements bruts/cout/net quotidiens en bps, et le detail par jour."""
    c = D["px_close"]
    ret1 = (c.shift(-1) / c - 1.0).clip(-WINSOR, WINSOR)          # close t -> t+1
    ret_exec = ret1.shift(-EXEC_LAG)                               # execute a t+lag
    score = D["met_glob_acct"]                                     # ratio L/S des comptes
    adv = D["px_quote_volume"].rolling(20).median()
    capacite = AUM / (2.0 * MIN_K) / ADV_FRAC                      # ADV minimal
    univ = score.notna() & c.notna() & (adv >= capacite)
    cost_sym = A.symbol_cost_bps(D, COST_BPS).reindex_like(c).fillna(COST_BPS * 4)

    rng = np.random.default_rng(seed)
    S = score.where(univ).to_numpy(float)
    R = ret_exec.to_numpy(float)
    C = cost_sym.to_numpy(float)
    T, N = S.shape
    W_prev = np.zeros(N)
    rows = []
    for i in range(T):
        s = S[i]
        ok = np.isfinite(s)
        n = int(ok.sum())
        k = max(MIN_K, n // DECILE)
        if n < 2 * k + 2 or not np.isfinite(R[i]).any():
            W = np.zeros(N)
        else:
            vals = s[ok]
            order = np.argsort(vals, kind="stable")
            idx = np.where(ok)[0]
            W = np.zeros(N)
            W[idx[order[:k]]] = +1.0 / k        # foule la MOINS longue : acheter
            W[idx[order[-k:]]] = -1.0 / k       # foule la PLUS longue : vendre
        r = R[i].copy()
        if placebo:                                   # rendements permutes entre symboles
            fin = np.where(np.isfinite(r))[0]
            if len(fin) > 2:
                r[fin] = r[rng.permutation(fin)]
        fin = np.isfinite(r)
        r = np.where(fin, r, 0.0)
        # DOLLAR-NEUTRALITE MAINTENUE. Un membre sans rendement a l'execution
        # (barre manquante, radiation) est retire et son cote est renormalise a
        # +-1 ; si un cote se vide, le jour est annule. Sans cela, 9 jours sur
        # 525 portaient jusqu'a 40 % d'exposition nette -- du beta involontaire.
        W = np.where(fin, W, 0.0)
        pos, neg = W > 0, W < 0
        if pos.any() and neg.any():
            W[pos] = W[pos] / W[pos].sum()
            W[neg] = W[neg] / (-W[neg].sum())
        else:
            W = np.zeros(N)
        gross = float((W * r).sum() * 1e4)
        cost = float((np.abs(W - W_prev) * C[i] / 2.0).sum())
        traded = bool(np.abs(W).sum() > 0)
        expo = float(W.sum())                       # doit valoir 0 : dollar-neutre
        rows.append((c.index[i], gross, cost, gross - cost, n, k, traded, expo))
        W_prev = W
    df = pd.DataFrame(rows, columns=["date", "gross", "cost", "net", "n_univ", "k", "traded",
                                     "net_exposure"]).set_index("date")
    return df[df.traded]


def stats(df):
    net, gross = df.net.to_numpy(), df.gross.to_numpy()
    n = len(net)
    eq = (1 + net / 2 / 1e4).cumprod()
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    sk = float(pd.Series(net).skew()) if n > 3 else float("nan")
    seg = np.array_split(net, 4) if n >= 16 else []
    return {
        "n_days": n, "gross_bps": round(float(gross.mean()), 3), "cost_bps": round(float(df.cost.mean()), 3),
        "net_bps": round(float(net.mean()), 3),
        "t_net_nw": round(float(A.newey_west_t(net, NW_LAG)), 4),
        "t_gross_nw": round(float(A.newey_west_t(gross, NW_LAG)), 4),
        "sharpe_arith_1x": round(float(net.mean() / net.std() * np.sqrt(365.25)), 4) if net.std() > 0 else None,
        "max_dd_1x": round(dd, 4), "skew_daily": round(sk, 3),
        "sous_periodes_positives": int(sum(s.mean() > 0 for s in seg)) if seg else None,
        "univers_median": int(df.n_univ.median()), "k_median": int(df.k.median()),
        "expo_nette_abs_max": round(float(df.net_exposure.abs().max()), 4),
        "jours_non_neutres": int((df.net_exposure.abs() > 1e-9).sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--placebo", action="store_true", help="scores permutes : test du pipeline, pas un regard")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    thr = NormalDist().inv_cdf(1 - ALPHA_ONE_SIDED)      # n = 1, unilateral

    if a.placebo and a.end >= SEAL_DATE:
        raise SystemExit(f"REFUS : pas de placebo sur la fenetre forward (>= {SEAL_DATE}) "
                         "avant la date du test. Elle ne se regarde pas, placebo compris.")
    if not a.placebo:
        # --- les trois refus, avant tout calcul -------------------------------
        if a.start != SEAL_DATE:
            raise SystemExit(f"REFUS : la fenetre doit commencer au scellement {SEAL_DATE}, pas {a.start}")
        want, have = pins_in_prereg(), pins_now()
        diff = {k: (want.get(k), have.get(k)) for k in want if want.get(k) != have.get(k)}
        if diff:
            raise SystemExit(f"REFUS : le code ne correspond plus au pre-enregistrement : {list(diff)}")
        cfg = {"hypothese": "FORWARD_CROWD_POSITIONING_V1", "window": [a.start, a.end],
               "decile": DECILE, "min_k": MIN_K, "exec_lag": EXEC_LAG, "cost_bps": COST_BPS,
               "aum": AUM, "adv_frac": ADV_FRAC, "dir": "DIR", "threshold_one_sided_n1": round(thr, 4)}
        try:
            ent = L.record("confirm", (a.start, a.end), [cfg], prereg=str(PREREG),
                           require_witness=True, witness_branch=BRANCH,
                           note="FORWARD_CROWD_POSITIONING_V1 : le regard unique")
        except L.LedgerError as e:
            raise SystemExit(f"REFUS : {e}")
        print(f"ledger seq={ent['seq']} temoin pushed={ent['prereg']['pushed']}", flush=True)

    D = A.load_cache(a.root, a.start, a.end)
    df = build(D, placebo=a.placebo, seed=a.seed)
    if not a.placebo and len(df) < MIN_DAYS:
        raise SystemExit(f"REFUS : {len(df)} jours utilisables < {MIN_DAYS}. Le regard est inscrit "
                         f"mais la fenetre n'est pas mure : ce refus COMPTE.")
    st = stats(df)
    st.update({"mode": "PLACEBO (rendements permutes entre symboles, par jour)" if a.placebo else "REEL", "window": [a.start, a.end],
               "threshold_one_sided_n1": round(thr, 4),
               "passe": (None if a.placebo else bool(st["t_net_nw"] >= thr))})
    out = ROOT / "reports" / "loop" / "forward"
    out.mkdir(parents=True, exist_ok=True)
    tag = "placebo" if a.placebo else "REEL"
    (out / f"FORWARD_CROWD_POSITIONING_V1_{tag}_{a.start}_{a.end}.json").write_text(json.dumps(st, indent=2))
    for k, v in st.items():
        print(f"  {k:26s} {v}")


if __name__ == "__main__":
    main()
