#!/usr/bin/env python3
"""
scripts/build_data_v2_readiness.py
─────────────────────────────────────────────────────────────────────────────
Data V2 steps 25-ish: the one authoritative report. Runs the real coverage/
integrity validator (data_v2.validation.validator, strict_alpha_readiness
mode) plus causality and fake-flow checks against every (dataset, symbol)
in the PIT universe, and writes reports/DATA_V2_READINESS.json.

Can be run at any point during the four P0 backfills (OI/perp/spot/
aggTrades) -- it reports real partial coverage, it does not wait. It will
simply not (and must not) declare DATA_V2_READY=true until they're done;
that is the correct, honest behavior of an incomplete corpus, not a bug in
this script.

Datasets covered:
  oi_vision_5m        -- data/derivatives_backfill/binance_vision_metrics/
  perp_5m             -- data_v2/normalized/perp_ohlcv/
  spot_5m             -- data_v2/normalized/spot_ohlcv/
  agg_trades_flow_1m  -- data_v2/normalized/agg_trades_flow/1m/
  agg_trades_flow_5m  -- data_v2/normalized/agg_trades_flow/5m/

Causality checks (market_causality_violations / execution_causality_
violations): every dataset here is Binance Vision batch archive data with
a genuine, provable live equivalent (kline/aggTrade websockets, OI REST
poll) predating this dataset's history -- provably_live_observable=True
throughout. add_temporal_columns is applied on the fly (not read from
pre-materialized columns -- none of the four builders persist available_at
columns to disk yet, a follow-up task, not required for this gate) and the
two invariants that must NEVER be violated by construction are checked:
research_available_at >= event_time, execution_available_at >=
archive_published_at. A violation here means a real bug in the temporal
module, not (at this stage) a downstream feature-join leak -- that class of
leak is only observable once the Event Scanner actually joins features to
labels, and must be re-checked there with assert_causal.

Usage:
    /home/qbee/futur/.venv/bin/python3 scripts/build_data_v2_readiness.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_v2.validation.validator import validate_series  # noqa: E402
from data_v2.temporal.available_at import add_temporal_columns  # noqa: E402
from data_pipeline.taker_flow_guard import looks_like_placeholder_taker_flow  # noqa: E402
from data_v2.normalized.perp_ohlcv.build_perp_5m import month_range  # noqa: E402
from data_v2.validation.manifest_gaps import (  # noqa: E402
    DATASET_MANIFEST_SPECS,
    VISION_OI_FLOOR,
    gap_confirmed_unfillable,
    load_funding as _load_funding,
    load_oi as _load_oi,
    load_year_partitioned as _load_year_partitioned,
)

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_PATH = ROOT / "reports/DATA_V2_READINESS.json"

MONTHLY_PUBLICATION_LAG_DAYS = 5.0  # matches available_at.BATCH_PUBLICATION_LAG["binance_vision_monthly"]


def _monthly_publication_watermark(now: pd.Timestamp, lag_days: float = MONTHLY_PUBLICATION_LAG_DAYS) -> pd.Timestamp:
    """The last instant a month-cadence Vision archive (perp_5m/spot_5m)
    can genuinely be expected to exist, given `now`. A month must be FULLY
    CLOSED (we are not still inside it) AND past its own publication lag
    before Binance has actually published its archive -- a still-open or
    just-closed-but-not-yet-lagged month is PENDING_PUBLICATION, not a
    real gap, and demanding coverage through "now" for it silently caps
    perp/spot's pass rate on data the source cannot yet provide (bug found
    2026-08-14: build_data_v2_readiness.py never passed an explicit
    expected_end for these two datasets, so validate_series' own
    fallback -- delisting_ts if known, else `now` -- meant every
    non-delisted symbol's coverage denominator implicitly extended to
    today, even though the current month's archive structurally cannot
    exist yet; confirmed empirically via a real perp/spot top-up run that
    returned new_months=0 for all 312 symbols)."""
    month_start = pd.Timestamp(year=now.year, month=now.month, day=1, tz="UTC")
    while True:
        prev_month_end = month_start - pd.Timedelta(minutes=5)  # last 5m bar of the month before month_start
        if prev_month_end + pd.Timedelta(days=lag_days) <= now:
            return prev_month_end
        month_start = (month_start - pd.Timedelta(days=1)).replace(day=1)


def _spot_absence_confirmed(symbol: str, expected_start: Optional[pd.Timestamp], expected_end: Optional[pd.Timestamp]) -> bool:
    """True only if the spot builder actually tried every expected month for
    this symbol and every one came back 404 -- i.e. genuine proof no spot
    market exists, not merely "we haven't backfilled it yet" (which must
    NOT be treated as NOT_APPLICABLE, or a real gap would silently vanish
    from the coverage denominator)."""
    manifest_path = ROOT / f"data_v2/normalized/spot_ohlcv/venue=binance/symbol={symbol}/manifest.json"
    if not manifest_path.exists():
        return False  # never attempted
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("done_months"):
        return False  # some spot data WAS found -- not absent
    if expected_start is None or expected_end is None:
        return False
    expected_months = {f"{y:04d}-{m:02d}" for y, m in month_range(expected_start.date(), expected_end.date())}
    missing = set(manifest.get("missing_months", []))
    return expected_months.issubset(missing)


def _agg_trades_manifest_counts(symbol: str) -> tuple[int, int]:
    """(failed_days, missing_days) from the 1m manifest -- the 1m and 5m
    builders share the same day-level manifest (data_v2/normalized/
    agg_trades/build_agg_trades_flow.py writes only under OUT_1M's
    manifest.json)."""
    path = ROOT / f"data_v2/normalized/agg_trades_flow/1m/venue=binance/symbol={symbol}/manifest.json"
    if not path.exists():
        return 0, 0
    manifest = json.loads(path.read_text())
    return len(manifest.get("failed_days", [])), len(manifest.get("missing_days", []))


DATASET_SPECS = {
    "oi_vision_5m": dict(
        loader=_load_oi, timestamp_col="create_time", bar_seconds=300,
        required_positive_columns=None,
        required_nonnegative_columns=["sum_open_interest"],  # 0 is a real, observed value near listing; negative is not
        source_available_from=VISION_OI_FLOOR, source_kind="binance_vision_daily",
        check_taker_flow=False,
        # a delisted symbol's OWN last real OI observation, not the
        # cross-source composite delisting_ts (see _expected_end_baseline).
        delisted_end_field="last_oi_ts",
    ),
    "perp_5m": dict(
        loader=lambda sym: _load_year_partitioned(ROOT / "data_v2/normalized/perp_ohlcv/venue=binance", sym, "perp_5m.parquet"),
        timestamp_col="timestamp", bar_seconds=300,
        required_positive_columns=["close"],
        source_available_from=None, source_kind="binance_vision_monthly",
        check_taker_flow=True,
        # Monthly Vision archives cannot possibly contain the current,
        # still-open month -- staleness for this source has an inherent
        # sawtooth of ~0 to ~35-40 days (days elapsed in the current month
        # + Vision's own publication lag for the month just closed), never
        # the ~3 days appropriate for a daily-cadence source. The default
        # 3.0 gate was flagging essentially every non-delisted symbol as
        # BLOCKING-stale even immediately after a fully successful backfill
        # run (confirmed empirically: median staleness 10.0 days across 312
        # symbols right after both perp_5m and spot_5m builders reported
        # "nothing left to fetch"). 40 days covers the worst point in that
        # cycle; still catches a symbol whose backfill genuinely stalled
        # for 2+ months.
        staleness_gate_days=40.0,
        # same granularity-mismatch reasoning: fetching "the month
        # containing the true listing timestamp" always pulls in some real
        # days from earlier in that month -- 31 days covers the worst case
        # (listed on the last day of a month).
        listing_alignment_grace_days=31.0,
        # the coverage denominator must not demand data through "now" for
        # a source that structurally cannot have the current, still-open
        # month yet -- see _monthly_publication_watermark's docstring.
        publication_watermark_fn=_monthly_publication_watermark,
        # a delisted symbol's OWN last real perp kline, not the
        # cross-source composite delisting_ts (see _expected_end_baseline).
        delisted_end_field="last_perp_kline_ts",
    ),
    "spot_5m": dict(
        loader=lambda sym: _load_year_partitioned(ROOT / "data_v2/normalized/spot_ohlcv/venue=binance", sym, "spot_5m.parquet"),
        timestamp_col="timestamp", bar_seconds=300,
        required_positive_columns=["spot_close"],
        source_available_from=None, source_kind="binance_vision_monthly",
        check_taker_flow=True,
        staleness_gate_days=40.0,  # same monthly-cadence reasoning as perp_5m above
        listing_alignment_grace_days=31.0,  # same monthly-cadence reasoning as perp_5m above
        publication_watermark_fn=_monthly_publication_watermark,  # same reasoning as perp_5m above
        # spot's expected coverage is bound to first_perp_kline_ts, not the
        # composite instrument_master listing_ts (which can be earlier --
        # see build_spot_5m.py's module docstring, 2026-08-11 fix). NaN ->
        # fail closed (expected_span left unknown, never a fabricated
        # fallback date).
        listing_ts_field="first_perp_kline_ts",
        # same symmetry on the end side: spot's own last real observation
        # isn't separately tracked in instrument_master, so it is bound to
        # perp's own last kline too (see _expected_end_baseline) -- spot
        # exists to build the perp/spot basis, so it should never need
        # coverage beyond what perp itself proves.
        delisted_end_field="last_perp_kline_ts",
        # NOT_APPLICABLE requires PROOF (every expected month tried, all
        # 404 -- see _spot_absence_confirmed), not merely "no file on disk".
        # An earlier version treated absence itself as NOT_APPLICABLE,
        # which would let a spot market that genuinely exists but simply
        # hasn't been backfilled yet (or failed to backfill) silently drop
        # out of the coverage denominator and inflate spot_coverage_gt_98pct.
        confirm_absence_fn=_spot_absence_confirmed,
    ),
    "funding": dict(
        loader=_load_funding, timestamp_col="timestamp", bar_seconds=8 * 3600,
        required_positive_columns=None,
        required_nonnegative_columns=None,  # funding_rate is legitimately signed; mark_price NaN pre-2023-10-31 is a known, accepted gap, not corruption
        source_available_from=None, source_kind="binance_vision_daily",
        check_taker_flow=False,
        # DATA_READY per prior audit (329 files, 0/311 PIT symbols missing)
        # -- included here so "funding_coverage_100pct" in hard_gates is an
        # actually-measured fact, not the hardcoded True an earlier version
        # of this script had.
        # variable_cadence (2026-08-11): 8h is Binance's documented STANDARD
        # interval, but some contracts use a shorter DYNAMIC interval --
        # verified on real data, AIAUSDT mixes 1h and 4h settlements. A
        # fixed "3/day" row-count expectation made denser-cadence symbols
        # report an impossible >100% coverage_pct (the ~147% anomaly) and
        # could never actually catch a missed settlement. bar_seconds is
        # now the MAXIMUM allowed interval for gap detection, not a literal
        # expected spacing -- see data_v2.validation.validator's
        # variable_cadence docstring.
        variable_cadence=True,
        # a delisted symbol's OWN last real settlement, not the
        # cross-source composite delisting_ts (see _expected_end_baseline).
        delisted_end_field="last_funding_ts",
    ),
    "agg_trades_flow_1m": dict(
        loader=lambda sym: _load_year_partitioned(ROOT / "data_v2/normalized/agg_trades_flow/1m/venue=binance", sym, "flow.parquet"),
        timestamp_col="timestamp", bar_seconds=60,
        required_positive_columns=[],
        source_available_from=None, source_kind="binance_vision_daily",
        check_taker_flow=False,
    ),
    "agg_trades_flow_5m": dict(
        loader=lambda sym: _load_year_partitioned(ROOT / "data_v2/normalized/agg_trades_flow/5m/venue=binance", sym, "flow.parquet"),
        timestamp_col="timestamp", bar_seconds=300,
        required_positive_columns=[],
        source_available_from=None, source_kind="binance_vision_daily",
        check_taker_flow=False,
    ),
}


def _symbol_listing_field(im: pd.DataFrame, symbol: str, field: str) -> Optional[pd.Timestamp]:
    """Read instrument_master's `field` for `symbol` -- None if the symbol
    row is missing or the field itself is NaN (never a fabricated
    fallback; callers must fail closed on None, not substitute a different
    field silently)."""
    im_row = im.loc[im["symbol"] == symbol]
    if im_row.empty or pd.isna(im_row.iloc[0][field]):
        return None
    return pd.Timestamp(im_row.iloc[0][field])


def _expected_end_baseline(dataset: str, symbol: str, im: pd.DataFrame, now: pd.Timestamp) -> Optional[pd.Timestamp]:
    """None means "let validate_series apply its own delisting_ts-else-now
    fallback, unchanged". Two overrides, both dataset-specific and both
    symmetric with _expected_start_baseline's own listing_ts_field
    precedent:

    1. A confirmed-delisted symbol uses THIS dataset's own last proven
       observation (delisted_end_field) instead of the cross-source
       composite delisting_ts, which is exactly `max(last_perp_kline_ts,
       last_funding_ts, last_oi_ts)` (see build_instrument_master.py's
       reconcile_end) -- misleadingly LATE for a dataset whose own real
       data stopped earlier than some OTHER source's. Bug found
       2026-08-14 (external review): EOSUSDT's perp klines genuinely
       stopped 2025-05-21, but composite delisting_ts is 2026-07-22
       (driven by funding continuing to report long after) -- an
       artificial ~14-month "gap" in perp_5m's coverage that no amount of
       re-backfilling could ever close, the same class of bug as the
       already-fixed spot listing_ts_field issue but on the end side.
       Confirmed the same pattern independently affects oi_vision_5m and
       funding too (checked via each delisted symbol's own COVERAGE
       failures); agg_trades_flow has no equivalent last_*_ts field in
       instrument_master, so it has no override here -- a known,
       documented gap for that one dataset, not silently extended without
       real supporting data.
    2. A still-active (non-delisted) symbol uses the dataset's own
       publication_watermark_fn if it has one (perp_5m/spot_5m), else
       defers to validate_series' now-fallback, unchanged.
    """
    if _symbol_listing_field(im, symbol, "delisting_ts") is not None:
        delisted_end_field = DATASET_SPECS[dataset].get("delisted_end_field")
        if delisted_end_field is not None:
            own_end = _symbol_listing_field(im, symbol, delisted_end_field)
            if own_end is not None:
                return own_end
        return None
    watermark_fn = DATASET_SPECS[dataset].get("publication_watermark_fn")
    return watermark_fn(now) if watermark_fn is not None else None


def _expected_start_baseline(dataset: str, symbol: str, im: pd.DataFrame) -> Optional[pd.Timestamp]:
    """The dataset's own notion of "when this symbol's history should
    start" -- instrument_master's generic composite listing_ts for most
    datasets, but first_perp_kline_ts specifically for spot_5m (see
    DATASET_SPECS["spot_5m"]["listing_ts_field"] and build_spot_5m.py's
    module docstring: funding/OI frequently observe a symbol slightly
    BEFORE its first perp kline, so the composite listing_ts silently
    pulled spot's expected coverage window back earlier than perp/basis
    can ever use). None (field NaN) means fail closed -- no fallback to a
    different field, no invented date."""
    field = DATASET_SPECS[dataset].get("listing_ts_field", "listing_ts")
    return _symbol_listing_field(im, symbol, field)


def _confirmed_unavailable_expected_start(
    dataset: str, symbol: str, im: pd.DataFrame, df: pd.DataFrame, timestamp_col: str,
    baseline_expected_start: Optional[pd.Timestamp],
) -> Optional[pd.Timestamp]:
    """If the gap between this symbol's theoretical expected_start
    (baseline_expected_start, capped by the dataset's own
    source_available_from) and the data's own real first row is CONFIRMED
    unfillable -- the backfiller's own manifest already 404'd every single
    period in that gap, see data_v2.validation.manifest_gaps -- return the
    data's real first row as an expected_start override, so that confirmed-
    unavailable prefix stops permanently counting against coverage_pct
    (the exact bug: ADAUSDT/ZRXUSDT's OI could never reach the 95%
    coverage gate no matter how complete the backfill was, because their
    first 456 days are genuinely 404 at the source, not unfetched).
    None means "no adjustment" -- caller falls back to baseline_expected_start
    (funding has no confirmed-unavailable tracking here; DATA_READY per the
    prior audit already established it has none)."""
    manifest_key = "agg_trades_flow_5m" if dataset == "agg_trades_flow_1m" else dataset
    manifest_spec = DATASET_MANIFEST_SPECS.get(manifest_key)
    if manifest_spec is None or baseline_expected_start is None:
        return None

    source_floor = manifest_spec["source_available_from"]
    repair_target = max(baseline_expected_start, source_floor) if source_floor is not None else baseline_expected_start

    window_start = pd.to_datetime(df[timestamp_col], utc=True).min()
    if pd.isna(window_start) or window_start <= repair_target:
        return None  # no gap at all, nothing to adjust

    missing = manifest_spec["missing_fn"](symbol)
    done = manifest_spec.get("done_fn", lambda s: set())(symbol)
    if gap_confirmed_unfillable(repair_target, window_start, missing, manifest_spec["granularity"], done=done):
        return window_start
    return None


FAIL_REASON_CODES = (
    "NO_DATA", "COVERAGE", "STALE", "PIT", "CORRUPTION", "DUPLICATES",
    "FAILED_MANIFEST", "CAUSALITY_VIOLATION", "FAKE_FLOW",
)


def _classify_fail_reasons(row: dict, report=None) -> list:
    """Every reason this (dataset, symbol) row is FAIL, so the report is
    immediately diagnosable per-cause rather than a single opaque
    boolean. Multiple reasons can co-occur (e.g. STALE and COVERAGE at
    once) -- all applicable ones are listed, not just the first found."""
    if row["verdict"] != "FAIL":
        return []
    reasons = []
    if row["actual_rows"] == 0:
        reasons.append("NO_DATA")
        return reasons  # nothing else is measurable with zero rows
    if report is not None and not report.expected_span_known:
        reasons.append("COVERAGE")  # can't even confirm sufficient coverage
    elif row["coverage_pct"] < 0.98:
        reasons.append("COVERAGE")
    if report is not None and report.staleness_gate_violated:
        reasons.append("STALE")
    if row["pit_violations"] > 0:
        reasons.append("PIT")
    if row["corruption"] > 0:
        reasons.append("CORRUPTION")
    if row["duplicates"] > 0:
        reasons.append("DUPLICATES")
    if row["failed_days"] > 0:
        reasons.append("FAILED_MANIFEST")
    if row["market_causality_violations"] > 0 or row["execution_causality_violations"] > 0:
        reasons.append("CAUSALITY_VIOLATION")
    if row["fake_flow_detected"]:
        reasons.append("FAKE_FLOW")
    return reasons or ["COVERAGE"]  # FAIL must always have >=1 reason; COVERAGE is the honest catch-all


def evaluate_dataset_symbol(dataset: str, symbol: str, im: pd.DataFrame, now: pd.Timestamp) -> dict:
    spec = DATASET_SPECS[dataset]
    df = spec["loader"](symbol)

    row = {
        "dataset": dataset, "symbol": symbol,
        "expected_start": None, "expected_end": None, "expected_span_known": False,
        "actual_start": None, "actual_end": None,
        "expected_rows": 0, "actual_rows": 0, "coverage_pct": 0.0,
        "gaps": 0, "max_gap_minutes": None,
        "duplicates": 0, "corruption": 0, "staleness_days": None,
        "failed_days": 0, "missing_days": 0,
        "pit_violations": 0,
        "market_causality_violations": 0, "execution_causality_violations": 0,
        "fake_flow_detected": False,
        "confirmed_unavailable_prefix_excluded": False,
        "verdict": "FAIL",
        "fail_reasons": [],
    }

    if dataset.startswith("agg_trades_flow"):
        failed, missing = _agg_trades_manifest_counts(symbol)
        row["failed_days"], row["missing_days"] = failed, missing

    if df is None or df.empty:
        row["notes"] = "no data on disk yet"
        confirm_absence_fn = spec.get("confirm_absence_fn")
        if confirm_absence_fn is not None:
            listing_ts = _expected_start_baseline(dataset, symbol, im)
            delisting_ts = _symbol_listing_field(im, symbol, "delisting_ts")
            candidates = [t for t in (listing_ts, spec.get("source_available_from")) if t is not None]
            exp_start = max(candidates) if candidates else None
            exp_end = delisting_ts if delisting_ts is not None else now
            if confirm_absence_fn(symbol, exp_start, exp_end):
                row["verdict"] = "NOT_APPLICABLE"
                row["notes"] = "confirmed absent: every expected month attempted, all 404 -- no market exists"
        if row["verdict"] == "FAIL":
            row["fail_reasons"] = ["NO_DATA"]
        return row

    baseline_expected_start = _expected_start_baseline(dataset, symbol, im)
    confirmed_unavailable_start = _confirmed_unavailable_expected_start(
        dataset, symbol, im, df, spec["timestamp_col"], baseline_expected_start
    )
    # Fail closed: if the dataset's own listing_ts_field (first_perp_kline_ts
    # for spot_5m, listing_ts otherwise) is NaN for this symbol, force an
    # explicit "unknown" expected_start (pd.NaT) rather than silently
    # falling back to validate_series' own generic-listing_ts default --
    # that fallback would defeat the whole point of binding spot to a
    # different, more correct field.
    if confirmed_unavailable_start is not None:
        effective_expected_start = confirmed_unavailable_start
        row["confirmed_unavailable_prefix_excluded"] = True
    elif baseline_expected_start is not None:
        effective_expected_start = baseline_expected_start
    else:
        effective_expected_start = pd.NaT
        row["notes"] = f"no {spec.get('listing_ts_field', 'listing_ts')} proof -- expected coverage left unknown, not fabricated"

    effective_expected_end = _expected_end_baseline(dataset, symbol, im, now)

    report = validate_series(
        df, symbol=symbol, timestamp_col=spec["timestamp_col"], bar_seconds=spec["bar_seconds"],
        source=dataset, instrument_master=im, now=now,
        required_positive_columns=spec["required_positive_columns"] or None,
        required_nonnegative_columns=spec.get("required_nonnegative_columns") or None,
        source_available_from=spec["source_available_from"],
        expected_start=effective_expected_start,
        expected_end=effective_expected_end,
        strict_alpha_readiness=True,
        variable_cadence=spec.get("variable_cadence", False),
        staleness_gate_days=spec.get("staleness_gate_days", 3.0),
        listing_alignment_grace_days=spec.get("listing_alignment_grace_days", 1.0),
    )

    # causality sanity check -- these two invariants must hold by
    # construction; a violation is a real bug, not (yet) a feature-join leak
    temporal = add_temporal_columns(
        df.rename(columns={spec["timestamp_col"]: "__event_time__"}),
        event_time_col="__event_time__", source_kind=spec["source_kind"],
        bar_seconds=spec["bar_seconds"], provably_live_observable=True,
    )
    market_violations = int((temporal["research_available_at"] < temporal["event_time"]).sum())
    execution_violations = int((temporal["execution_available_at"] < temporal["archive_published_at"]).sum())

    fake_flow = False
    if spec["check_taker_flow"]:
        fake_flow = bool(looks_like_placeholder_taker_flow(df))

    pit_violation = 0 if report.listing_alignment in ("ok", "unknown") else 1

    verdict = "PASS" if (
        report.passed and market_violations == 0 and execution_violations == 0
        and not fake_flow and pit_violation == 0 and row["failed_days"] == 0
    ) else "FAIL"

    row.update({
        "expected_start": str(report.expected_start) if report.expected_start is not None else None,
        "expected_end": str(report.expected_end) if report.expected_end is not None else None,
        "expected_span_known": report.expected_span_known,
        "actual_start": str(report.window_start) if report.window_start is not None else None,
        "actual_end": str(report.window_end) if report.window_end is not None else None,
        "expected_rows": report.expected_rows, "actual_rows": report.actual_rows,
        "coverage_pct": round(report.coverage_pct, 4),
        "gaps": report.gap_count,
        "max_gap_minutes": report.max_gap.total_seconds() / 60 if report.max_gap is not None else None,
        "duplicates": report.duplicate_pk, "corruption": report.corruption,
        "staleness_days": report.staleness.total_seconds() / 86400 if report.staleness is not None else None,
        "pit_violations": pit_violation,
        "market_causality_violations": market_violations,
        "execution_causality_violations": execution_violations,
        "fake_flow_detected": fake_flow,
        "verdict": verdict,
        "notes": report.notes,
    })
    row["fail_reasons"] = _classify_fail_reasons(row, report)
    return row


def build(now: Optional[pd.Timestamp] = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    im = pd.read_parquet(INSTRUMENT_MASTER)
    symbols = sorted(im.loc[im["symbol"].str.endswith("USDT"), "symbol"].unique())

    rows = []
    for dataset in DATASET_SPECS:
        for symbol in symbols:
            rows.append(evaluate_dataset_symbol(dataset, symbol, im, now))

    df_rows = pd.DataFrame(rows)

    dataset_summaries = {}
    for dataset in DATASET_SPECS:
        d = df_rows[df_rows["dataset"] == dataset]
        applicable = d[d["verdict"] != "NOT_APPLICABLE"]
        n_pass = int((applicable["verdict"] == "PASS").sum())
        fail_reason_counts = {code: 0 for code in FAIL_REASON_CODES}
        for reasons in applicable["fail_reasons"]:
            for r in reasons:
                fail_reason_counts[r] = fail_reason_counts.get(r, 0) + 1
        dataset_summaries[dataset] = {
            "expected_symbols": len(applicable),
            "available_symbols": int((applicable["actual_rows"] > 0).sum()),
            "pass_symbols": n_pass,
            "pass_pct": round(n_pass / len(applicable), 4) if len(applicable) else 0.0,
            "mean_coverage_pct": round(applicable["coverage_pct"].mean(), 4) if len(applicable) else 0.0,
            "total_duplicates": int(applicable["duplicates"].sum()),
            "total_corruption": int(applicable["corruption"].sum()),
            "total_failed_days": int(applicable["failed_days"].sum()),
            "total_market_causality_violations": int(applicable["market_causality_violations"].sum()),
            "total_execution_causality_violations": int(applicable["execution_causality_violations"].sum()),
            "any_fake_flow_detected": bool(applicable["fake_flow_detected"].any()),
            "any_pit_violation": bool((applicable["pit_violations"] > 0).any()),
            # per-cause breakdown -- "why doesn't this dataset pass", at a glance
            "fail_reason_counts": fail_reason_counts,
            "not_applicable_symbols": int((d["verdict"] == "NOT_APPLICABLE").sum()),
            "confirmed_unavailable_prefix_symbols": int(applicable["confirmed_unavailable_prefix_excluded"].sum()),
        }

    applicable_all = df_rows[df_rows["verdict"] != "NOT_APPLICABLE"]
    hard_gates = {
        "corruption": int(applicable_all["corruption"].sum()) == 0,
        "fake_flow": not bool(applicable_all["fake_flow_detected"].any()),
        "causal_violation": int(
            applicable_all["market_causality_violations"].sum() + applicable_all["execution_causality_violations"].sum()
        ) == 0,
        "duplicate_pk": int(applicable_all["duplicates"].sum()) == 0,
        "gate_fail": int(applicable_all["failed_days"].sum()) == 0,
        "pit_violation": not bool((applicable_all["pit_violations"] > 0).any()),
        "funding_coverage_100pct": dataset_summaries["funding"]["pass_pct"] >= 0.99,
        "oi_coverage_gt_95pct": dataset_summaries["oi_vision_5m"]["pass_pct"] > 0.95,
        "perp_coverage_gt_98pct": dataset_summaries["perp_5m"]["pass_pct"] > 0.98,
        "spot_coverage_gt_98pct": dataset_summaries["spot_5m"]["pass_pct"] > 0.98,
        "aggtrades_coverage_gt_95pct": dataset_summaries["agg_trades_flow_5m"]["pass_pct"] > 0.95,
    }

    data_v2_ready = all(hard_gates.values())

    out = {
        "generated_at": str(now),
        "pit_universe_size": len(symbols),
        "hard_gates": hard_gates,
        "dataset_summaries": dataset_summaries,
        "rows": rows,
        "DATA_V2_READY": data_v2_ready,
    }
    return out


def main() -> None:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))

    print(f"Dataset summaries ({out['pit_universe_size']} PIT symbols):")
    for name, s in out["dataset_summaries"].items():
        print(f"  {name:22} pass={s['pass_symbols']:3}/{s['expected_symbols']:3} ({s['pass_pct']*100:5.1f}%) "
              f"mean_coverage={s['mean_coverage_pct']*100:5.1f}% failed_days={s['total_failed_days']} "
              f"not_applicable={s['not_applicable_symbols']} confirmed_unavailable_prefix={s['confirmed_unavailable_prefix_symbols']}")
        nonzero_reasons = {k: v for k, v in s["fail_reason_counts"].items() if v}
        if nonzero_reasons:
            print(f"    fail causes: {nonzero_reasons}")
    print("\nHard gates:")
    for k, v in out["hard_gates"].items():
        print(f"  {k:28} {'OK' if v else 'FAIL'}")
    print(f"\nDATA_V2_READY: {out['DATA_V2_READY']}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
