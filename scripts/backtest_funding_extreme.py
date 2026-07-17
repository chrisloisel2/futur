#!/usr/bin/env python3
"""
scripts/backtest_funding_extreme.py
─────────────────────────────────────────────────────────────────────────────
BASIS_DISPERSION_V1 — funding extrême en NIVEAU (test pré-enregistré).

Ne re-teste PAS ce qui est déjà tranché : directionnel cross-exchange
(REJECTED), carry portefeuille (NON_VALIDATED_PORTFOLIO), stress-gate
dispersion (VALIDATED_SIGNAL). Hypothèse NOUVELLE ici : le NIVEAU extrême
du funding (pas le spread inter-exchange) comme filtre contrarian.

PROTOCOLE PRÉ-ENREGISTRÉ (avant tout calcul feature → retour) :

  PRIMAIRE (The Crypto Carry Trade, CMU : funding élevé coïncide avec les
  sommets locaux et précède les corrections)
    funding_z = z-score causal du funding 8 h, fenêtre 270 obs (90 j),
    min 180. Panel poolé sur tous les symboles avec funding + prix 1 h.
    fwd_ret_24h = close t+24h / close t − 1 (exécution barre suivante :
    fenêtre [t+1h, t+25h]).
    VERDICT SIGNAL_VALIDATED ssi TOUTES :
      P1  NW-t (lag 6) de fwd_ret_24h ~ funding_z ≤ −2,0 (panel, signe −)
      P2  signe négatif sur les 2 moitiés temporelles
      P3  coefficient négatif sur ≥ 2/3 des symboles individuels
      P4  Δ moyenne (funding_z > +2 vs reste) < 0, robuste au drop des
          10 pires événements du bucket extrême
    NOTE : un SIGNAL_VALIDATED reste un filtre de recherche — le câblage
    portefeuille doit passer le test d'impact V1.1 (leçon CARRY_GATE_V2).

  SECONDAIRES (exploratoires, pas de verdict, essais DSR = 5) :
    S1 horizon 72 h
    S2 basis premium 8 h z bas (< −2) → fwd_ret_24h positif (BTC/ETH/SOL)
    S3 divergence funding HL−Binance z → fwd 24 h (BTC/ETH/SOL)
    S4 asymétrie : funding_z < −2 → fwd 24 h ?
    S5 funding_z > +2 persistant 3 obs (24 h) vs ponctuel

Env : .venv Python 3.8.10.
Commande : .venv/bin/python scripts/backtest_funding_extreme.py
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FUND = ROOT / "data/derivatives_backfill/binance/funding"
PREM = ROOT / "data/derivatives_backfill/binance_vision_premium"
HL = ROOT / "data/derivatives_backfill/hyperliquid/funding"
ENR = ROOT / "data/enriched"
OUT = ROOT / "reports/BASIS_DISPERSION_V1_FUNDING_EXTREME.json"

Z_WIN, Z_MIN = 270, 180
NW_LAG = 6
EXTREME = 2.0


def nw_tstat(y, x, lag=NW_LAG):
    mask = np.isfinite(y) & np.isfinite(x)
    y, x = y[mask], x[mask]
    n = len(y)
    if n < 100:
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
    V = np.linalg.inv(X.T @ X / n) @ S @ np.linalg.inv(X.T @ X / n) / n
    return float(beta[1]), float(beta[1] / np.sqrt(V[1, 1])), n


def zscore_causal(s, win=Z_WIN, mn=Z_MIN):
    mu = s.rolling(win, min_periods=mn).mean()
    sd = s.rolling(win, min_periods=mn).std()
    return (s - mu) / sd.replace(0, np.nan)


def load_symbol(sym: str):
    """Funding 8 h + close 1 h -> DataFrame indexé sur funding timestamps."""
    fp = FUND / f"{sym}.parquet"
    ep = ENR / f"{sym}_1h_enriched.parquet"
    if not fp.exists() or not ep.exists():
        return None
    f = pd.read_parquet(fp)
    f["ts"] = pd.to_datetime(f["timestamp"], utc=True).dt.floor("h")
    f = f.set_index("ts").sort_index()[["funding_rate"]]
    e = pd.read_parquet(ep)
    tcol = "timestamp" if "timestamp" in e.columns else ("datetime" if "datetime" in e.columns else "open_time")
    e["ts"] = pd.to_datetime(e[tcol], utc=True).dt.floor("h")
    close = e.set_index("ts").sort_index()["close"]
    # fwd [t+1h, t+25h] et [t+1h, t+73h] — exécution barre suivante
    fwd24 = close.shift(-25) / close.shift(-1) - 1
    fwd72 = close.shift(-73) / close.shift(-1) - 1
    f["fwd24"] = fwd24.reindex(f.index)
    f["fwd72"] = fwd72.reindex(f.index)
    f["funding_z"] = zscore_causal(f["funding_rate"])
    f["symbol"] = sym
    return f.dropna(subset=["funding_z", "fwd24"])


def main():
    syms = sorted(p.stem for p in FUND.glob("*.parquet"))
    frames = {}
    for s in syms:
        d = load_symbol(s)
        if d is not None and len(d) > 500:
            frames[s] = d
    panel = pd.concat(frames.values()).sort_index()
    y, x = panel["fwd24"].values, panel["funding_z"].values

    beta, t_full, n_full = nw_tstat(y, x)
    # P2 moitiés temporelles
    cut = panel.index.sort_values()[len(panel) // 2]
    a, b = panel[panel.index <= cut], panel[panel.index > cut]
    b1, t1, _ = nw_tstat(a["fwd24"].values, a["funding_z"].values)
    b2, t2, _ = nw_tstat(b["fwd24"].values, b["funding_z"].values)
    # P3 par symbole
    per_sym = {}
    neg = 0
    for s, d in frames.items():
        bs, ts_, ns = nw_tstat(d["fwd24"].values, d["funding_z"].values)
        per_sym[s] = {"beta": round(bs, 6), "nw_t": round(ts_, 2), "n": ns}
        neg += int(bs < 0)
    # P4 bucket extrême
    ext = panel[panel["funding_z"] > EXTREME]
    rest = panel[panel["funding_z"] <= EXTREME]
    delta = float(ext["fwd24"].mean() - rest["fwd24"].mean())
    # robustesse : retirer du bucket extrême les 10 fwd24 les plus NÉGATIFS
    # (ceux qui aident le signal) et vérifier que le delta reste < 0
    keep_idx = ext["fwd24"].nlargest(len(ext) - 10).index if len(ext) > 10 else ext.index
    delta_drop = float(ext.loc[keep_idx, "fwd24"].mean() - rest["fwd24"].mean())

    p1 = bool(t_full <= -2.0)
    p2 = bool(b1 < 0 and b2 < 0)
    p3 = bool(neg >= int(np.ceil(2 / 3 * len(frames))))
    p4 = bool(delta < 0 and delta_drop < 0)
    verdict = "SIGNAL_VALIDATED" if (p1 and p2 and p3 and p4) else "NO_EDGE"

    # Secondaires
    sec = {}
    _, t72, n72 = nw_tstat(panel["fwd72"].values, panel["funding_z"].values, lag=10)
    sec["S1_fwd72"] = {"nw_t": round(t72, 2), "n": n72}
    # S2 basis premium z bas -> fwd24 positif
    s2 = {}
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        pp = PREM / f"{s}_premium_5m.parquet"
        if not pp.exists() or s not in frames:
            continue
        pr = pd.read_parquet(pp)
        pr["ts"] = pd.to_datetime(pr["ts"] if "ts" in pr.columns else pr["open_time"], utc=True)
        p8 = pr.set_index("ts")["premium"].resample("8h").mean()
        pz = zscore_causal(p8).rename("prem_z")
        d = frames[s].join(pz, how="left").dropna(subset=["prem_z"])
        lo = d[d["prem_z"] < -EXTREME]
        s2[s] = {"n_ext": int(len(lo)),
                 "fwd24_ext": round(float(lo["fwd24"].mean()), 5) if len(lo) else None,
                 "fwd24_all": round(float(d["fwd24"].mean()), 5)}
    sec["S2_premium_low"] = s2
    # S3 HL - Binance divergence
    s3 = {}
    for s, hl_name in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("SOLUSDT", "SOL")]:
        hp = HL / f"{hl_name}.parquet"
        if not hp.exists() or s not in frames:
            continue
        h = pd.read_parquet(hp)
        h["ts"] = pd.to_datetime(h["timestamp"], utc=True).dt.floor("h")
        # HL funding horaire -> somme 8 h pour comparabilité
        h8 = h.set_index("ts")["funding_rate"].rolling(8).sum().rename("hl_f")
        d = frames[s].join(h8, how="left").dropna(subset=["hl_f"])
        div = zscore_causal(d["hl_f"] - d["funding_rate"])
        _, td, nd = nw_tstat(d["fwd24"].values, div.values)
        s3[s] = {"nw_t": round(td, 2), "n": nd}
    sec["S3_hl_divergence"] = s3
    # S4 asymétrie basse
    low = panel[panel["funding_z"] < -EXTREME]
    sec["S4_low_extreme"] = {"n": int(len(low)),
                             "fwd24_mean": round(float(low["fwd24"].mean()), 5) if len(low) else None,
                             "fwd24_all": round(float(panel["fwd24"].mean()), 5)}
    # S5 persistance
    pers = panel.groupby("symbol", group_keys=False).apply(
        lambda d: (d["funding_z"] > EXTREME).rolling(3).sum() == 3)
    ext_pers = panel[pers.values]
    sec["S5_persistent_extreme"] = {"n": int(len(ext_pers)),
                                    "fwd24_mean": round(float(ext_pers["fwd24"].mean()), 5) if len(ext_pers) else None}

    result = {
        "test": "BASIS_DISPERSION_V1_FUNDING_EXTREME",
        "date": "2026-07-17",
        "symbols": list(frames.keys()),
        "sample": [str(panel.index.min()), str(panel.index.max())],
        "n_panel": n_full,
        "verdict": verdict,
        "primary": {
            "beta": round(beta, 6), "nw_t": round(t_full, 2),
            "half1": {"beta": round(b1, 6), "nw_t": round(t1, 2)},
            "half2": {"beta": round(b2, 6), "nw_t": round(t2, 2)},
            "n_symbols_negative": neg, "per_symbol": per_sym,
            "extreme_bucket": {"n": int(len(ext)), "delta_fwd24": round(delta, 5),
                               "delta_fwd24_drop10": round(delta_drop, 5)},
            "P1": p1, "P2": p2, "P3": p3, "P4": p4,
        },
        "secondaries_exploratory": sec,
        "notes": [
            "Complement des verdicts existants : ne re-teste ni le spread cross-exchange ni le carry portefeuille.",
            "SIGNAL_VALIDATED != alpha portefeuille — cablage a tester contre V1.1 (lecon CARRY_GATE_V2).",
        ],
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
