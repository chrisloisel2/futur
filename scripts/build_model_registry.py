#!/usr/bin/env python3
"""
scripts/build_model_registry.py
─────────────────────────────────────────────────────────────────────────────
Registre modèle (Phase 15/16) — rend le système falsifiable.

RÈGLE : aucun chiffre de performance ne peut être cité si aucun artefact modèle
hashé et CHARGEABLE ne permet de le reproduire (executable=true).

Scanne les emplacements modèles, hashe (sha256), tente le chargement, écrit
    artifacts/model_registry/models.yaml

TRM v5 est inscrit explicitement comme MISSING_ARTIFACT / executable=false tant
qu'aucun .pkl/.joblib v5 n'existe (vérifié : aucun).

Usage : python3 scripts/build_model_registry.py [--strict]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATUS_REGISTRY = ROOT / "artifacts" / "institutional" / "engines" / "status_registry.json"
OUT = ROOT / "artifacts" / "model_registry" / "models.yaml"


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def _try_load(path: Path) -> tuple:
    """(executable, type_name). Essaie joblib puis pickle."""
    try:
        import joblib
        obj = joblib.load(path)
        return True, type(obj).__name__
    except Exception:
        pass
    try:
        import pickle
        with open(path, "rb") as f:
            obj = pickle.load(f)
        return True, type(obj).__name__
    except Exception as e:
        return False, f"UNLOADABLE:{type(e).__name__}"


def _entry(model_id: str, engine: str, version: str, path: Path, status: str) -> dict:
    executable, tname = _try_load(path)
    return {
        "engine": engine, "version": version,
        "artifact_path": str(path.relative_to(ROOT)),
        "artifact_sha256": _sha256(path),
        "artifact_type": tname,
        "executable": executable,
        "status": status if executable else "BROKEN_ARTIFACT",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    status_reg = json.loads(STATUS_REGISTRY.read_text()) if STATUS_REGISTRY.exists() else {}
    registry: dict = {}

    # 1. TRM v5 — explicitement non exécutable (aucun artefact)
    registry["TRM_TREND_LONG_v5"] = {
        "engine": "TRM_TREND_LONG", "version": "v5",
        "artifact_path": None, "artifact_sha256": None, "artifact_type": None,
        "executable": False, "status": "MISSING_ARTIFACT",
        "note": "+5.88%/mois = résultat HISTORIQUE non reproductible tant qu'aucun "
                "artefact v5 n'est retrouvé/régénéré. Ne pas citer comme alpha live.",
    }

    # 2. TRM v4 fleets persistés (alpha exécutable réel)
    for p in sorted((ROOT / "reports" / "paper_trading" / ".models").glob("*USDT_2025.pkl")):
        asset = p.stem.replace("_2025", "")
        registry[f"TRM_TREND_LONG_v4_{asset}"] = _entry(
            f"TRM_TREND_LONG_v4_{asset}", "TRM_TREND_LONG", "v4", p,
            status_reg.get("TRM_TREND_LONG", {}).get("status", "PAPER"))
    exit_p = ROOT / "reports" / "paper_trading" / ".models" / "exit_model_v1.pkl"
    if exit_p.exists():
        registry["EXIT_ENGINE_v1"] = _entry("EXIT_ENGINE_v1", "EXIT_ENGINE", "v1", exit_p, "SHADOW")

    # 3. TRM_TREND_INST folds
    inst_root = ROOT / "artifacts" / "institutional" / "backtests" / "btc_eth_trend"
    for p in sorted(inst_root.glob("*/v1.0/*/model_*.pkl")):
        asset = p.parts[-4]
        year = p.stem.replace("model_", "")
        registry[f"TRM_TREND_INST_{asset}_{year}"] = _entry(
            f"TRM_TREND_INST_{asset}_{year}", "TRM_TREND_INST", f"v1.0/{year}", p,
            status_reg.get("TRM_TREND_INST", {}).get("status", "SHADOW"))

    # 4. ML engines (pullback/liquidation/carry)
    eng_root = ROOT / "artifacts" / "institutional" / "engines"
    for p in sorted(eng_root.glob("*/*/v1.0/model_*.pkl")):
        engine = p.parts[-4]
        asset = p.parts[-3]
        year = p.stem.replace("model_", "")
        registry[f"{engine}_{asset}_{year}"] = _entry(
            f"{engine}_{asset}_{year}", engine, f"v1.0/{year}", p,
            status_reg.get(engine, {}).get("status", "SHADOW"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(yaml.safe_dump(registry, sort_keys=True, allow_unicode=True))

    n = len(registry)
    n_exec = sum(1 for v in registry.values() if v["executable"])
    n_broken = sum(1 for v in registry.values() if v["status"] == "BROKEN_ARTIFACT")
    print(f"\n{'='*60}\nMODEL REGISTRY — {OUT.relative_to(ROOT)}\n{'='*60}")
    print(f"  entrées : {n}  |  exécutables : {n_exec}  |  cassées : {n_broken}  |  missing v5 : 1")
    by_engine = {}
    for v in registry.values():
        by_engine.setdefault(v["engine"], {"n": 0, "exec": 0})
        by_engine[v["engine"]]["n"] += 1
        by_engine[v["engine"]]["exec"] += int(v["executable"])
    for eng, c in sorted(by_engine.items()):
        print(f"   {eng:<22} {c['exec']}/{c['n']} exécutables")
    print(f"\n  ⚠ TRM v5 : executable=false (MISSING_ARTIFACT)")

    if args.strict and n_broken:
        print(f"\nSTRICT FAIL : {n_broken} artefact(s) cassé(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
