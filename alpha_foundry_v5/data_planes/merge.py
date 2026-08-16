from __future__ import annotations

import json
from pathlib import Path
from typing import List, Mapping, Sequence

import pandas as pd


def _parts(path: str) -> List[Path]:
    rows = sorted(Path(path).glob("part-*.parquet"))
    if not rows:
        raise ValueError("no part-*.parquet under %s" % path)
    return rows


def merge_planes(base_tape: str, plane_dirs: Sequence[str], out_dir: str) -> Mapping[str, object]:
    base_parts = _parts(base_tape)
    plane_parts = [_parts(path) for path in plane_dirs]
    for path, parts in zip(plane_dirs, plane_parts):
        if len(parts) != len(base_parts):
            raise ValueError("plane part count mismatch: %s" % path)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    columns = set()
    for i, base_path in enumerate(base_parts):
        base = pd.read_parquet(base_path).reset_index(drop=True)
        keys = base[["asof_ns", "symbol"]].reset_index(drop=True)
        merged = base
        for plane_dir, parts in zip(plane_dirs, plane_parts):
            plane = pd.read_parquet(parts[i]).reset_index(drop=True)
            if len(plane) != len(base):
                raise ValueError("plane row count mismatch for part %d: %s" % (i, plane_dir))
            plane_keys = plane[["asof_ns", "symbol"]].reset_index(drop=True)
            if not keys.equals(plane_keys):
                raise ValueError("plane key mismatch for part %d: %s" % (i, plane_dir))
            payload = plane.drop(columns=["asof_ns", "symbol"])
            collisions = sorted(set(payload.columns).intersection(merged.columns))
            if collisions:
                raise ValueError("column collision from %s: %s" % (plane_dir, collisions[:10]))
            merged = pd.concat([merged, payload], axis=1)
        out = root / ("part-%05d.parquet" % i)
        merged.to_parquet(out, index=False)
        total += len(merged)
        columns.update(merged.columns)
        print("[afv5-plane] merged %s rows_total=%d" % (out.name, total), flush=True)
    summary = {"rows": int(total), "parts": int(len(base_parts)), "columns": sorted(columns), "base_tape": str(base_tape), "planes": [str(x) for x in plane_dirs]}
    (root / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (root / "_SUCCESS").write_text("ok\n", encoding="utf-8")
    return summary
