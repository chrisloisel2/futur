from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd


def audit_frame(
    df: pd.DataFrame,
    *,
    name: str,
    timestamp_col: str = "timestamp",
    key_cols: Iterable[str] = ("source", "symbol", "interval", "timestamp"),
    expected_freq: Optional[str] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "name": name,
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "duplicate_keys": 0,
        "nan_columns": {},
        "inf_columns": {},
        "gap_count": 0,
        "start": None,
        "end": None,
    }
    if df.empty:
        return result

    frame = df.copy()
    if timestamp_col not in frame.columns:
        if isinstance(frame.index, pd.DatetimeIndex):
            frame[timestamp_col] = frame.index
        elif "datetime" in frame.columns:
            frame[timestamp_col] = frame["datetime"]
    if timestamp_col in frame.columns:
        frame[timestamp_col] = pd.to_datetime(frame[timestamp_col], utc=True, errors="coerce")
        ts = frame[timestamp_col].dropna().sort_values()
        if not ts.empty:
            result["start"] = ts.iloc[0].isoformat()
            result["end"] = ts.iloc[-1].isoformat()
        if expected_freq and len(ts) > 2:
            expected_delta = pd.Timedelta(expected_freq)
            result["gap_count"] = int((ts.diff().dropna() > expected_delta * 1.5).sum())

    present_keys = [col for col in key_cols if col in frame.columns]
    if present_keys:
        result["duplicate_keys"] = int(frame.duplicated(subset=present_keys).sum())

    numeric_cols = frame.select_dtypes(include=[np.number]).columns
    nan_counts = frame[numeric_cols].isna().sum()
    inf_counts = np.isinf(frame[numeric_cols]).sum()
    result["nan_columns"] = {str(k): int(v) for k, v in nan_counts[nan_counts > 0].items()}
    result["inf_columns"] = {str(k): int(v) for k, v in inf_counts[inf_counts > 0].items()}
    return result


def audit_parquet_tree(root: Path, *, max_files: Optional[int] = None) -> Dict[str, Any]:
    files = sorted(root.glob("**/*.parquet")) if root.exists() else []
    if max_files is not None:
        files = files[:max_files]
    reports = []
    for path in files:
        try:
            reports.append(audit_frame(pd.read_parquet(path), name=str(path.relative_to(root))))
        except Exception as exc:
            reports.append({"name": str(path.relative_to(root)), "error": str(exc)})
    return {"root": str(root), "files": len(files), "reports": reports}


def write_audit_report(report: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, default=str)
