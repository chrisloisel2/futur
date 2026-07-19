#!/usr/bin/env python3
"""Entraîne PULLBACK_LONG (walk-forward, anti-leakage) sur l'univers PARALLEL_50 valide."""
import sys, time, json, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
import logging; logging.basicConfig(level=logging.ERROR)
from src.institutional.engines.pullback_long.infer import PullbackLongEngine
from src.institutional.universe.asset_quality_filter import assess_universe, AssetQualityStatus as Q

ROOT = Path(__file__).parents[1]
U = yaml.safe_load((ROOT/"configs/portfolio_v1_1_parallel_50.yaml").read_text())["universe"]
qual = assess_universe(U)
tradable = [s for s in U if qual[s].status != Q.BLOCK]   # PASS + WARN
print(f"Univers tradable: {len(tradable)}/{len(U)}", flush=True)

report = {}
for i, sym in enumerate(tradable, 1):
    mp = ROOT/"artifacts/institutional/engines/PULLBACK_LONG"/sym/"v1.0"/"model_2025.pkl"
    if mp.exists():
        report[sym] = "SKIP_TRAINED"; print(f"  [{i}/{len(tradable)}] {sym:<11} SKIP (déjà)", flush=True); continue
    t0 = time.time()
    try:
        eng = PullbackLongEngine(assets=[sym])
        eng.train(start="2021-01-01", end="2025-12-31")
        n = sum(1 for y in (2022,2023,2024,2025,2026)
                if (ROOT/"artifacts/institutional/engines/PULLBACK_LONG"/sym/"v1.0"/f"model_{y}.pkl").exists())
        report[sym] = f"OK ({n} folds)"
        print(f"  [{i}/{len(tradable)}] {sym:<11} OK {n}folds ({time.time()-t0:.0f}s)", flush=True)
    except Exception as e:
        report[sym] = f"ERR {repr(e)[:60]}"
        print(f"  [{i}/{len(tradable)}] {sym:<11} {report[sym]}", flush=True)
(ROOT/"reports/parallel_50").mkdir(parents=True, exist_ok=True)
(ROOT/"reports/parallel_50/pullback_train_status.json").write_text(json.dumps(report, indent=2))
ok = sum(1 for v in report.values() if v.startswith("OK") or v == "SKIP_TRAINED")
print(f"\nDONE: {ok}/{len(tradable)} modèles disponibles")
