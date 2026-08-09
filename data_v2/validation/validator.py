"""
data_v2/validation/validator.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 11: real coverage/integrity gate, replacing the old check ("no
corrupt file + at least one partition exists"). A 5-minute-cadence source
that is active all day is expected to carry 288 timestamps/day; this
validator measures how far a given (symbol, source) parquet actually is
from that, plus the integrity checks the plan calls for: expected_rows,
actual_rows, coverage_pct, gap_count, max_gap, duplicate_pk,
temporal_inversion, schema_drift, staleness, corruption, listing_alignment.

Usage:
    from data_v2.validation.validator import validate_series
    report = validate_series(
        df, symbol="BTCUSDT", timestamp_col="create_time",
        bar_seconds=300, instrument_master=im_df,
        expected_columns={"create_time", "sum_open_interest"},
    )
    report.passed          # bool
    report.to_dict()       # flat dict, ready for a validation manifest row
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterable, Optional

import numpy as np
import pandas as pd


@dataclass
class ValidationReport:
    symbol: str
    source: str
    window_start: Optional[pd.Timestamp]
    window_end: Optional[pd.Timestamp]
    expected_rows: int
    actual_rows: int
    coverage_pct: float
    gap_count: int
    max_gap: Optional[pd.Timedelta]
    duplicate_pk: int
    temporal_inversion: int
    schema_drift: list = field(default_factory=list)
    staleness: Optional[pd.Timedelta] = None
    corruption: int = 0
    listing_alignment: str = "unknown"  # ok | rows_before_listing | rows_after_delisting | both | no_instrument_master
    notes: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.coverage_pct >= 0.98
            and self.duplicate_pk == 0
            and self.temporal_inversion == 0
            and self.corruption == 0
            and not self.schema_drift
            # "unknown" (no instrument_master passed in) is uninformative,
            # not a failure -- only a confirmed mismatch fails the gate.
            and self.listing_alignment in ("ok", "unknown")
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "window_start": str(self.window_start) if self.window_start is not None else None,
            "window_end": str(self.window_end) if self.window_end is not None else None,
            "expected_rows": self.expected_rows,
            "actual_rows": self.actual_rows,
            "coverage_pct": round(self.coverage_pct, 4),
            "gap_count": self.gap_count,
            "max_gap_minutes": self.max_gap.total_seconds() / 60 if self.max_gap is not None else None,
            "duplicate_pk": self.duplicate_pk,
            "temporal_inversion": self.temporal_inversion,
            "schema_drift": self.schema_drift,
            "staleness_days": self.staleness.total_seconds() / 86400 if self.staleness is not None else None,
            "corruption": self.corruption,
            "listing_alignment": self.listing_alignment,
            "passed": self.passed,
            "notes": self.notes,
        }


def _listing_bounds(instrument_master: Optional[pd.DataFrame], symbol: str):
    if instrument_master is None:
        return None, None
    row = instrument_master.loc[instrument_master["symbol"] == symbol]
    if row.empty:
        return None, None
    r = row.iloc[0]
    listing_ts = r.get("listing_ts")
    delisting_ts = r.get("delisting_ts")
    listing_ts = pd.Timestamp(listing_ts) if pd.notna(listing_ts) else None
    delisting_ts = pd.Timestamp(delisting_ts) if pd.notna(delisting_ts) else None
    return listing_ts, delisting_ts


def validate_series(
    df: pd.DataFrame,
    *,
    symbol: str,
    timestamp_col: str,
    bar_seconds: int = 300,
    source: str = "",
    pk_cols: Optional[Iterable[str]] = None,
    expected_columns: Optional[Iterable[str]] = None,
    required_positive_columns: Optional[Iterable[str]] = None,
    instrument_master: Optional[pd.DataFrame] = None,
    now: Optional[pd.Timestamp] = None,
    staleness_gate_days: float = 3.0,
) -> ValidationReport:
    """Validate one (symbol, source) time series against real coverage/
    integrity expectations, not just "file opens and isn't empty"."""
    pk_cols = list(pk_cols) if pk_cols else [timestamp_col]
    now = now or pd.Timestamp.utcnow()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")

    notes: list = []

    if df.empty:
        return ValidationReport(
            symbol=symbol, source=source, window_start=None, window_end=None,
            expected_rows=0, actual_rows=0, coverage_pct=0.0, gap_count=0, max_gap=None,
            duplicate_pk=0, temporal_inversion=0, schema_drift=["empty_dataframe"],
            staleness=None, corruption=0, listing_alignment="unknown",
            notes=["dataframe is empty"],
        )

    ts = pd.to_datetime(df[timestamp_col], utc=True, errors="coerce")
    corruption = int(ts.isna().sum())
    valid = ts.notna()

    # schema drift
    schema_drift = []
    if expected_columns is not None:
        missing = sorted(set(expected_columns) - set(df.columns))
        if missing:
            schema_drift.append(f"missing_columns:{missing}")

    # corruption: NaN/inf/non-positive on required numeric columns
    if required_positive_columns:
        for col in required_positive_columns:
            if col not in df.columns:
                schema_drift.append(f"missing_required_column:{col}")
                continue
            numeric = pd.to_numeric(df[col], errors="coerce")
            bad = numeric.isna() | ~np.isfinite(numeric) | (numeric <= 0)
            corruption += int(bad.sum())

    ts_sorted = ts[valid].sort_values()
    window_start, window_end = (ts_sorted.iloc[0], ts_sorted.iloc[-1]) if len(ts_sorted) else (None, None)

    # duplicate primary key
    pk_present = [c for c in pk_cols if c in df.columns]
    duplicate_pk = int(df.duplicated(subset=pk_present, keep=False).sum()) if pk_present else 0

    # temporal inversion: count of out-of-order timestamps (not counting exact duplicates)
    ts_raw = ts[valid].reset_index(drop=True)
    temporal_inversion = int((ts_raw.diff().dt.total_seconds() < 0).sum())

    # gaps, on the deduplicated+sorted series
    dedup_ts = ts_sorted.drop_duplicates()
    diffs = dedup_ts.diff().dropna()
    expected_step = pd.Timedelta(seconds=bar_seconds)
    gap_mask = diffs > expected_step * 1.5
    gap_count = int(gap_mask.sum())
    max_gap = diffs.max() if len(diffs) else None

    # listing alignment
    listing_ts, delisting_ts = _listing_bounds(instrument_master, symbol)
    if listing_ts is None and delisting_ts is None and instrument_master is not None:
        listing_alignment = "no_instrument_master_row"
    elif instrument_master is None:
        listing_alignment = "unknown"
    else:
        before = listing_ts is not None and window_start is not None and window_start < listing_ts - pd.Timedelta(hours=1)
        after = delisting_ts is not None and window_end is not None and window_end > delisting_ts + pd.Timedelta(hours=1)
        if before and after:
            listing_alignment = "both"
        elif before:
            listing_alignment = "rows_before_listing"
        elif after:
            listing_alignment = "rows_after_delisting"
        else:
            listing_alignment = "ok"

    # expected rows, bounded by listing/delisting when known
    eff_start = window_start
    eff_end = window_end
    if listing_ts is not None and (eff_start is None or listing_ts > eff_start):
        eff_start = max(listing_ts, eff_start) if eff_start is not None else listing_ts
    if delisting_ts is not None and (eff_end is None or delisting_ts < eff_end):
        eff_end = min(delisting_ts, eff_end) if eff_end is not None else delisting_ts
    if eff_start is not None and eff_end is not None and eff_end > eff_start:
        expected_rows = int((eff_end - eff_start) / expected_step) + 1
    else:
        expected_rows = len(dedup_ts)
        notes.append("expected_rows fallback to actual span: no reliable listing/delisting bound")

    actual_rows = len(dedup_ts)
    coverage_pct = (actual_rows / expected_rows) if expected_rows else 0.0

    staleness = (now - window_end) if window_end is not None else None
    if staleness is not None and staleness.total_seconds() / 86400 > staleness_gate_days and delisting_ts is None:
        notes.append(f"stale: last row {staleness} ago (gate {staleness_gate_days}d) and symbol not delisted")

    return ValidationReport(
        symbol=symbol,
        source=source,
        window_start=window_start,
        window_end=window_end,
        expected_rows=expected_rows,
        actual_rows=actual_rows,
        coverage_pct=coverage_pct,
        gap_count=gap_count,
        max_gap=max_gap,
        duplicate_pk=duplicate_pk,
        temporal_inversion=temporal_inversion,
        schema_drift=schema_drift,
        staleness=staleness,
        corruption=corruption,
        listing_alignment=listing_alignment,
        notes=notes,
    )
