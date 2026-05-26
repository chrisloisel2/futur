from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

import pandas as pd


ContextFrame = Tuple[str, pd.DataFrame, Optional[pd.Timedelta]]


def parse_timedelta(value: Optional[str]) -> Optional[pd.Timedelta]:
    if value is None or value == "":
        return None
    return pd.Timedelta(value)


def point_in_time_join(
    base: pd.DataFrame,
    contexts: Iterable[ContextFrame],
    *,
    timestamp_col: str = "timestamp",
    by: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """
    Backward-only asof join for contextual features.

    Each context tuple is `(prefix, frame, tolerance)`. Context columns are
    prefixed, and only values with `context.timestamp <= base.timestamp` can join.
    """

    if base.empty:
        return base.copy()

    had_datetime_index = isinstance(base.index, pd.DatetimeIndex) and timestamp_col not in base.columns
    left = _with_timestamp(base, timestamp_col).sort_values(timestamp_col).reset_index(drop=True)

    for prefix, context, tolerance in contexts:
        if context.empty:
            continue
        right = _with_timestamp(context, timestamp_col).sort_values(timestamp_col).reset_index(drop=True)
        join_by = [col for col in (by or []) if col in left.columns and col in right.columns]
        rename = {}
        for col in right.columns:
            if col == timestamp_col or col in join_by:
                continue
            rename[col] = "%s_%s" % (prefix, col) if not col.startswith(prefix + "_") else col
        right = right.rename(columns=rename)
        left = pd.merge_asof(
            left,
            right,
            on=timestamp_col,
            by=join_by if join_by else None,
            direction="backward",
            tolerance=tolerance,
        )

    if had_datetime_index:
        left = left.set_index(timestamp_col)
        left.index.name = base.index.name or timestamp_col
    return left


def _with_timestamp(frame: pd.DataFrame, timestamp_col: str) -> pd.DataFrame:
    out = frame.copy()
    if timestamp_col not in out.columns:
        if isinstance(out.index, pd.DatetimeIndex):
            out[timestamp_col] = out.index
        elif "datetime" in out.columns:
            out[timestamp_col] = out["datetime"]
        else:
            raise ValueError("Frame has no timestamp column")
    out[timestamp_col] = pd.to_datetime(out[timestamp_col], utc=True, errors="coerce")
    out = out.dropna(subset=[timestamp_col])
    return out
