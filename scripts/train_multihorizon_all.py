#!/usr/bin/env python3
"""
scripts/train_multihorizon_all.py
─────────────────────────────────────────────────────────────────────────────
Industrialise l'ensemble MULTI-HORIZON — optimisation structurelle PAR horizon.

Pour chaque moteur (cascade, premium) et chaque horizon (1h/4h/8h) :
  • FINE-TUNING par horizon sur VALIDATION PURGÉE (jamais le test) : petite
    grille de régularisation, sélection par AUC val. Les horizons courts
    (bruités) reçoivent structurellement plus de régularisation.
  • bagging 5 graines sur la config gagnante.
Puis 3 façons d'exploiter la structure par terme, comparées à horizon commun 4h :
  BASELINE  : single 4h (l'actuel).
  TERM_HOLD : entrée 4h, sortie portée à 8h si le modèle 8h confirme.
  CONSENSUS : trade seulement si les 3 horizons sont d'accord (conviction).
  COMBINED  : consensus POUR l'entrée + term-hold POUR la sortie.

Gate anti-fishing : val purgée + embargo, sélection hyperparams sur val,
évaluation OOS pure. Sortie : reports/liq_cascade/MULTIHORIZON_ENGINE.{json,md}
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
COST = 0.0014
EMBARGO = pd.Timedelta(hours=8)
YEARS = [2023, 2024, 2025, 2026]
TOP = 0.20
N_BAG = 5

# grille de régularisation ; sélection sur VALIDATION (honnête)
GRID = [dict(num_leaves=8, reg_lambda=10.0, min_child_samples=50),
        dict(num_leaves=15, reg_lambda=5.0, min_child_samples=30),
        dict(num_leaves=24, reg_lambda=3.0, min_child_samples=20),
        dict(num_leaves=31, reg_lambda=1.0, min_child_samples=15)]

# config PAR moteur : dataset, features, horizons, horizon de trade & d'exit long
ENGINES = {
    "LIQ_CASCADE": {
        "path": ROOT / "data" / "events" / "liq_cascade_dataset.parquet",
        "feats": FEATURES_V2, "horizons": ["fwd_1h", "fwd_4h", "fwd_8h"],
        "trade_h": "fwd_4h", "exit_h": "fwd_8h"},
    "PREMIUM_DISLOCATION": {
        "path": ROOT / "data" / "events" / "premium_dataset.parquet",
        "feats": FEATURES_V2 + ["prem_at", "prem_z_at"],
        "horizons": ["fwd_1h", "fwd_4h", "fwd_8h"], "trade_h": "fwd_4h", "exit_h": "fwd_8h"},
    "CROWDING_REVERSAL": {
        "path": ROOT / "data" / "events" / "crowding_dataset.parquet",
        "feats": FEATURES_V2, "horizons": ["fwd_4h", "fwd_8h", "fwd_24h"],
        "trade_h": "fwd_8h", "exit_h": "fwd_24h", "min_train": 500},
}


def _pf(net):
    net = net[np.isfinite(net)]
    g = net[net > 0].sum(); l = abs(net[net < 0].sum())
    return float(g / l) if l > 0 else float("inf")


def _m(net):
    net = net[np.isfinite(net)]
    if not len(net):
        return {"n": 0, "pf": 0.0, "mean_bps": 0.0, "net_units": 0.0}
    return {"n": int(len(net)), "pf": round(_pf(net), 3),
            "mean_bps": round(float(net.mean()) * 1e4, 1),
            "net_units": round(float(net.sum()) * 1e4, 0)}


def _fit_horizon(tr, feats, horizon):
    """Fine-tune sur val purgée (grille régularisation) + bagging config gagnante."""
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    tr = tr.sort_values("event_time").reset_index(drop=True)
    ok = np.isfinite(tr[horizon].values)
    tr = tr[ok]
    cut = int(len(tr) * 0.85)
    vstart = tr["event_time"].iloc[cut]
    fit = tr[tr["event_time"] < (vstart - EMBARGO)]
    val = tr[tr["event_time"] >= vstart]
    yfit = (fit[horizon].values > COST).astype(int)
    yval = (val[horizon].values > COST).astype(int)
    wfit = np.clip(np.abs(fit[horizon].values - COST), None,
                   np.nanpercentile(np.abs(fit[horizon].values - COST), 95))
    best, best_auc = GRID[1], -1
    if 0 < yval.sum() < len(yval):
        for hp in GRID:
            mdl = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03, max_depth=6,
                                     subsample=0.8, colsample_bytree=0.7,
                                     random_state=0, verbose=-1, **hp)
            mdl.fit(fit[feats].values, yfit, sample_weight=wfit)
            auc = roc_auc_score(yval, mdl.predict_proba(val[feats].values)[:, 1])
            if auc > best_auc:
                best_auc, best = auc, hp
    # bagging sur config gagnante, ré-entraîné sur tout le train
    models = []
    y = (tr[horizon].values > COST).astype(int)
    w = np.clip(np.abs(tr[horizon].values - COST), None,
                np.nanpercentile(np.abs(tr[horizon].values - COST), 95))
    for k in range(N_BAG):
        mdl = lgb.LGBMClassifier(n_estimators=500, learning_rate=0.03, max_depth=6,
                                 subsample=0.7 + 0.05 * (k % 3),
                                 colsample_bytree=0.6 + 0.05 * (k % 3),
                                 random_state=k, verbose=-1, **best)
        mdl.fit(tr[feats].values, y, sample_weight=w)
        models.append(mdl)
    return models, best, round(best_auc, 4)


def _predict(models, X):
    return np.mean([m.predict_proba(X)[:, 1] for m in models], axis=0)


def _by_year_pf(net, years):
    out = {}
    for y in sorted(set(years)):
        n = net[(years == y)]
        n = n[np.isfinite(n)]
        if len(n) >= 20:
            out[int(y)] = round(_pf(n), 2)
    return out


def run_engine(name, cfg):
    ev = pd.read_parquet(cfg["path"])
    ev = ev[ev["label_full"]].copy()
    feats, horizons = cfg["feats"], cfg["horizons"]
    trade_h, exit_h = cfg["trade_h"], cfg["exit_h"]
    min_train = cfg.get("min_train", 2000)
    oos = {h: pd.Series(np.nan, index=ev.index) for h in horizons}
    chosen = {}
    for y in YEARS:
        tr = ev[ev["event_time"].dt.year < y]
        te = ev[ev["event_time"].dt.year == y]
        if len(tr) < min_train or len(te) < 30:
            continue
        for h in horizons:
            models, hp, auc = _fit_horizon(tr, feats, h)
            oos[h].loc[te.index] = _predict(models, te[feats].values)
            chosen.setdefault(h, []).append({"year": y, "hp": hp["num_leaves"], "val_auc": auc})
    for h in horizons:
        ev[f"p_{h}"] = oos[h]
    ev = ev.dropna(subset=[f"p_{h}" for h in horizons])
    if ev.empty:
        return {"note": "aucun fold valide (données insuffisantes)"}, chosen

    years = ev["event_time"].dt.year.values
    net_t = ev[trade_h].values - COST
    net_e = ev[exit_h].values - COST
    net_t2 = ev[trade_h].values - 2 * COST   # cost-stress ×2
    thr = {h: np.nanquantile(ev[f"p_{h}"], 1 - TOP) for h in horizons}
    sel = ev[f"p_{trade_h}"].values >= thr[trade_h]
    cons = np.ones(len(ev), dtype=bool)
    for h in horizons:
        cons &= ev[f"p_{h}"].values >= thr[h]
    holde = ev[f"p_{exit_h}"].values >= thr[exit_h]
    combined_net = np.where(holde, net_e, net_t)

    res = {
        "BASELINE": _m(net_t[sel]),
        "TERM_HOLD": _m(combined_net[sel]),
        "CONSENSUS": _m(net_t[cons]),
        "COMBINED": _m(combined_net[cons]),
        "COMBINED_costx2": _m(np.where(holde, net_e - COST, net_t2)[cons]),
        "COMBINED_by_year": _by_year_pf(combined_net[cons], years[cons]),
        "trade_h": trade_h, "exit_h": exit_h,
    }
    idx = np.flatnonzero(cons)
    pd.DataFrame({
        "event_time": ev["event_time"].values[idx], "symbol": ev["symbol"].values[idx],
        "net": combined_net[idx],
        "score": ev[[f"p_{h}" for h in horizons]].mean(axis=1).values[idx],
    }).to_parquet(OUT / f"{name}_MH_trades.parquet", index=False)
    return res, chosen


def main():
    all_res, all_chosen = {}, {}
    for name, cfg in ENGINES.items():
        if not cfg["path"].exists():
            print(f"SKIP {name} (dataset absent)"); continue
        print(f"── {name} (trade {cfg['trade_h']}, exit {cfg['exit_h']}) ──", flush=True)
        res, chosen = run_engine(name, cfg)
        all_res[name] = res; all_chosen[name] = chosen
        if "note" in res:
            print(" ", res["note"]); continue
        for k in ("BASELINE", "TERM_HOLD", "CONSENSUS", "COMBINED", "COMBINED_costx2"):
            v = res[k]
            print(f"  {k:16} n={v['n']:5} PF={v['pf']:.3f} mean={v['mean_bps']:+.1f}bps", flush=True)
        print(f"  COMBINED par année : {res['COMBINED_by_year']}", flush=True)

    L = ["# Moteur MULTI-HORIZON — optimisation structurelle par horizon (OOS)\n",
         "Fine-tuning régularisation par horizon sur VALIDATION purgée (jamais test). "
         "Chaque moteur à ses horizons ; comparaison à son horizon de trade.\n"]
    for name, res in all_res.items():
        if "note" in res:
            L.append(f"\n## {name}\n_{res['note']}_"); continue
        L.append(f"\n## {name} (trade {res['trade_h']}, exit {res['exit_h']})\n"
                 "| config | n | PF | mean bps | net (unités) |\n|---|---:|---:|---:|---:|")
        base = res["BASELINE"]["net_units"]
        for k in ("BASELINE", "TERM_HOLD", "CONSENSUS", "COMBINED", "COMBINED_costx2"):
            v = res[k]; up = f" (×{v['net_units']/base:.2f})" if base else ""
            L.append(f"| {k} | {v['n']} | {v['pf']} | {v['mean_bps']:+.1f} | {v['net_units']:+.0f}{up} |")
        L.append(f"\n_COMBINED PF par année : {res['COMBINED_by_year']}_")
    (OUT / "MULTIHORIZON_ENGINE.json").write_text(json.dumps(all_res, indent=2, default=str))
    (OUT / "MULTIHORIZON_ENGINE.md").write_text("\n".join(L))
    print("\n→ rapport écrit")


if __name__ == "__main__":
    main()
