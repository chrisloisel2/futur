from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple

import pandas as pd

from .labs.registry import LabRegistry
from .support_audit import LAB_SUPPORT_POLICY


AUDIT_CLOCK_SUFFIXES = ("_available_ts_ns", "_receive_ts_ns")
ALWAYS_REQUIRED = ("asof_ns", "symbol", "price_fair_value")


def parquet_union_schema(root: str) -> Tuple[Tuple[Path, ...], Tuple[str, ...], Dict[str, Tuple[str, ...]]]:
    """Return every parquet part and the ordered union of their schemas.

    Sparse multimodal tensors may introduce a feature only in later chunks, so
    the first parquet schema is never treated as the logical dataset schema.
    """
    path = Path(root)
    parts = tuple(sorted(path.glob("part-*.parquet")))
    if not parts:
        raise ValueError("no part-*.parquet under %s" % path)
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - qbee has pyarrow
        raise RuntimeError("pyarrow is required for projected support-audit loading") from exc

    ordered = []
    seen = set()
    by_part = {}
    for part in parts:
        columns = tuple(str(x) for x in pq.ParquetFile(str(part)).schema_arrow.names)
        by_part[str(part)] = columns
        for column in columns:
            if column not in seen:
                seen.add(column)
                ordered.append(column)
    return parts, tuple(ordered), by_part


def _matched(columns: Sequence[str], patterns: Sequence[str]) -> Tuple[str, ...]:
    out = []
    for column in columns:
        name = str(column)
        if any(fnmatch.fnmatchcase(name, str(pattern)) for pattern in patterns):
            out.append(name)
    return tuple(out)


def support_projection_columns(
    all_columns: Sequence[str],
    labs: Sequence[str],
    registry: LabRegistry,
) -> Tuple[str, ...]:
    """Compute the complete target-free column surface needed by support audit.

    Includes:
      * structural keys and past-only regime price;
      * every PIT availability/receive clock so audit_point_in_time remains full;
      * all readiness requirements for selected labs;
      * all support-source and ESS-anchor patterns for selected labs.

    No target or PnL column is admitted by this projection.
    """
    columns = tuple(str(c) for c in all_columns)
    selected = set()

    for name in ALWAYS_REQUIRED:
        if name in columns:
            selected.add(name)

    for column in columns:
        if column.endswith(AUDIT_CLOCK_SUFFIXES):
            selected.add(column)

    for raw_lab in labs:
        lab_id = str(raw_lab).upper()
        if lab_id not in LAB_SUPPORT_POLICY:
            raise ValueError("unsupported support-audit lab: %s" % lab_id)
        spec = registry.spec(lab_id)
        patterns = []
        patterns.extend(spec.required_column_patterns)
        for group in spec.required_any_groups:
            patterns.extend(group)
        for pattern, _min_rows in spec.activity_requirements:
            patterns.append(pattern)

        policy = LAB_SUPPORT_POLICY[lab_id]
        for group in policy["groups"]:
            patterns.extend(group["patterns"])
        patterns.extend(policy["anchors"])

        for column in _matched(columns, patterns):
            if not column.startswith("target_"):
                selected.add(column)

    # Preserve logical dataset schema order. This makes anchor ordering and the
    # max_features cap deterministic relative to the full tensor.
    return tuple(column for column in columns if column in selected)


def load_projected_support_frame(
    root: str,
    labs: Sequence[str],
    registry: LabRegistry,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    path = Path(root)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
        return frame, {
            "mode": "csv_full",
            "parts": 1,
            "logical_columns": int(len(frame.columns)),
            "loaded_columns": int(len(frame.columns)),
        }
    if path.is_file():
        frame = pd.read_parquet(path)
        return frame, {
            "mode": "single_parquet_full",
            "parts": 1,
            "logical_columns": int(len(frame.columns)),
            "loaded_columns": int(len(frame.columns)),
        }

    parts, all_columns, by_part = parquet_union_schema(str(path))
    projection = support_projection_columns(all_columns, labs, registry)
    if "asof_ns" not in projection or "symbol" not in projection:
        raise ValueError("support projection lost required asof_ns/symbol keys")

    chunks = []
    for part in parts:
        available = set(by_part[str(part)])
        columns = [column for column in projection if column in available]
        chunks.append(pd.read_parquet(part, columns=columns))

    frame = pd.concat(chunks, ignore_index=True, sort=False)
    # Sparse parts legitimately omit later-emerging features. Reindex once on
    # the small projected surface so missing chunk columns become NaN.
    frame = frame.reindex(columns=list(projection))
    report = {
        "mode": "parquet_column_pruned",
        "parts": int(len(parts)),
        "logical_columns": int(len(all_columns)),
        "loaded_columns": int(len(projection)),
        "pruned_columns": int(len(all_columns) - len(projection)),
        "loaded_column_names": list(projection),
    }
    return frame, report
