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

expected_start/expected_end are computed INDEPENDENTLY of the data's own
observed window (window_start/window_end) -- an earlier version derived
them FROM window_start/window_end (only ever trimming inward for listing/
delisting, never extending outward), which meant a dataset with 500 rows
from 2026-07-01 to 2026-07-22 but a real 3-year expected history would
self-report ~100% coverage: expected_rows was computed from the data's own
tiny window, not from listing_ts..now. Fixed: expected_start defaults to
max(listing_ts, source_available_from); expected_end defaults to
delisting_ts or `now`. A registry that only checked "acquisition succeeded,
file isn't corrupt" (scripts/validate_derivatives_store.py's old
gate = len(bad)==0 and len(parts)>0) could show status=PASS on exactly this
500-row-of-3-years case -- PASS meant "valid acquisition", not "sufficient
history to search for alpha in". This validator's coverage_pct/passed are
meant to mean the latter.

staleness_gate_days is now BLOCKING (fails the gate for a still-active,
non-delisted symbol whose last row is older than the gate), not just an
informational note.

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
from typing import Iterable, Optional

import numpy as np
import pandas as pd


@dataclass
class ValidationReport:
    symbol: str
    source: str
    window_start: Optional[pd.Timestamp]
    window_end: Optional[pd.Timestamp]
    expected_start: Optional[pd.Timestamp]
    expected_end: Optional[pd.Timestamp]
    expected_rows: int
    actual_rows: int
    coverage_pct: float
    gap_count: int
    max_gap: Optional[pd.Timedelta]
    duplicate_pk: int
    temporal_inversion: int
    schema_drift: list = field(default_factory=list)
    staleness: Optional[pd.Timedelta] = None
    staleness_gate_violated: bool = False
    corruption: int = 0
    listing_alignment: str = "unknown"  # ok | rows_before_listing | rows_after_delisting | both | no_instrument_master
    expected_span_known: bool = False
    strict_alpha_readiness: bool = False
    notes: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        if self.strict_alpha_readiness and not self.expected_span_known:
            # For a local/dev check, "we don't know the true expected span"
            # is acceptable (coverage_pct falls back to the data's own
            # observed window, still useful for gap detection). For
            # DATA_V2_READY it is NOT: a source whose expected coverage is
            # unknown must not be able to contribute to a READY verdict --
            # its coverage_pct could be silently meaningless (see the
            # fallback-branch note in validate_series).
            return False
        return (
            self.coverage_pct >= 0.98
            and self.duplicate_pk == 0
            and self.temporal_inversion == 0
            and self.corruption == 0
            and not self.schema_drift
            # "unknown" (no instrument_master passed in) is uninformative,
            # not a failure -- only a confirmed mismatch fails the gate.
            and self.listing_alignment in ("ok", "unknown")
            and not self.staleness_gate_violated
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "window_start": str(self.window_start) if self.window_start is not None else None,
            "window_end": str(self.window_end) if self.window_end is not None else None,
            "expected_start": str(self.expected_start) if self.expected_start is not None else None,
            "expected_end": str(self.expected_end) if self.expected_end is not None else None,
            "expected_span_known": self.expected_span_known,
            "strict_alpha_readiness": self.strict_alpha_readiness,
            "expected_rows": self.expected_rows,
            "actual_rows": self.actual_rows,
            "coverage_pct": round(self.coverage_pct, 4),
            "gap_count": self.gap_count,
            "max_gap_minutes": self.max_gap.total_seconds() / 60 if self.max_gap is not None else None,
            "duplicate_pk": self.duplicate_pk,
            "temporal_inversion": self.temporal_inversion,
            "schema_drift": self.schema_drift,
            "staleness_days": self.staleness.total_seconds() / 86400 if self.staleness is not None else None,
            "staleness_gate_violated": self.staleness_gate_violated,
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
    required_nonnegative_columns: Optional[Iterable[str]] = None,
    instrument_master: Optional[pd.DataFrame] = None,
    now: Optional[pd.Timestamp] = None,
    staleness_gate_days: float = 3.0,
    expected_start: Optional[pd.Timestamp] = None,
    expected_end: Optional[pd.Timestamp] = None,
    source_available_from: Optional[pd.Timestamp] = None,
    strict_alpha_readiness: bool = False,
) -> ValidationReport:
    """Validate one (symbol, source) time series against real coverage/
    integrity expectations, not just "file opens and isn't empty".

    expected_start: explicit override; if None, derived as
        max(listing_ts, source_available_from) from whichever of those two
        is available. Passing neither and no instrument_master row means
        expected span is UNKNOWN -- falls back to the data's own window
        (flagged via expected_span_known=False), not silently treated as
        100% coverage of something knowable.
    expected_end: explicit override; if None, derived as delisting_ts (if
        delisted) else `now` -- a still-active symbol is expected to have
        data up to today, not just up to whatever its last actual row is.
    source_available_from: the SOURCE's own historical floor (e.g. Binance
        Vision futures metrics start 2020-09-01 regardless of a symbol's
        own listing date) -- independent of listing_ts, the later of the
        two wins.
    strict_alpha_readiness: when True, `passed` additionally REQUIRES
        expected_span_known -- a source whose true expected coverage is
        unknown must not be able to contribute to a DATA_V2_READY verdict,
        even if its (meaningless, in that case) coverage_pct happens to
        look fine. False (the default) is appropriate for local/dev checks
        where "we don't know the true span but the data looks internally
        consistent" is still a useful signal.
    """
    pk_cols = list(pk_cols) if pk_cols else [timestamp_col]
    now = now or pd.Timestamp.utcnow()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")

    notes: list = []

    if df.empty:
        return ValidationReport(
            symbol=symbol, source=source, window_start=None, window_end=None,
            expected_start=None, expected_end=None,
            expected_rows=0, actual_rows=0, coverage_pct=0.0, gap_count=0, max_gap=None,
            duplicate_pk=0, temporal_inversion=0, schema_drift=["empty_dataframe"],
            staleness=None, corruption=0, listing_alignment="unknown",
            strict_alpha_readiness=strict_alpha_readiness,
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

    # corruption: NaN/inf/non-positive on required numeric columns. Two
    # variants -- a price/close CAN NEVER legitimately be exactly 0, but
    # open interest CAN (verified on real data: a newly-listed thin
    # contract can show a genuine sum_open_interest == 0.0 for a few 5m
    # bars right after listing -- flagging that as "corruption" via a
    # strict >0 check was a false positive; only a negative OI is actually
    # impossible).
    if required_positive_columns:
        for col in required_positive_columns:
            if col not in df.columns:
                schema_drift.append(f"missing_required_column:{col}")
                continue
            numeric = pd.to_numeric(df[col], errors="coerce")
            bad = numeric.isna() | ~np.isfinite(numeric) | (numeric <= 0)
            corruption += int(bad.sum())
    if required_nonnegative_columns:
        for col in required_nonnegative_columns:
            if col not in df.columns:
                schema_drift.append(f"missing_required_column:{col}")
                continue
            numeric = pd.to_numeric(df[col], errors="coerce")
            bad = numeric.isna() | ~np.isfinite(numeric) | (numeric < 0)
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

    # listing alignment (this one legitimately compares the DATA's own
    # bounds against listing/delisting -- "are there rows outside the
    # PIT-valid window", unrelated to the expected_rows fix above)
    listing_ts, delisting_ts = _listing_bounds(instrument_master, symbol)
    if listing_ts is None and delisting_ts is None and instrument_master is not None:
        listing_alignment = "no_instrument_master_row"
    elif instrument_master is None:
        listing_alignment = "unknown"
    else:
        # 24h grace, not 1h: real Vision OI data was observed starting ~9h
        # before a symbol's exchangeInfo onboardDate (AIAUSDT) -- listing/
        # delisting timestamps from different sources/feeds have some
        # natural slop at the boundary. Violations larger than a day (e.g.
        # ANTUSDT's OI data continuing ~4 weeks past its last daily kline,
        # or a ticker rename like RNDRUSDT->RENDERUSDT) stay flagged --
        # that is real, useful signal about a specific symbol's PIT
        # boundary needing a dedicated look, not noise to widen away.
        grace = pd.Timedelta(hours=24)
        before = listing_ts is not None and window_start is not None and window_start < listing_ts - grace
        after = delisting_ts is not None and window_end is not None and window_end > delisting_ts + grace
        if before and after:
            listing_alignment = "both"
        elif before:
            listing_alignment = "rows_before_listing"
        elif after:
            listing_alignment = "rows_after_delisting"
        else:
            listing_alignment = "ok"

    # expected_start/expected_end: INDEPENDENT of window_start/window_end.
    # This is the fix -- computing these from the data's own observed
    # window (as an earlier version did) makes any short recent slice
    # self-report ~100% coverage of itself.
    eff_start = expected_start
    if eff_start is None:
        candidates = [t for t in (listing_ts, source_available_from) if t is not None]
        eff_start = max(candidates) if candidates else None

    eff_end = expected_end
    if eff_end is None:
        eff_end = delisting_ts if delisting_ts is not None else now

    expected_span_known = eff_start is not None and eff_end is not None and eff_end > eff_start
    if not expected_span_known:
        # No independent expected_start/expected_end resolvable (no
        # listing_ts/source_available_from/explicit override) -- fall back
        # to the data's OWN observed span (window_start..window_end), NOT
        # its row count. Row count would make coverage_pct trivially 1.0
        # always (actual_rows/len(dedup_ts) == 1), hiding internal gaps.
        # The span-based fallback still catches gaps within the observed
        # window; it just can't catch missing HISTORY before window_start
        # (that needs listing_ts) -- flagged via expected_span_known=False.
        eff_start, eff_end = window_start, window_end
        notes.append(
            "expected_start/expected_end not independently resolvable (no "
            "listing_ts/source_available_from/explicit override) -- falling "
            "back to the data's own observed span for gap detection; "
            "coverage_pct here can NOT catch missing history before the "
            "first observed row, treat as partial information, not 100%"
        )

    if eff_start is not None and eff_end is not None and eff_end > eff_start:
        expected_rows = int((eff_end - eff_start) / expected_step) + 1
    else:
        expected_rows = len(dedup_ts)

    actual_rows = len(dedup_ts)
    coverage_pct = (actual_rows / expected_rows) if expected_rows else 0.0

    # staleness -- BLOCKING for a still-active (non-delisted) symbol,
    # measured against `now`, not just left as an informational note.
    staleness = (now - window_end) if window_end is not None else None
    staleness_gate_violated = False
    if delisting_ts is None and staleness is not None:
        if staleness.total_seconds() / 86400 > staleness_gate_days:
            staleness_gate_violated = True
            notes.append(
                f"BLOCKING staleness: last row {staleness} ago > gate "
                f"{staleness_gate_days}d and symbol not delisted"
            )

    return ValidationReport(
        symbol=symbol,
        source=source,
        window_start=window_start,
        window_end=window_end,
        expected_start=eff_start,
        expected_end=eff_end,
        expected_rows=expected_rows,
        actual_rows=actual_rows,
        coverage_pct=coverage_pct,
        gap_count=gap_count,
        max_gap=max_gap,
        duplicate_pk=duplicate_pk,
        temporal_inversion=temporal_inversion,
        schema_drift=schema_drift,
        staleness=staleness,
        staleness_gate_violated=staleness_gate_violated,
        corruption=corruption,
        listing_alignment=listing_alignment,
        expected_span_known=expected_span_known,
        strict_alpha_readiness=strict_alpha_readiness,
        notes=notes,
    )
