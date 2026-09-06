#!/usr/bin/env python3
"""
scripts/build_basis_manifest.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, section 14: manifest + corpus hash for the just-rebuilt
basis store (data_v2/normalized/basis/venue=binance/, deleted and rebuilt
from zero by data_v2/features/basis.py per this session -- see its own
module docstring for the exact-join/strict-prior-z/shift(1)/full-warmup
contract). Read-only: hashes what's on disk, changes nothing.

    python3 scripts/build_basis_manifest.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASIS_DIR = ROOT / "data_v2/normalized/basis/venue=binance"
OUT_PATH = ROOT / "reports/BASIS_MANIFEST.json"


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def main() -> None:
    files = sorted(BASIS_DIR.glob("symbol=*/year=*/basis_5m.parquet"))
    per_symbol: dict[str, dict] = {}
    corpus_hasher = hashlib.sha256()
    total_rows = 0
    min_ts, max_ts = None, None

    for f in files:
        symbol = f.parent.parent.name.split("=", 1)[1]
        content = f.read_bytes()
        corpus_hasher.update(content)
        df = pd.read_parquet(f, columns=["timestamp"])
        n = len(df)
        total_rows += n
        if n:
            lo, hi = df["timestamp"].min(), df["timestamp"].max()
            min_ts = lo if min_ts is None else min(min_ts, lo)
            max_ts = hi if max_ts is None else max(max_ts, hi)
        entry = per_symbol.setdefault(symbol, {"rows": 0, "years": []})
        entry["rows"] += n
        entry["years"].append(f.parent.name.split("=", 1)[1])

    out = {
        "git_sha": _git_sha(),
        "timestamp": str(pd.Timestamp.now(tz="UTC")),
        "source": "data_v2/features/basis.py (rebuilt from zero this session -- exact perp/spot join, no future nearest, strict-prior basis_z, shift(1), full min_periods)",
        "symbols": len(per_symbol),
        "files": len(files),
        "total_rows": total_rows,
        "min_timestamp": str(min_ts) if min_ts is not None else None,
        "max_timestamp": str(max_ts) if max_ts is not None else None,
        "corpus_sha256": corpus_hasher.hexdigest(),
        "per_symbol_rows": {s: v["rows"] for s, v in sorted(per_symbol.items())},
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"basis manifest: {len(per_symbol)} symbols, {total_rows} rows, corpus_sha256={out['corpus_sha256'][:16]}...")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
