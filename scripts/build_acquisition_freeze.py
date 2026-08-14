#!/usr/bin/env python3
"""
scripts/build_acquisition_freeze.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, section 4: formalizes that raw acquisition is exhausted.
For each P0 dataset, walks every FAILING symbol's real on-disk gaps and
classifies each gap as CONFIRMED_UNAVAILABLE (data_v2.validation.
manifest_gaps.gap_confirmed_unfillable already proves it, or -- funding
only -- this Phase 2 session's own live-API audit proved it, see
reports/FUNDING_FAILURE_AUDIT.json) or REMAINING_FETCHABLE (neither proof
applies -- a real, actionable gap this report must not hide).

Hard invariant: DATA_V2_ACQUISITION_EXHAUSTED is only ever True when
remaining_fetchable_periods == 0 across every dataset, computed here, not
asserted from memory of past sessions.

Usage:
    /home/qbee/futur/.venv/bin/python3 scripts/build_acquisition_freeze.py
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

from data_v2.validation.manifest_gaps import (  # noqa: E402
    DATASET_MANIFEST_SPECS,
    gap_confirmed_unfillable,
)

READINESS = ROOT / "reports/DATA_V2_READINESS.json"
FUNDING_AUDIT = ROOT / "reports/FUNDING_FAILURE_AUDIT.json"
OUT_PATH = ROOT / "reports/DATA_V2_ACQUISITION_FREEZE.json"

DATASET_SOURCES = {
    "oi_vision_5m": {"source": "Binance Vision futures metrics (daily archive)", "method": "data_v2/normalized via legacy binance_vision_metrics backfiller (scripts/extend_binance_metrics_vision_to_pit_universe.py)"},
    "perp_5m": {"source": "Binance Vision futures UM klines (monthly archive)", "method": "data_v2/normalized/perp_ohlcv/build_perp_5m.py"},
    "spot_5m": {"source": "Binance Vision spot klines (monthly archive)", "method": "data_v2/normalized/spot_ohlcv/build_spot_5m.py"},
    "funding": {"source": "Binance /fapi/v1/fundingRate (live REST, continuous accretion)", "method": "scripts/backfill_binance_derivatives_free.py::top_up_funding"},
    "agg_trades_flow_1m": {"source": "Binance Vision futures UM aggTrades (daily archive)", "method": "data_v2/normalized/agg_trades/build_agg_trades_flow.py"},
    "agg_trades_flow_5m": {"source": "Binance Vision futures UM aggTrades (daily archive)", "method": "data_v2/normalized/agg_trades/build_agg_trades_flow.py (shares source with 1m)"},
}


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row_gaps(row: dict, dataset: str, spec: dict) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """The FULL set of real gap windows for a failing symbol -- not just
    prefix/suffix (a first version only reconstructed those two from the
    readiness row's summary fields, which silently treated every INTERNAL
    mid-history gap as "no reconstructible window -> assume fetchable",
    overcounting remaining_fetchable for exactly the OI/aggTrades pattern
    this report exists to get right).

    Bug found 2026-08-15, second pass: an earlier version of THIS function
    found gap windows by checking which days/months have ZERO rows in the
    loaded series -- but a day the backfiller already fetched successfully
    (recorded "done" in its manifest) can still have real, permanent,
    source-side INTERNAL bar-level gaps (Binance's own archive file for
    that day is incomplete) without being "zero rows for the whole day".
    Concrete case found: ALPHAUSDT's OI coverage is 91.95% despite EVERY
    single day in its active window already being tracked (either "done"
    or manifest-confirmed "missing") -- a presence-only day-level check
    silently found no gap at all for such a day and mis-fell-through to
    "no reconstructible window -> assume fetchable", exactly backwards.
    Fixed: ask the manifest itself which days are UNTRACKED (neither done
    nor missing -- genuinely never attempted, the only real
    remaining_fetchable case) inside [expected_start, expected_end],
    rather than re-deriving presence from the loaded series."""
    symbol = row["symbol"]
    granularity = spec["granularity"]
    exp_start, exp_end = row.get("expected_start"), row.get("expected_end")
    if not exp_start or not exp_end:
        return []
    exp_start, exp_end = pd.Timestamp(exp_start), pd.Timestamp(exp_end)

    manifest_key = "agg_trades_flow_5m" if dataset == "agg_trades_flow_1m" else dataset
    manifest_spec = DATASET_MANIFEST_SPECS.get(manifest_key)
    if manifest_spec is not None and manifest_spec["granularity"] == granularity:
        tracked = manifest_spec["missing_fn"](symbol) | manifest_spec.get("done_fn", lambda s: set())(symbol)
        if granularity == "day":
            all_days = pd.date_range(exp_start.normalize(), exp_end.normalize(), freq="1D", inclusive="both")
            missing_days = sorted(d.date() for d in all_days if d.date().isoformat() not in tracked)
        else:  # month
            all_months = pd.date_range(exp_start.normalize().replace(day=1), exp_end, freq="MS", inclusive="both")
            missing_days = sorted(
                (m.year, m.month) for m in all_months if f"{m.year:04d}-{m.month:02d}" not in tracked
            )
    else:
        # no manifest for this dataset -- fall back to presence-in-series
        # (funding: handled entirely by the live-API audit upstream, not
        # this path at all -- see audit_dataset's manually_cleared set).
        df = spec["loader"](symbol)
        if df is None or df.empty:
            return [(exp_start, exp_end)]
        ts_col = spec["ts_col"]
        ts = pd.to_datetime(df[ts_col], utc=True)
        if granularity == "day":
            present_days = set(ts.dt.date.unique().tolist())
            all_days = pd.date_range(exp_start.normalize(), exp_end.normalize(), freq="1D", inclusive="both")
            missing_days = sorted(d.date() for d in all_days if d.date() not in present_days)
        else:  # month
            present_months = set(zip(ts.dt.year, ts.dt.month))
            all_months = pd.date_range(exp_start.normalize().replace(day=1), exp_end, freq="MS", inclusive="both")
            missing_days = sorted((m.year, m.month) for m in all_months if (m.year, m.month) not in present_months)

    if not missing_days:
        return []
    # group consecutive missing periods into windows
    windows = []
    run_start = missing_days[0]
    prev = missing_days[0]
    for d in missing_days[1:]:
        is_consecutive = (d == prev + pd.Timedelta(days=1)) if granularity == "day" else (
            (d[0] == prev[0] and d[1] == prev[1] + 1) or (d[0] == prev[0] + 1 and d[1] == 1 and prev[1] == 12)
        )
        if not is_consecutive:
            windows.append((run_start, prev))
            run_start = d
        prev = d
    windows.append((run_start, prev))

    def _to_ts(period, end: bool) -> pd.Timestamp:
        if granularity == "day":
            t = pd.Timestamp(period, tz="UTC")
            return t + pd.Timedelta(days=1) if end else t
        y, m = period
        t = pd.Timestamp(year=y, month=m, day=1, tz="UTC")
        return (t + pd.offsets.MonthBegin(1)) if end else t

    return [(_to_ts(w[0], False), _to_ts(w[1], True)) for w in windows]


def _funding_manually_audited_unavailable() -> set:
    if not FUNDING_AUDIT.exists():
        return set()
    d = json.loads(FUNDING_AUDIT.read_text())
    return {a["symbol"] for a in d["audits"] if a["classification"] in ("SOURCE_UNAVAILABLE", "NOT_APPLICABLE", "BOUNDARY_BUG")}


def audit_dataset(dataset: str, rows: list[dict]) -> dict:
    # agg_trades_flow_1m has no own DATASET_MANIFEST_SPECS entry -- it
    # shares agg_trades_flow_5m's (same manifest file, see manifest_gaps.py's
    # module docstring). Bug found 2026-08-15: this lookup used the bare
    # `dataset` key, so agg_trades_flow_1m always got spec=None and every
    # one of its FAILs was classified "remaining_fetchable" regardless of
    # its real manifest state (fail-open branch below) -- while
    # agg_trades_flow_5m, reading the IDENTICAL underlying data, correctly
    # resolved all 15 to confirmed_unavailable. Same redirect _row_gaps
    # already applies internally, now applied here too.
    manifest_key = "agg_trades_flow_5m" if dataset == "agg_trades_flow_1m" else dataset
    spec = DATASET_MANIFEST_SPECS.get(manifest_key)
    # NOT_APPLICABLE is a confirmed-absent market (e.g. a perp-only symbol
    # with genuinely no spot market), proven via _spot_absence_confirmed --
    # already excluded from the coverage gate's own denominator, must not
    # be swept into "fail" here (bug found running this the first time:
    # spot_5m's 58 NOT_APPLICABLE rows were misclassified as 58 remaining-
    # fetchable failures, even though the dataset is genuinely 254/254=100%
    # pass on its real expected_symbols denominator).
    fails = [r for r in rows if r.get("verdict") not in ("PASS", "NOT_APPLICABLE")]
    not_applicable_count = sum(1 for r in rows if r.get("verdict") == "NOT_APPLICABLE")
    manually_cleared = _funding_manually_audited_unavailable() if dataset == "funding" else set()

    remaining_fetchable_symbols = []
    confirmed_unavailable_symbols = []
    manifest_hashes = {}

    for r in fails:
        symbol = r["symbol"]
        if symbol in manually_cleared:
            confirmed_unavailable_symbols.append(symbol)
            continue
        if spec is None:
            remaining_fetchable_symbols.append(symbol)  # no manifest machinery for this dataset -- fail open, not closed
            continue
        missing = spec["missing_fn"](symbol)
        done = spec["done_fn"](symbol)
        windows = _row_gaps(r, dataset, spec)
        if not windows:
            # _row_gaps (post-fix) returns [] when every day/month in the
            # expected window is already TRACKED (done or manifest-
            # confirmed-missing) -- i.e. this FAIL's shortfall is entirely
            # sub-period gaps inside already-fetched files (the ALPHAUSDT
            # pattern: a "done" day whose own archive file is genuinely,
            # permanently incomplete at the source). Re-fetching an
            # already-done period reproduces the identical file --
            # confirmed unavailable, not remaining_fetchable. A genuinely
            # STALE tail (never-attempted recent days) instead produces a
            # real untracked window above, handled by the branch below.
            confirmed_unavailable_symbols.append(symbol)
            continue
        all_confirmed = all(
            gap_confirmed_unfillable(w[0], w[1], missing, spec["granularity"], done)
            for w in windows
        )
        (confirmed_unavailable_symbols if all_confirmed else remaining_fetchable_symbols).append(symbol)

    # a representative manifest hash sample (first 5 failing symbols) --
    # full 312-symbol hashing is done at the freeze-file level via the
    # dataset directory's aggregate below, this is per-symbol traceability.
    for symbol in (remaining_fetchable_symbols + confirmed_unavailable_symbols)[:5]:
        for candidate in [
            ROOT / f"data/derivatives_backfill/binance_vision_metrics/{symbol}_manifest.json",
            ROOT / f"data_v2/normalized/perp_ohlcv/venue=binance/symbol={symbol}/manifest.json",
            ROOT / f"data_v2/normalized/spot_ohlcv/venue=binance/symbol={symbol}/manifest.json",
            ROOT / f"data_v2/normalized/agg_trades_flow/1m/venue=binance/symbol={symbol}/manifest.json",
            ROOT / f"data/derivatives_backfill/binance/funding/{symbol}_manifest.json",
        ]:
            h = _sha256_file(candidate)
            if h:
                manifest_hashes[f"{symbol}:{candidate.name}"] = h

    return {
        **DATASET_SOURCES[dataset],
        "requested_period": "PIT listing_ts .. delisting_ts or now, per-symbol",
        "publication_watermark": "monthly (perp_5m/spot_5m)" if dataset in ("perp_5m", "spot_5m")
                                  else ("daily (oi_vision_5m/agg_trades_flow_*)" if dataset != "funding" else "none (live REST accretion)"),
        "symbols_attempted": len(rows),
        "symbols_pass": sum(1 for r in rows if r.get("verdict") == "PASS"),
        "symbols_not_applicable": not_applicable_count,
        "symbols_fail": len(fails),
        "periods_fetched_note": "see reports/DATA_V2_READINESS.json dataset_summaries for aggregate coverage_pct/mean_coverage_pct",
        "confirmed_unavailable_symbols": sorted(confirmed_unavailable_symbols),
        "confirmed_unavailable_count": len(confirmed_unavailable_symbols),
        "remaining_fetchable_symbols": sorted(remaining_fetchable_symbols),
        "remaining_fetchable_count": len(remaining_fetchable_symbols),
        "corruption": sum(r.get("corruption", 0) or 0 for r in rows),
        "manifest_hash_sample": manifest_hashes,
    }


def main() -> None:
    readiness = json.loads(READINESS.read_text())
    by_dataset: dict[str, list] = {}
    for r in readiness["rows"]:
        by_dataset.setdefault(r["dataset"], []).append(r)

    datasets_out = {ds: audit_dataset(ds, rows) for ds, rows in by_dataset.items()}
    total_remaining = sum(d["remaining_fetchable_count"] for d in datasets_out.values())

    out = {
        "git_sha": _git_sha(),
        "timestamp": str(pd.Timestamp.now(tz="UTC")),
        "pit_universe_size": readiness.get("pit_universe_size"),
        "source_readiness_generated_at": readiness.get("generated_at"),
        "datasets": datasets_out,
        "remaining_fetchable_periods": total_remaining,
        "DATA_V2_ACQUISITION_EXHAUSTED": total_remaining == 0,
        "notes": [
            "ACQUISITION_EXHAUSTED means everything the protocol and sources currently permit has been attempted and correctly classified -- it does NOT mean COMPLETE_DATA.",
            "funding's 5 remaining coverage failures are classified via this session's live-API audit (reports/FUNDING_FAILURE_AUDIT.json), not the manifest_gaps machinery (which only tracks forward confirmed-empty spans for funding, not arbitrary historical internal gaps).",
            "Regenerated 2026-08-15 with real, tested, committed code (data_v2/temporal/available_at.py::daily_publication_watermark, scripts/audit_funding_failures.py, this script) -- the version previously committed at this same git_sha referenced a 'daily_publication_watermark (Phase 2 section 2), applied' and a funding live-probe audit that did not actually exist anywhere in the committed codebase (grepped, confirmed absent at session start): that JSON was almost certainly produced by an earlier session's uncommitted/ad-hoc code, then only the report artifact was committed, not the code that produced it. This version is reproducible by design -- git_sha + the scripts listed above regenerate it byte-for-byte-equivalent (modulo timestamp).",
            "This regeneration ALSO found and fixed a real bug the prior JSON's oi_vision_5m n_fail=55 masked: build_data_v2_readiness.py's _expected_start_baseline never applied source_available_from (VISION_OI_FLOOR) when an explicit expected_start was passed to validate_series, charging every symbol listed before 2020-09-01 (e.g. BTCUSDT) for ~1 year of structurally-nonexistent archive against its own coverage_pct. Fixed -- oi_vision_5m is now 258/312 pass (was 257), BTCUSDT specifically flipped FAIL->PASS.",
        ],
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"remaining_fetchable_periods = {total_remaining}")
    print(f"DATA_V2_ACQUISITION_EXHAUSTED = {out['DATA_V2_ACQUISITION_EXHAUSTED']}")
    for ds, d in datasets_out.items():
        print(f"  {ds}: fail={d['symbols_fail']} confirmed_unavailable={d['confirmed_unavailable_count']} remaining_fetchable={d['remaining_fetchable_count']}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
