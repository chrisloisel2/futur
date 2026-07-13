"""
src/institutional/engines/event_production.py
─────────────────────────────────────────────────────────────────────────────
Modèles de PRODUCTION des moteurs événementiels : entraînement sur tout le
passé, persistance .pkl + registre sha256 (règle du repo depuis l'autopsie
TRM v5 : AUCUN chiffre sans artefact chargeable), et scoring pour le shadow.

Un artefact = {models (bagged), features, horizon, thresholds (quantiles des
scores val purgée), val_score_quantiles (mapping score→percentile pour rendre
les moteurs comparables), trained_until, engine}.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
ART_DIR = ROOT / "artifacts" / "event_engines"
REGISTRY = ART_DIR / "registry.json"

EMBARGO = pd.Timedelta(hours=8)
N_BAG = 5


def train_production_model(ev: pd.DataFrame, features: List[str], horizon: str,
                           cost: float, engine: str) -> Dict:
    """Même méthodo que le WF (val purgée chrono+embargo, weights, bagging),
    entraîné sur TOUT l'historique disponible — pour scorer le FUTUR (shadow)."""
    import lightgbm as lgb
    tr = ev[np.isfinite(ev[horizon].values)].sort_values("event_time")
    tr = tr.reset_index(drop=True)
    cut = int(len(tr) * 0.85)
    val_start = tr["event_time"].iloc[cut]
    fit = tr[tr["event_time"] < (val_start - EMBARGO)]
    val = tr[tr["event_time"] >= val_start]
    y_fit = (fit[horizon].values > cost).astype(int)
    w = np.abs(fit[horizon].values - cost)
    w = np.clip(w, None, np.nanpercentile(w, 95))
    models = []
    for k in range(N_BAG):
        m = lgb.LGBMClassifier(
            n_estimators=600, learning_rate=0.03, num_leaves=15, max_depth=5,
            min_child_samples=30, subsample=0.7 + 0.05 * (k % 3),
            colsample_bytree=0.6 + 0.05 * (k % 3), reg_lambda=5.0,
            random_state=k, verbose=-1)
        m.fit(fit[features].values, y_fit, sample_weight=w,
              eval_set=[(val[features].values,
                         (val[horizon].values > cost).astype(int))],
              callbacks=[lgb.early_stopping(50, verbose=False)])
        models.append(m)
    p_val = np.mean([m.predict_proba(val[features].values)[:, 1]
                     for m in models], axis=0)
    qgrid = np.linspace(0, 1, 101)
    return {
        "engine": engine, "features": features, "horizon": horizon, "cost": cost,
        "models": models,
        "thresholds": {f: float(np.quantile(p_val, 1 - f))
                       for f in (0.30, 0.20, 0.10, 0.05)},
        "val_score_quantiles": [float(np.quantile(p_val, q)) for q in qgrid],
        "trained_until": str(tr["event_time"].max()),
        "n_train": int(len(tr)),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }


def save_artifact(art: Dict) -> Path:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{art['engine']}_{art['trained_at'][:10]}.pkl"
    path = ART_DIR / name
    with open(path, "wb") as f:
        pickle.dump(art, f)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    reg = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    reg[art["engine"]] = {"path": str(path.relative_to(ROOT)), "sha256": sha,
                          "trained_until": art["trained_until"],
                          "n_train": art["n_train"],
                          "trained_at": art["trained_at"]}
    REGISTRY.write_text(json.dumps(reg, indent=2))
    return path


def load_artifact(engine: str, max_age_days: float = 10.0) -> Optional[Dict]:
    """Charge l'artefact du registre, vérifie sha256 + fraîcheur."""
    if not REGISTRY.exists():
        return None
    reg = json.loads(REGISTRY.read_text())
    if engine not in reg:
        return None
    entry = reg[engine]
    path = ROOT / entry["path"]
    if not path.exists():
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise RuntimeError(f"sha256 mismatch pour {path} — artefact corrompu")
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(entry["trained_at"])).days
    if age > max_age_days:
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def score(art: Dict, ev: pd.DataFrame) -> np.ndarray:
    X = ev[art["features"]].values
    return np.mean([m.predict_proba(X)[:, 1] for m in art["models"]], axis=0)


def score_percentile(art: Dict, scores: np.ndarray) -> np.ndarray:
    """Percentile [0,1] du score dans la distribution val du modèle —
    rend les scores des moteurs comparables (rang inter-moteurs des vagues)."""
    return np.searchsorted(np.array(art["val_score_quantiles"]), scores) / 100.0
