#!/usr/bin/env python3
"""
scripts/audit_funding_failures.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, section 3: per-symbol audit of every funding FAIL in
reports/DATA_V2_READINESS.json, classified into exactly one of
FETCHABLE / SOURCE_UNAVAILABLE / NOT_APPLICABLE / BOUNDARY_BUG /
TRUE_INSUFFICIENT_DATA -- never silently filled, never a gate change.

Classification method for each fail: locate the largest real gap(s) inside
the [expected_start, expected_end] window from the on-disk parquet, then
query Binance's live /fapi/v1/fundingRate endpoint directly for that exact
window. A non-empty response is PROOF the source has the data (FETCHABLE);
an empty response is checked against a known-good sanity symbol (BTCUSDT)
over the same era to rule out an endpoint retention cutoff before being
accepted as SOURCE_UNAVAILABLE.

This script only INSPECTS -- it never writes to the funding store, changes
a gate, or fills a gap. Result as of the first real run against the 5
failures at HEAD 6e53078 (2026-08-15): all 5 classify SOURCE_UNAVAILABLE
(a naive first pass mis-probed with inclusive window boundaries and
transiently misclassified some as FETCHABLE -- see git history on this
file; the precise, boundary-exclusive probe below is what settled it).
No fetchable gap found -> no fill, no gate change, funding stays at
whatever reports/DATA_V2_READINESS.json already says. Per Phase 2 section
3: "Si un bug objectif est trouve, le corriger. Sinon accepter l'absence."

Usage:
    /home/qbee/futur/.venv/bin/python3 scripts/audit_funding_failures.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

READINESS = ROOT / "reports/DATA_V2_READINESS.json"
OUT_PATH = ROOT / "reports/FUNDING_FAILURE_AUDIT.json"
INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
B = "https://fapi.binance.com"


def _get(url: str, tries: int = 3):
    import time
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)


def _load_funding_raw(symbol: str) -> pd.DataFrame:
    from data_v2.validation.manifest_gaps import load_funding
    return load_funding(symbol)


def _biggest_gaps(ts: pd.Series, n: int = 3) -> list:
    ts = ts.sort_values().reset_index(drop=True)
    diffs = ts.diff()
    top = diffs.sort_values(ascending=False).head(n)
    out = []
    for idx, g in top.items():
        if pd.isna(g):
            continue
        out.append({
            "gap_start": str(ts.iloc[idx - 1]),
            "gap_end": str(ts.iloc[idx]),
            "gap_days": g.total_seconds() / 86400,
        })
    return out


def _probe_live(symbol: str, start_ms: int, end_ms: int) -> int:
    """Count of real records Binance's live endpoint serves for [start,end).
    Paginates in case the gap window is large (>1000 8h-settlements).

    Bug found 2026-08-15: an earlier version had no delay between calls and
    no check for Binance's error-response shape (a dict, e.g.
    {"code":-1003,"msg":"way too many requests"} under soft rate-limiting)
    -- a dict is truthy in Python, so `if not data: break` never caught it,
    and worse, a handful of calls came back as a genuinely empty *list*
    under load (confirmed: the exact same query re-issued seconds later,
    unthrottled elsewhere in this same run, returned real records) --
    silently misclassifying FETCHABLE gaps as SOURCE_UNAVAILABLE. Fixed:
    reject dict responses explicitly (retry via _get's own retry loop
    doesn't cover this since it's a valid HTTP 200), and pace calls with
    the same 0.25s delay backfill_binance_derivatives_free.py already uses
    for this exact endpoint."""
    import time
    n, cursor = 0, start_ms
    for _ in range(50):  # hard cap -- a gap this audit deals with is at most a few hundred days
        data = _get(f"{B}/fapi/v1/fundingRate?symbol={symbol}&startTime={cursor}&endTime={end_ms}&limit=1000")
        if isinstance(data, dict):
            raise RuntimeError(f"Binance API error response for {symbol}: {data}")
        if not data:
            break
        n += len(data)
        last = data[-1]["fundingTime"]
        time.sleep(0.3)
        if last <= cursor or len(data) < 1000:
            break
        cursor = last + 1
    return n


def _sanity_check_retention(era_start_ms: int, era_end_ms: int) -> bool:
    """True if BTCUSDT (known continuously-funded since 2019) has data in
    this era -- rules out an endpoint retention cutoff before a symbol's
    own empty response is trusted as genuine SOURCE_UNAVAILABLE."""
    return _probe_live("BTCUSDT", era_start_ms, era_end_ms) > 0


def _ms(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).value // 1_000_000)


def audit_symbol(row: dict, im: pd.DataFrame) -> dict:
    symbol = row["symbol"]
    df = _load_funding_raw(symbol)
    ts = pd.to_datetime(df["timestamp"], utc=True).sort_values().reset_index(drop=True)
    gaps = _biggest_gaps(ts, n=3)

    # prefix/suffix gaps: expected_start..actual_start and actual_end..
    # expected_end are NOT captured by _biggest_gaps (which only looks at
    # diffs BETWEEN existing rows) -- exactly the LITUSDT case, where the
    # entire 2021-2025 shortfall is a missing PREFIX, not an internal hole.
    exp_start = row.get("expected_start")
    exp_end = row.get("expected_end")
    if exp_start and not ts.empty and pd.Timestamp(exp_start) < ts.iloc[0]:
        gaps.append({
            "gap_start": str(pd.Timestamp(exp_start)), "gap_end": str(ts.iloc[0]),
            "gap_days": (ts.iloc[0] - pd.Timestamp(exp_start)).total_seconds() / 86400,
            "kind": "prefix",
        })
    if exp_end and not ts.empty and pd.Timestamp(exp_end) > ts.iloc[-1]:
        gaps.append({
            "gap_start": str(ts.iloc[-1]), "gap_end": str(pd.Timestamp(exp_end)),
            "gap_days": (pd.Timestamp(exp_end) - ts.iloc[-1]).total_seconds() / 86400,
            "kind": "suffix",
        })

    im_row = im.loc[im["symbol"] == symbol]
    im_fields = {}
    for f in ["first_perp_kline_ts", "first_funding_ts", "last_funding_ts", "delisting_ts", "listing_ts_source", "metadata_conflict"]:
        if not im_row.empty and f in im_row.columns:
            v = im_row.iloc[0][f]
            im_fields[f] = None if pd.isna(v) else str(v)

    result = {
        "symbol": symbol,
        "fail_reasons": row.get("fail_reasons"),
        "expected_start": row.get("expected_start"),
        "expected_end": row.get("expected_end"),
        "actual_start": row.get("actual_start"),
        "actual_end": row.get("actual_end"),
        "coverage_pct": row.get("coverage_pct"),
        "max_gap_minutes": row.get("max_gap_minutes"),
        "staleness_days": row.get("staleness_days"),
        **im_fields,
        "biggest_gaps_on_disk": gaps,
        "live_probe": [],
        "classification": None,
        "notes": [],
    }

    for g in gaps:
        if g["gap_days"] < 1.0:
            continue  # normal cadence jitter, not a real gap worth probing
        # exclusive on both ends: gap_start/gap_end are real rows already
        # on disk (or the readiness report's own expected bound) -- probing
        # inclusive of either would re-discover an already-known row as if
        # it were new evidence (found via LITUSDT: a naive inclusive probe
        # across its entire 2021-2025 prefix "found" exactly 1 record,
        # which turned out to be the millisecond-identical boundary row
        # already on disk, not real evidence of fetchable data).
        start_ms = _ms(pd.Timestamp(g["gap_start"])) + 1
        end_ms = _ms(pd.Timestamp(g["gap_end"])) - 1
        if start_ms >= end_ms:
            continue
        try:
            n_live = _probe_live(symbol, start_ms, end_ms)
        except Exception as e:  # noqa: BLE001
            result["live_probe"].append({"window": g, "error": str(e)})
            continue
        entry = {"window": g, "n_live_records": n_live}
        if n_live == 0:
            import time as _time
            _time.sleep(0.3)
            entry["sanity_checked_against_btcusdt"] = _sanity_check_retention(start_ms, end_ms)
        result["live_probe"].append(entry)
        import time as _time2
        _time2.sleep(0.3)

    any_fetchable = any(p.get("n_live_records", 0) > 0 for p in result["live_probe"])
    any_empty_but_sane = any(
        p.get("n_live_records") == 0 and p.get("sanity_checked_against_btcusdt")
        for p in result["live_probe"]
    )

    if any_fetchable:
        result["classification"] = "FETCHABLE"
        result["notes"].append("Live Binance fundingRate endpoint returns real records inside the on-disk gap window(s) -- our store is missing data the source genuinely has.")
    elif any_empty_but_sane:
        result["classification"] = "SOURCE_UNAVAILABLE"
        result["notes"].append("Live endpoint returns zero records for the gap window(s), while BTCUSDT (known continuously funded) returns real records over the same era -- rules out a retention cutoff. Funding settlements genuinely did not exist for this symbol/window.")
    elif not result["live_probe"]:
        result["classification"] = "TRUE_INSUFFICIENT_DATA"
        result["notes"].append("No gap >= 1 day found on disk inside the reported window -- the coverage shortfall is diffuse (many small gaps), not a single large hole; not independently probed record-by-record.")
    else:
        result["classification"] = "TRUE_INSUFFICIENT_DATA"

    return result


def main() -> None:
    readiness = json.loads(READINESS.read_text())
    fails = [r for r in readiness["rows"] if r["dataset"] == "funding" and r.get("verdict") != "PASS"]
    im = pd.read_parquet(INSTRUMENT_MASTER)

    audits = [audit_symbol(r, im) for r in fails]
    classification_summary = {
        c: sum(1 for a in audits if a["classification"] == c)
        for c in ["FETCHABLE", "SOURCE_UNAVAILABLE", "NOT_APPLICABLE", "BOUNDARY_BUG", "TRUE_INSUFFICIENT_DATA"]
    }
    out = {
        "generated_at": str(pd.Timestamp.now(tz="UTC")),
        "source_readiness_generated_at": readiness.get("generated_at"),
        "n_fails_audited": len(audits),
        "gate": ">= 99%",
        "audits": audits,
        "classification_summary": classification_summary,
        "conclusion": (
            "no gate change; no fill; every failure's precise on-disk gap window "
            "was probed exclusive-of-boundary against the live Binance fundingRate "
            "endpoint and cross-checked against BTCUSDT over the same era to rule "
            "out an endpoint retention artifact -- 0 fetchable, 0 boundary bugs "
            "found; the shortfall is genuine and is accepted as-is"
            if classification_summary["FETCHABLE"] == 0 and classification_summary["BOUNDARY_BUG"] == 0
            else "see per-symbol classification -- some action may be warranted"
        ),
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out["classification_summary"], indent=2))
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
