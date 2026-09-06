"""
tests/unit/test_manifest_gaps.py
─────────────────────────────────────────────────────────────────────────────
data_v2/validation/manifest_gaps.py::gap_confirmed_unfillable -- the
intra-period ("done") proof added 2026-08-11 for the LENDUSDT case: a
monthly-cadence source (perp_5m) fetched July 2020 successfully (the whole
month is in done_months, never missing_months) but the real archive's
first row that month is 2020-07-23, not the canonical_listing_ts-implied
2020-07-21. The missing-only check could never see this (the month was
never missing, just started partway through) and left it "actionable"
forever -- re-fetching an already-done month can only reproduce the
identical file. done_fn/the intra-period branch closes that gap generically
(no symbol-specific logic: applies to any archive with a similar
intra-period start, at either day or month granularity).

Gate:
    python3 -m pytest tests/unit/test_manifest_gaps.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.validation.manifest_gaps import gap_confirmed_unfillable, _funding_confirmed_empty_days

TS = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


# ── month granularity ──────────────────────────────────────────────────


def test_intra_month_gap_inside_a_done_month_is_confirmed_unfillable():
    """The exact LENDUSDT pattern: gap is 2020-07-21 -> 2020-07-23, both
    inside July 2020, which IS in done_months (successfully fetched) and
    NOT in missing_months (never 404'd) -- missing alone can't prove this,
    done can."""
    result = gap_confirmed_unfillable(
        TS("2020-07-21"), TS("2020-07-23"), missing=set(), granularity="month", done={"2020-07"},
    )
    assert result is True


def test_gap_spanning_two_months_is_not_covered_by_single_done_month():
    """The intra-period proof only applies when start and end fall in the
    SAME period -- a gap crossing a month boundary needs the missing-based
    proof (or is genuinely actionable), not the intra-period shortcut."""
    result = gap_confirmed_unfillable(
        TS("2020-06-25"), TS("2020-07-23"), missing=set(), granularity="month", done={"2020-07"},
    )
    assert result is False  # June isn't done or missing -- still actionable


def test_done_month_not_matching_gap_month_does_not_apply():
    result = gap_confirmed_unfillable(
        TS("2020-07-21"), TS("2020-07-23"), missing=set(), granularity="month", done={"2020-08"},
    )
    assert result is False


def test_no_done_set_falls_back_to_missing_only_behavior():
    """done=None (or omitted) must behave exactly like the pre-fix
    function -- no regression for callers that don't pass it."""
    assert gap_confirmed_unfillable(TS("2020-07-21"), TS("2020-07-23"), missing=set(), granularity="month") is False
    assert gap_confirmed_unfillable(
        TS("2020-07-21"), TS("2020-07-23"), missing={"2020-07"}, granularity="month",
    ) is True  # the pre-existing missing-based proof still works unaided


# ── day granularity ────────────────────────────────────────────────────


def test_intra_day_gap_inside_a_done_day_is_confirmed_unfillable():
    """Same principle for a day-granularity source (OI/aggTrades): a day
    that was fetched successfully (in done) but whose real data starts a
    few hours after the theoretical listing bound."""
    result = gap_confirmed_unfillable(
        TS("2021-03-15 07:00"), TS("2021-03-15 16:00"), missing=set(), granularity="day", done={"2021-03-15"},
    )
    assert result is True


def test_intra_day_gap_not_in_done_days_is_not_confirmed():
    result = gap_confirmed_unfillable(
        TS("2021-03-15 07:00"), TS("2021-03-15 16:00"), missing=set(), granularity="day", done={"2021-03-16"},
    )
    assert result is False


def test_day_gap_spanning_two_days_not_covered_by_single_done_day():
    result = gap_confirmed_unfillable(
        TS("2021-03-14 07:00"), TS("2021-03-15 16:00"), missing=set(), granularity="day", done={"2021-03-15"},
    )
    assert result is False


# ── existing missing-based proof still works (regression guard) ─────────


def test_missing_based_proof_still_works_for_multi_period_gaps():
    missing = {f"2020-09-{d:02d}" for d in range(1, 31)} | {f"2020-10-{d:02d}" for d in range(1, 32)}
    result = gap_confirmed_unfillable(
        TS("2020-09-01"), TS("2020-11-01"), missing=missing, granularity="day", done=set(),
    )
    assert result is True


# ── _funding_confirmed_empty_days: funding gained a manifest 2026-08-14
# (it never had one before, unlike OI/perp/spot/aggTrades) -- a single
# confirmed-empty-forward-fetch fact expanded into a day-string set for
# reuse by the same generic exclusion machinery ─────────────────────────


def test_funding_confirmed_empty_days_expands_the_full_span_inclusive(tmp_path):
    manifest = tmp_path / "AGIXUSDT_manifest.json"
    manifest.write_text('{"confirmed_empty_from": "2025-06-19T08:00:00", "confirmed_as_of": "2025-06-22T00:00:00"}')
    result = _funding_confirmed_empty_days(manifest)
    # confirmed_as_of's own day IS included (bug found 2026-08-14: excluding
    # it broke the trailing-gap check whenever readiness runs same-day as
    # the backfill, the normal case -- see the function's own docstring)
    assert result == {"2025-06-19", "2025-06-20", "2025-06-21", "2025-06-22"}


def test_funding_confirmed_empty_days_missing_file_returns_empty(tmp_path):
    assert _funding_confirmed_empty_days(tmp_path / "does_not_exist.json") == set()


def test_funding_confirmed_empty_days_malformed_manifest_returns_empty(tmp_path):
    manifest = tmp_path / "FOOUSDT_manifest.json"
    manifest.write_text("{}")  # no confirmed_empty_from/confirmed_as_of keys
    assert _funding_confirmed_empty_days(manifest) == set()


def test_funding_confirmed_empty_days_same_calendar_day_still_confirms_that_day(tmp_path):
    manifest = tmp_path / "FOOUSDT_manifest.json"
    manifest.write_text('{"confirmed_empty_from": "2025-06-19T08:00:00", "confirmed_as_of": "2025-06-19T09:00:00"}')
    assert _funding_confirmed_empty_days(manifest) == {"2025-06-19"}
