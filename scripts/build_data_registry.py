#!/usr/bin/env python3
"""
scripts/build_data_registry.py
─────────────────────────────────────────────────────────────────────────────
Registre de données enriched (Phase 23.9) — hashé et validé.

Règle : aucun moteur ne lit un fichier absent du registre ou
validation_status != PASS.

Écrit artifacts/data_registry/enriched_store.yaml :
    {ASSET}_1h: {path, sha256, schema_sha256, rows, start, end,
                 validation_status, build_script}

Usage : python3 scripts/build_data_registry.py
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_parquet_store import validate_file, _sha256

ENRICHED = ROOT / "data" / "enriched"
OUT = ROOT / "artifacts" / "data_registry" / "enriched_store.yaml"
# actifs sans source raw → retirés de l'univers (documenté)
DROPPED = {"DOTUSDT": "no raw source in data_out/result (quarantined corrupt enriched only)"}


def _schema_sha(path: Path) -> str:
    import pyarrow.parquet as pq
    names = sorted(pq.ParquetFile(path).schema_arrow.names)
    return hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    registry = {}
    for p in sorted(ENRICHED.glob("*_1h_enriched.parquet")):
        asset = p.stem.replace("_1h_enriched", "")
        rep = validate_file(p)
        entry = {
            "path": str(p.relative_to(ROOT)),
            "sha256": rep.get("sha256"),
            "schema_sha256": _schema_sha(p) if rep["ok"] or rep.get("sha256") else None,
            "rows": rep["rows"],
            "validation_status": "PASS" if rep["ok"] else "FAIL",
            "issues": rep["issues"],
            "build_script": "scripts/rebuild_enriched_from_origin.py",
        }
        if rep["ok"]:
            df = pd.read_parquet(p, columns=["datetime"])
            ts = pd.to_datetime(df["datetime"], utc=True)
            entry["start"] = str(ts.min()); entry["end"] = str(ts.max())
        registry[f"{asset}_1h"] = entry

    for asset, reason in DROPPED.items():
        registry[f"{asset}_1h"] = {"validation_status": "DROPPED", "reason": reason}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(yaml.safe_dump(registry, sort_keys=True, allow_unicode=True))

    n_pass = sum(1 for v in registry.values() if v.get("validation_status") == "PASS")
    n_fail = sum(1 for v in registry.values() if v.get("validation_status") == "FAIL")
    n_drop = sum(1 for v in registry.values() if v.get("validation_status") == "DROPPED")
    print(f"\nDATA REGISTRY → {OUT.relative_to(ROOT)}")
    print(f"  PASS={n_pass}  FAIL={n_fail}  DROPPED={n_drop}")
    for k, v in sorted(registry.items()):
        st = v.get("validation_status")
        print(f"   [{st:<7}] {k:<16} rows={v.get('rows','-')}")
    if args.strict and n_fail:
        print(f"\nSTRICT FAIL : {n_fail} fichier(s) non valides dans le store")
        sys.exit(1)


if __name__ == "__main__":
    main()
