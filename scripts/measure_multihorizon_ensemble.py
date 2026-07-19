#!/usr/bin/env python3
"""
scripts/measure_multihorizon_ensemble.py
─────────────────────────────────────────────────────────────────────────────
Teste l'intuition « copies du modèle sur plusieurs horizons → vue globale ».

On entraîne le MÊME modèle (LightGBM sur FEATURES_V2) à 3 horizons (1h/4h/8h)
en walk-forward, puis on compare 4 façons de s'en servir, à horizon de trade
COMMUN (4h) pour une comparaison juste :

  A. SINGLE_4H   : le modèle actuel (baseline) — 1 horizon.
  B. ENSEMBLE    : moyenne des 3 probas → sélection top-20%.
  C. CONSENSUS   : les 3 horizons d'accord (chacun ≥ son q80) → conviction ↑.
  D. TERM_HOLD   : sélection 4h MAIS sortie portée à 8h si le modèle 8h est
                   aussi positif (structure par terme sur l'EXIT).

Question rigoureuse : la corrélation des probas inter-horizons dit si la
diversification est réelle ou illusoire. Sortie :
reports/liq_cascade/MULTIHORIZON_ENSEMBLE.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.engines.liq_cascade.dataset import FEATURES_V2

OUT = ROOT / "reports" / "liq_cascade"
CACHE = ROOT / "data" / "events" / "liq_cascade_dataset.parquet"
COST = 0.0014
HORIZONS = ["fwd_1h", "fwd_4h", "fwd_8h"]
TRADE_H = "fwd_4h"          # horizon de trade commun (comparaison juste)
TOP = 0.20
YEARS = [2023, 2024, 2025, 2026]


def pf(net):
    net = net[np.isfinite(net)]
    g = net[net > 0].sum(); l = abs(net[net < 0].sum())
    return float(g / l) if l > 0 else float("inf")


def m(net):
    net = net[np.isfinite(net)]
    return {"n": int(len(net)), "pf": round(pf(net), 3),
            "mean_bps": round(float(net.mean()) * 1e4, 1) if len(net) else 0.0,
            "wr": round(float((net > 0).mean()), 3) if len(net) else 0.0}


def main():
    ev = pd.read_parquet(CACHE)
    ev = ev[ev["label_full"]].copy()
    import lightgbm as lgb

    # scores OOS par horizon, walk-forward
    oos = {h: pd.Series(np.nan, index=ev.index) for h in HORIZONS}
    for y in YEARS:
        tr = ev[ev["event_time"].dt.year < y]
        te = ev[ev["event_time"].dt.year == y]
        if len(tr) < 2000 or len(te) < 30:
            continue
        for h in HORIZONS:
            yb = (tr[h].values > COST).astype(int)
            w = np.clip(np.abs(tr[h].values - COST), None,
                        np.nanpercentile(np.abs(tr[h].values - COST), 95))
            mdl = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03,
                                     num_leaves=15, max_depth=5, min_child_samples=30,
                                     subsample=0.8, colsample_bytree=0.7, reg_lambda=5.0,
                                     random_state=0, verbose=-1)
            ok = np.isfinite(tr[h].values)
            mdl.fit(tr[FEATURES_V2].values[ok], yb[ok], sample_weight=w[ok])
            oos[h].loc[te.index] = mdl.predict_proba(te[FEATURES_V2].values)[:, 1]

    for h in HORIZONS:
        ev[f"p_{h}"] = oos[h]
    ev = ev.dropna(subset=[f"p_{h}" for h in HORIZONS])

    # corrélation des probas inter-horizons (diversification réelle ?)
    corr = ev[[f"p_{h}" for h in HORIZONS]].corr().round(3)

    net_trade = ev[TRADE_H].values - COST
    net_8h = ev["fwd_8h"].values - COST
    res = {}

    # A. SINGLE 4h
    thrA = np.nanquantile(ev["p_fwd_4h"], 1 - TOP)
    res["A_single_4h"] = m(net_trade[ev["p_fwd_4h"].values >= thrA])
    # B. ENSEMBLE (moyenne 3 probas)
    ens = ev[[f"p_{h}" for h in HORIZONS]].mean(axis=1).values
    res["B_ensemble_mean"] = m(net_trade[ens >= np.nanquantile(ens, 1 - TOP)])
    # C. CONSENSUS (3 horizons ≥ q80 chacun)
    cons = np.ones(len(ev), dtype=bool)
    for h in HORIZONS:
        cons &= ev[f"p_{h}"].values >= np.nanquantile(ev[f"p_{h}"], 1 - TOP)
    res["C_consensus"] = m(net_trade[cons])
    # D. TERM_HOLD : sélection 4h, exit 8h si p_8h aussi haut (sinon 4h)
    sel = ev["p_fwd_4h"].values >= thrA
    hold8 = sel & (ev["p_fwd_8h"].values >= np.nanquantile(ev["p_fwd_8h"], 1 - TOP))
    net_term = np.where(hold8, net_8h, net_trade)
    res["D_term_hold"] = m(net_term[sel])

    L = ["# Multi-horizon ensemble — mesuré (cascade, walk-forward, net de frais)\n",
         "## Corrélation des probabilités inter-horizons\n", corr.to_markdown(),
         "\n_Si ~1.0 → les horizons disent la même chose, diversification illusoire._\n",
         "\n## Résultats à horizon de trade commun (4h)\n",
         "| config | n | PF | mean bps | WR |", "|---|---:|---:|---:|---:|"]
    labels = {"A_single_4h": "A · single 4h (ACTUEL)", "B_ensemble_mean": "B · ensemble (moy 3)",
              "C_consensus": "C · consensus (3 d'accord)", "D_term_hold": "D · term-structure exit"}
    for k, lab in labels.items():
        r = res[k]
        L.append(f"| {lab} | {r['n']} | {r['pf']} | {r['mean_bps']:+.1f} | {r['wr']} |")
    base = res["A_single_4h"]["pf"]
    best = max(res, key=lambda k: res[k]["pf"])
    L.append(f"\n**Verdict** : meilleur = {labels[best]} (PF {res[best]['pf']} vs baseline {base}). "
             + ("L'ensemble AIDE." if res[best]['pf'] > base * 1.05
                else "L'ensemble n'améliore PAS significativement (horizons trop corrélés)."))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "MULTIHORIZON_ENSEMBLE.json").write_text(json.dumps(
        {"corr": corr.to_dict(), "results": res}, indent=2, default=str))
    (OUT / "MULTIHORIZON_ENSEMBLE.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
