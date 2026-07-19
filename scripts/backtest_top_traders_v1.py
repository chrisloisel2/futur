#!/usr/bin/env python3
"""
scripts/backtest_top_traders_v1.py
─────────────────────────────────────────────────────────────────────────────
TOP_TRADERS_V1 — divergence top-traders vs retail (test pré-enregistré).

DÉCOUVERTE DATA (2026-07-18) : les Vision metrics 5 m déjà backfillés
contiennent les ratios top-traders depuis 2021-12 (~49 symboles, 4,6 ans).
La prémisse « attendre 60-90 j d'archive live » était fausse — l'API ne
retient que 30 j mais Vision publie l'historique complet.

PROTOCOLE PRÉ-ENREGISTRÉ (avant tout calcul feature → retour) — les 4
sous-signaux étaient déclarés dans research/edge_factory/top_traders/
README.md avant tout contact avec ces données. Un seul PRIMAIRE :

  PRIMAIRE (sous-signal « lead/divergence » : les tops se positionnent
  sans euphorie retail)
    div = z30j(top_pos_ratio) − z30j(global_acct_ratio)   [1 h, causal]
    top_pos_ratio  = sum_toptrader_long_short_ratio  (pondéré positions)
    global_ratio   = count_long_short_ratio          (comptes retail)
    fwd24 = close[t+25h]/close[t+1h] − 1  (exécution barre suivante)
    Panel sur tous les symboles metrics ∩ enriched, 2021-12 → 2026-06.
    VERDICT SIGNAL_VALIDATED ssi TOUTES :
      P1  NW-t (lag 24) de fwd24 ~ div ≥ 2,0 (signe +)
      P2  signe + sur les 2 moitiés temporelles
      P3  coefficient + sur ≥ 2/3 des symboles
      P4  bucket div > +2 : Δ fwd24 > 0, robuste au drop des 10 meilleurs
          événements du bucket

  SECONDAIRES (exploratoires, essais DSR = 5) :
    S1 conviction : z(top_pos) − z(top_acct) (moins de comptes, plus gros)
    S2 distribution : top_pos en baisse & global en hausse (Δ7j) → fwd négatif
    S3 contrarian extrême : z(top_pos) > 2 ET z(global) > 2 → fwd négatif
    S4 interaction taker : div × z(taker_ratio)
    S5 horizon 4 h

Env : .venv Python 3.8.10.
Commande : .venv/bin/python scripts/backtest_top_traders_v1.py
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MET = ROOT / "data/derivatives_backfill/binance_vision_metrics"
ENR = ROOT / "data/enriched"
OUT = ROOT / "reports/TOP_TRADERS_V1_VERDICT.json"

Z_WIN, Z_MIN = 720, 360   # 30 j en heures
NW_LAG = 24
EXTREME = 2.0


def nw_tstat(y, x, lag=NW_LAG):
    mask = np.isfinite(y) & np.isfinite(x)
    y, x = y[mask], x[mask]
    n = len(y)
    if n < 500:
        return np.nan, np.nan, n
    X = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    u = y - X @ beta
    Xu = X * u[:, None]
    S = Xu.T @ Xu / n
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        G = Xu[k:].T @ Xu[:-k] / n
        S += w * (G + G.T)
    Vi = np.linalg.inv(X.T @ X / n)
    V = Vi @ S @ Vi / n
    return float(beta[1]), float(beta[1] / np.sqrt(V[1, 1])), n


def zc(s):
    mu = s.rolling(Z_WIN, min_periods=Z_MIN).mean()
    sd = s.rolling(Z_WIN, min_periods=Z_MIN).std()
    return (s - mu) / sd.replace(0, np.nan)


def load_symbol(sym):
    mf = MET / f"{sym}_metrics_5m.parquet"
    ef = ENR / f"{sym}_1h_enriched.parquet"
    if not mf.exists() or not ef.exists():
        return None
    m = pd.read_parquet(mf, columns=["create_time", "sum_toptrader_long_short_ratio",
                                     "count_toptrader_long_short_ratio",
                                     "count_long_short_ratio",
                                     "sum_taker_long_short_vol_ratio"])
    m["ts"] = pd.to_datetime(m["create_time"], utc=True)
    h = m.set_index("ts").sort_index().resample("1h").last()
    e = pd.read_parquet(ef, columns=["datetime", "close"])
    close = e.assign(ts=pd.to_datetime(e["datetime"], utc=True)).set_index("ts")["close"].sort_index()
    h["fwd24"] = (close.shift(-25) / close.shift(-1) - 1).reindex(h.index)
    h["fwd4"] = (close.shift(-5) / close.shift(-1) - 1).reindex(h.index)
    h["z_top_pos"] = zc(h["sum_toptrader_long_short_ratio"])
    h["z_top_acct"] = zc(h["count_toptrader_long_short_ratio"])
    h["z_glob"] = zc(h["count_long_short_ratio"])
    h["z_taker"] = zc(h["sum_taker_long_short_vol_ratio"])
    h["div"] = h["z_top_pos"] - h["z_glob"]
    h["d7_top"] = h["sum_toptrader_long_short_ratio"].diff(168)
    h["d7_glob"] = h["count_long_short_ratio"].diff(168)
    h["symbol"] = sym
    return h.dropna(subset=["div", "fwd24"])


def main():
    syms = sorted(p.name.replace("_metrics_5m.parquet", "")
                  for p in MET.glob("*_metrics_5m.parquet"))
    frames = {}
    for s in syms:
        d = load_symbol(s)
        if d is not None and len(d) > 5000:
            frames[s] = d
    panel = pd.concat(frames.values()).sort_index()

    y, x = panel["fwd24"].values, panel["div"].values
    beta, t_full, n_full = nw_tstat(y, x)
    cut = panel.index.sort_values()[len(panel) // 2]
    a, b = panel[panel.index <= cut], panel[panel.index > cut]
    b1, t1, _ = nw_tstat(a["fwd24"].values, a["div"].values)
    b2, t2, _ = nw_tstat(b["fwd24"].values, b["div"].values)
    pos = 0
    per_sym = {}
    for s, d in frames.items():
        bs, ts_, ns = nw_tstat(d["fwd24"].values, d["div"].values)
        per_sym[s] = {"beta": round(bs, 6) if np.isfinite(bs) else None,
                      "nw_t": round(ts_, 2) if np.isfinite(ts_) else None, "n": ns}
        pos += int(np.isfinite(bs) and bs > 0)
    ext = panel[panel["div"] > EXTREME]
    rest = panel[panel["div"] <= EXTREME]
    delta = float(ext["fwd24"].mean() - rest["fwd24"].mean())
    keep = ext["fwd24"].nsmallest(max(0, len(ext) - 10)).index if len(ext) > 10 else ext.index
    delta_drop = float(ext.loc[keep, "fwd24"].mean() - rest["fwd24"].mean())

    p1 = bool(t_full >= 2.0)
    p2 = bool(b1 > 0 and b2 > 0)
    p3 = bool(pos >= int(np.ceil(2 / 3 * len(frames))))
    p4 = bool(delta > 0 and delta_drop > 0)
    verdict = "SIGNAL_VALIDATED" if (p1 and p2 and p3 and p4) else "NO_EDGE"

    sec = {}
    conv = panel["z_top_pos"] - panel["z_top_acct"]
    _, t_c, n_c = nw_tstat(panel["fwd24"].values, conv.values)
    sec["S1_conviction"] = {"nw_t": round(t_c, 2), "n": n_c}
    dist = panel[(panel["d7_top"] < 0) & (panel["d7_glob"] > 0)]
    sec["S2_distribution"] = {"n": int(len(dist)),
                              "fwd24_mean": round(float(dist["fwd24"].mean()), 5),
                              "fwd24_all": round(float(panel["fwd24"].mean()), 5)}
    both = panel[(panel["z_top_pos"] > EXTREME) & (panel["z_glob"] > EXTREME)]
    sec["S3_contrarian_both_extreme"] = {"n": int(len(both)),
                                         "fwd24_mean": round(float(both["fwd24"].mean()), 5) if len(both) else None}
    inter = panel["div"] * panel["z_taker"]
    _, t_i, n_i = nw_tstat(panel["fwd24"].values, inter.values)
    sec["S4_div_x_taker"] = {"nw_t": round(t_i, 2), "n": n_i}
    _, t_4, n_4 = nw_tstat(panel["fwd4"].values, panel["div"].values, lag=6)
    sec["S5_fwd4h"] = {"nw_t": round(t_4, 2), "n": n_4}

    result = {
        "test": "TOP_TRADERS_V1",
        "date": "2026-07-18",
        "n_symbols": len(frames),
        "sample": [str(panel.index.min()), str(panel.index.max())],
        "n_panel": n_full,
        "verdict": verdict,
        "primary": {
            "beta": round(beta, 6), "nw_t": round(t_full, 2),
            "half1": {"beta": round(b1, 6), "nw_t": round(t1, 2)},
            "half2": {"beta": round(b2, 6), "nw_t": round(t2, 2)},
            "n_symbols_positive": pos,
            "extreme_bucket": {"n": int(len(ext)), "delta_fwd24": round(delta, 5),
                               "delta_fwd24_drop10": round(delta_drop, 5)},
            "P1": p1, "P2": p2, "P3": p3, "P4": p4,
        },
        "per_symbol_top10_abs_t": dict(sorted(per_sym.items(),
                                              key=lambda kv: -abs(kv[1]["nw_t"] or 0))[:10]),
        "secondaries_exploratory": sec,
        "notes": [
            "Ratios top-traders Vision metrics 5m depuis 2021-12 — la retention API 30j ne s'applique qu'au live.",
            "Panel inter-actifs correle (memes crashs) — NW lag 24 attenue mais ne supprime pas.",
        ],
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
