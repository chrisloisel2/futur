#!/usr/bin/env python3
"""
scripts/train_event_engine.py
─────────────────────────────────────────────────────────────────────────────
Harnais walk-forward UNIFIÉ pour les moteurs événementiels (même méthodo que
LIQ_CASCADE v2b : val purgée chrono + embargo, sample weights |ret−coût| p95,
bagging 5 graines, sélection quantile top 20% des scores val, sim concurrence
bornée). RÈGLES DE VERDICT identiques et pré-déclarées : fold valide ⟺ train ≥
2000 events ; CANDIDATE ⟺ ≥3 folds PF ≥ 1.35 ET aucun fold valide destructeur.

  python3 scripts/train_event_engine.py --engine crowding    # CROWDING_REVERSAL (24h)
  python3 scripts/train_event_engine.py --engine premium     # PREMIUM_DISLOCATION (4h)

Sortie : reports/liq_cascade/{ENGINE}_WALKFORWARD.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.engines.liq_cascade.dataset import (
    FEATURES_V2, build_event_dataset)
from src.institutional.engines.liq_cascade.detector import CascadeConfig


def load_universe(root: Path) -> list[str]:
    """Univers de trading FIGÉ (jamais dérivé du contenu d'un dossier de données :
    un backfill non lié a fait passer ce dossier de 50 à 312 fichiers le
    2026-08-14 et a fait basculer le verdict walk-forward de CANDIDATE à
    NO_EDGE en une semaine, voir
    reports/edge_discovery/alpha_hunt_2026-08-29/w1_liq_cascade/REPORT.md §1.3)."""
    return sorted(yaml.safe_load(
        (root / "configs/portfolio_v1_1_parallel_50.yaml").read_text())["universe"])

OUT_DIR = ROOT / "reports" / "liq_cascade"
CACHE_DIR = ROOT / "data" / "events"
COST_RT = 0.0014
COST_RT_STRESS = 0.0028
SIZING = 0.10
EMBARGO = pd.Timedelta(hours=8)
N_BAG = 5
TOP_FRAC = 0.20
MIN_TRAIN_EVENTS = 2000

ENGINES = {
    "cascade": {
        "name": "LIQ_CASCADE",           # même moteur que v2b, via harnais unifié
        "horizon": "fwd_4h", "hold_h": 4, "max_concurrent": 3,
        "features": FEATURES_V2,
    },
    "crowding": {
        "name": "CROWDING_REVERSAL",
        "horizon": "fwd_24h", "hold_h": 24, "max_concurrent": 5,
        "features": FEATURES_V2,
    },
    "premium": {
        "name": "PREMIUM_DISLOCATION",
        "horizon": "fwd_4h", "hold_h": 4, "max_concurrent": 3,
        "features": FEATURES_V2 + ["prem_at", "prem_z_at"],
    },
    "ignition": {
        "name": "FLOW_IGNITION",       # expansion OI + taker = continuation
        "horizon": "fwd_8h", "hold_h": 8, "max_concurrent": 3,
        "features": FEATURES_V2 + ["taker_z_at"],
    },
    "spillover": {
        "name": "BTC_SPILLOVER",       # lead-lag BTC → alt retardataire
        "horizon": "fwd_4h", "hold_h": 4, "max_concurrent": 3,
        "features": FEATURES_V2 + ["btc_ret_1h_at", "alt_ret_1h_at", "lag_gap"],
    },
}


def _detector(engine: str):
    if engine == "cascade":
        return None    # défaut du pipeline = detect_cascades
    if engine == "crowding":
        from src.institutional.engines.crowding_reversal.detector import detect_washouts
        return detect_washouts
    if engine == "ignition":
        from src.institutional.engines.flow_ignition.detector import detect_ignitions
        return detect_ignitions
    if engine == "spillover":
        from src.institutional.engines.btc_spillover.detector import detect_spillovers
        return detect_spillovers
    from src.institutional.engines.premium_dislocation.detector import (
        detect_premium_dislocations)
    return detect_premium_dislocations


def _metrics(net: np.ndarray) -> dict:
    net = net[np.isfinite(net)]
    if len(net) == 0:
        return {"n": 0, "pf": 0.0, "wr": 0.0, "mean_net_bps": 0.0, "roi_sized": 0.0}
    wins, losses = net[net > 0], net[net < 0]
    pf = float(wins.sum() / max(abs(losses.sum()), 1e-12)) if len(losses) else float("inf")
    eq = np.cumprod(1 + net * SIZING)
    return {"n": int(len(net)), "pf": round(pf, 3),
            "wr": round(float((net > 0).mean()), 3),
            "mean_net_bps": round(float(net.mean()) * 1e4, 1),
            "roi_sized": round(float(eq[-1] - 1), 4)}


def _sim_concurrent(te, sel, cost, horizon, hold_h, max_conc):
    ev = te.loc[sel].sort_values("event_time")
    hold = pd.Timedelta(hours=hold_h)
    equity, open_until, rets = 1.0, [], []
    for _, r in ev.iterrows():
        t = r["event_time"]
        open_until = [u for u in open_until if u > t]
        if len(open_until) >= max_conc or not np.isfinite(r[horizon]):
            continue
        net = float(r[horizon]) - cost
        equity *= (1 + net * SIZING)
        rets.append(net)
        open_until.append(t + hold)
    m = _metrics(np.array(rets)) if rets else _metrics(np.array([]))
    m["roi_portfolio_style"] = round(equity - 1, 4)
    m["n_taken"] = len(rets)
    return m


def _fit(tr, cost, features, horizon):
    import lightgbm as lgb
    tr = tr.sort_values("event_time").reset_index(drop=True)
    cut = int(len(tr) * 0.85)
    val_start = tr["event_time"].iloc[cut]
    fit = tr[tr["event_time"] < (val_start - EMBARGO)]
    val = tr[tr["event_time"] >= val_start]
    fit = fit[np.isfinite(fit[horizon].values)]
    val = val[np.isfinite(val[horizon].values)]
    y_fit = (fit[horizon].values > cost).astype(int)
    w_fit = np.abs(fit[horizon].values - cost)
    w_fit = np.clip(w_fit, None, np.nanpercentile(w_fit, 95))
    X_fit, X_val = fit[features].values, val[features].values
    models = []
    for k in range(N_BAG):
        m = lgb.LGBMClassifier(
            n_estimators=600, learning_rate=0.03, num_leaves=15, max_depth=5,
            min_child_samples=30, subsample=0.7 + 0.05 * (k % 3),
            colsample_bytree=0.6 + 0.05 * (k % 3), reg_lambda=5.0,
            random_state=k, verbose=-1)
        m.fit(X_fit, y_fit, sample_weight=w_fit,
              eval_set=[(X_val, (val[horizon].values > cost).astype(int))],
              callbacks=[lgb.early_stopping(50, verbose=False)])
        models.append(m)
    p_val = np.mean([m.predict_proba(X_val)[:, 1] for m in models], axis=0)
    thr = {f: float(np.quantile(p_val, 1 - f)) for f in (0.30, 0.20, 0.10, 0.05)}
    return (lambda X: np.mean([m.predict_proba(X)[:, 1] for m in models], axis=0),
            models, thr)


def run_fold(ev, year, cost, spec):
    horizon, features = spec["horizon"], spec["features"]
    lf = ev[np.isfinite(ev[horizon].values)]
    tr = lf[lf["event_time"].dt.year < year]
    te = lf[lf["event_time"].dt.year == year]
    if len(tr) < 150 or len(te) < 30:
        return {"year": year, "skip": True, "n_train": len(tr), "n_test": len(te)}
    out = {"year": year, "skip": False, "n_train": int(len(tr)), "n_test": int(len(te))}
    predict, models, thr = _fit(tr, cost, features, horizon)
    p = predict(te[features].values)
    sel = p >= thr[TOP_FRAC]
    out["ml_gated"] = _metrics(te.loc[sel, horizon].values - cost)
    out["portfolio_sim"] = _sim_concurrent(te, sel, cost, horizon,
                                           spec["hold_h"], spec["max_concurrent"])
    out["threshold_ladder"] = {
        f"top{int(f*100)}%": _metrics(te.loc[p >= t, horizon].values - cost)
        for f, t in thr.items()}
    try:
        from sklearn.metrics import roc_auc_score
        yte = (te[horizon].values > cost).astype(int)
        if 0 < yte.sum() < len(yte):
            out["ml_auc_test"] = round(float(roc_auc_score(yte, p)), 4)
    except Exception:
        pass
    imp = np.mean([m.booster_.feature_importance("gain") for m in models], axis=0)
    out["top_features"] = [(f, round(float(v), 0)) for f, v in
                           sorted(zip(features, imp), key=lambda x: -x[1])[:8]]
    # tape des trades sélectionnés (pour l'analyse de stack inter-moteurs)
    tape = te.loc[sel, ["event_time", "symbol", horizon]].copy()
    tape["net"] = tape[horizon] - cost
    tape["score"] = p[sel]
    out["_tape"] = tape
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=list(ENGINES), required=True)
    ap.add_argument("--rebuild-cache", action="store_true")
    args = ap.parse_args()
    spec = ENGINES[args.engine]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cache = CACHE_DIR / f"{args.engine}_dataset.parquet"
    if cache.exists() and not args.rebuild_cache:
        ev = pd.read_parquet(cache)
    else:
        symbols = load_universe(ROOT)
        ev = build_event_dataset(symbols, CascadeConfig(),
                                 detector_fn=_detector(args.engine))
        if ev.empty:
            print("0 event"); sys.exit(1)
        cache.parent.mkdir(parents=True, exist_ok=True)
        ev.to_parquet(cache, index=False)
    print(f"[{spec['name']}] events: {len(ev)}")
    print(ev.groupby(ev['event_time'].dt.year).size().to_string(), flush=True)

    net_all = ev[spec["horizon"]] - COST_RT
    net_all = net_all[np.isfinite(net_all)]
    print(f"edge brut ({spec['horizon']} net 14bps) : mean {net_all.mean()*1e4:+.1f} bps, "
          f"PF {net_all[net_all>0].sum()/max(abs(net_all[net_all<0].sum()),1e-9):.3f}", flush=True)

    tapes = []
    years = [2022, 2023, 2024, 2025, 2026]
    results = {"engine": spec["name"], "events_total": int(len(ev)),
               "config": {"cost_rt": COST_RT, "selection": f"top{int(TOP_FRAC*100)}%_val",
                          "horizon": spec["horizon"], "sizing": SIZING,
                          "max_concurrent": spec["max_concurrent"]},
               "folds": [], "folds_stress": []}
    print(f"\n── WF {spec['name']} (14 bps) ──")
    for y in years:
        r = run_fold(ev, y, COST_RT, spec)
        if isinstance(r.get("_tape"), pd.DataFrame):
            tapes.append(r.pop("_tape"))
        results["folds"].append(r)
        if r.get("skip"):
            print(f"  {y}: SKIP (train={r['n_train']}, test={r['n_test']})"); continue
        m, ps = r["ml_gated"], r["portfolio_sim"]
        print(f"  {y}: ML n={m['n']:4} PF={m['pf']:.2f} {m['mean_net_bps']:+.1f}bps "
              f"WR={m['wr']:.0%} AUC={r.get('ml_auc_test','-')} | "
              f"PORTF: n={ps['n_taken']} PF={ps['pf']:.2f} "
              f"ROI={ps['roi_portfolio_style']*100:+.1f}%", flush=True)
    print("\n── Stress ×2 (28 bps) ──")
    for y in years:
        r = run_fold(ev, y, COST_RT_STRESS, spec)
        r.pop("_tape", None)
        results["folds_stress"].append(r)
        if not r.get("skip"):
            m = r["ml_gated"]
            print(f"  {y}: n={m['n']:4} PF={m['pf']:.2f} {m['mean_net_bps']:+.1f}bps", flush=True)

    if tapes:
        tape = pd.concat(tapes, ignore_index=True)
        tape.to_parquet(OUT_DIR / f"{spec['name']}_trades.parquet", index=False)
        print(f"tape: {len(tape)} trades → {spec['name']}_trades.parquet", flush=True)

    valid = [r for r in results["folds"]
             if not r.get("skip") and r["n_train"] >= MIN_TRAIN_EVENTS]
    excluded = [r["year"] for r in results["folds"]
                if not r.get("skip") and r["n_train"] < MIN_TRAIN_EVENTS]
    pf_ok = [r for r in valid if r["ml_gated"]["pf"] >= 1.35 and r["ml_gated"]["n"] >= 20]
    destructive = [r for r in valid if r["ml_gated"]["pf"] < 1.0]
    verdict = ("CANDIDATE" if len(valid) >= 3 and len(pf_ok) >= 3 and not destructive
               else "NO_EDGE" if valid else "INSUFFICIENT_DATA")
    results["verdict"] = verdict
    results["verdict_rule"] = {"min_train_events": MIN_TRAIN_EVENTS,
                               "excluded_folds": excluded,
                               "pf_ok_folds": [r["year"] for r in pf_ok],
                               "destructive_folds": [r["year"] for r in destructive]}
    print(f"\nVERDICT {spec['name']} : {verdict}  "
          f"(valides {[r['year'] for r in valid]}, PF≥1.35 {len(pf_ok)}/{len(valid)}, "
          f"destructeurs {[r['year'] for r in destructive]}, exclus {excluded})")
    (OUT_DIR / f"{spec['name']}_WALKFORWARD.json").write_text(
        json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
