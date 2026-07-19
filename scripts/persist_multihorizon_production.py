#!/usr/bin/env python3
"""
scripts/persist_multihorizon_production.py
─────────────────────────────────────────────────────────────────────────────
Persiste les modèles MULTI-HORIZON de PRODUCTION (entraînés sur tout le passé)
avec sha256 — règle du repo : aucun chiffre sans artefact chargeable.

Par moteur × horizon : fine-tune régularisation sur val purgée + bagging, sur
TOUT l'historique. Sauve un artefact unique {engine: {horizon: models, thr,...}}
+ seuils de consensus. Registre artifacts/event_engines/multihorizon_registry.json.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.train_multihorizon_all import ENGINES, COST, TOP, _fit_horizon, _predict

ART = ROOT / "artifacts" / "event_engines"
REG = ART / "multihorizon_registry.json"


def main():
    ART.mkdir(parents=True, exist_ok=True)
    registry = {}
    for name, cfg in ENGINES.items():
        if not cfg["path"].exists():
            continue
        ev = pd.read_parquet(cfg["path"])
        ev = ev[ev["label_full"]].copy()
        feats, horizons = cfg["feats"], cfg["horizons"]
        art = {"engine": name, "features": feats, "horizons": horizons,
               "trade_h": cfg["trade_h"], "exit_h": cfg["exit_h"], "cost": COST,
               "top_frac": TOP, "models": {}, "thresholds": {}, "hp": {},
               "trained_until": str(ev["event_time"].max()),
               "n_train": int(len(ev)),
               "trained_at": datetime.now(timezone.utc).isoformat()}
        for h in horizons:
            models, hp, auc = _fit_horizon(ev, feats, h)
            p = _predict(models, ev[feats].values)
            art["models"][h] = models
            art["thresholds"][h] = float(np.nanquantile(p, 1 - TOP))
            art["hp"][h] = {"num_leaves": hp["num_leaves"], "val_auc": auc}
            print(f"  {name}/{h}: num_leaves {hp['num_leaves']} val_auc {auc}", flush=True)
        path = ART / f"MH_{name}.pkl"
        with open(path, "wb") as f:
            pickle.dump(art, f)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        registry[name] = {"path": str(path.relative_to(ROOT)), "sha256": sha,
                          "horizons": horizons, "hp": {h: art["hp"][h] for h in horizons},
                          "trained_until": art["trained_until"], "n_train": art["n_train"],
                          "trained_at": art["trained_at"]}
        print(f"  → {path.name} sha256 {sha[:12]}", flush=True)
    REG.write_text(json.dumps(registry, indent=2))
    print(f"\nRegistre : {REG}")


if __name__ == "__main__":
    main()
