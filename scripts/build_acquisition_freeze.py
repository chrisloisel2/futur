#!/usr/bin/env python3
"""
scripts/build_acquisition_freeze.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, section 4: formalizes that the raw P0 acquisition
(OI/perp/spot/aggTrades/funding) is EXHAUSTED -- everything the protocol
and the sources currently permit has been attempted and correctly
classified. Writes reports/DATA_V2_ACQUISITION_FREEZE.json.

DATA_V2_ACQUISITION_EXHAUSTED=True is declared iff remaining_fetchable_
periods == 0 for every dataset, where "remaining fetchable" means a
period BOTH (a) not confirmed-unavailable at the source (the backfiller's
own manifest never got a 404 for it) AND (b) not already fetched -- i.e.
genuinely retryable. Concretely this is each backfiller's own
`failed_days`/`missing_days`-vs-`done` bookkeeping (data_v2.validation.
manifest_gaps), already exercised continuously by build_data_v2_readiness.py
-- this script reads that same bookkeeping, it does not re-derive it.

EXHAUSTED != COMPLETE. A dataset can be EXHAUSTED at 82% full-universe
coverage if the missing 18% is provably 404 at the source (VISION_OI_FLOOR-
era gaps, delisted-symbol tails, etc) -- see reports/DATA_V2_READINESS.json
for the honest coverage numbers, unchanged by this report.

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

from data_v2.validation.manifest_gaps import DATASET_MANIFEST_SPECS  # noqa: E402

READINESS = ROOT / "reports/DATA_V2_READINESS.json"
FUNDING_AUDIT = ROOT / "reports/FUNDING_FAILURE_AUDIT.json"
INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_PATH = ROOT / "reports/DATA_V2_ACQUISITION_FREEZE.json"

ACQUISITION_META = {
    "oi_vision_5m": dict(
        source="Binance Vision futures metrics (daily zip) + REST openInterestHist top-up",
        acquisition_method="scripts/extend_binance_metrics_vision_to_pit_universe.py",
        requested_period="max(VISION_OI_FLOOR, listing_ts) .. delisting_ts_or_now",
    ),
    "perp_5m": dict(
        source="Binance Vision USDT-M futures monthly klines (1m, resampled to 5m)",
        acquisition_method="data_v2/normalized/perp_ohlcv/build_perp_5m.py",
        requested_period="first_perp_kline_ts .. delisting_ts_or_now",
    ),
    "spot_5m": dict(
        source="Binance Vision spot monthly klines (1m, resampled to 5m)",
        acquisition_method="data_v2/normalized/spot_ohlcv/build_spot_5m.py",
        requested_period="first_perp_kline_ts .. delisting_ts_or_now (bound to perp, not spot's own listing)",
    ),
    "agg_trades_flow_1m": dict(
        source="Binance Vision USDT-M futures daily aggTrades",
        acquisition_method="data_v2/normalized/agg_trades/build_agg_trades_flow.py",
        requested_period="first_perp_kline_ts .. delisting_ts_or_now",
    ),
    "agg_trades_flow_5m": dict(
        source="Binance Vision USDT-M futures daily aggTrades (shares 1m's manifest)",
        acquisition_method="data_v2/normalized/agg_trades/build_agg_trades_flow.py",
        requested_period="first_perp_kline_ts .. delisting_ts_or_now",
    ),
    "funding": dict(
        source="Binance live REST /fapi/v1/fundingRate (continuous accretion, NOT a Vision batch archive)",
        acquisition_method="scripts/backfill_binance_derivatives_free.py::top_up_funding",
        requested_period="first_perp_kline_ts .. delisting_ts_or_now",
    ),
}


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _manifest_paths_for(dataset: str, symbols: list) -> list:
    if dataset == "oi_vision_5m":
        return [ROOT / f"data/derivatives_backfill/binance_vision_metrics/{s}_manifest.json" for s in symbols]
    if dataset == "perp_5m":
        return [ROOT / f"data_v2/normalized/perp_ohlcv/venue=binance/symbol={s}/manifest.json" for s in symbols]
    if dataset == "spot_5m":
        return [ROOT / f"data_v2/normalized/spot_ohlcv/venue=binance/symbol={s}/manifest.json" for s in symbols]
    if dataset in ("agg_trades_flow_1m", "agg_trades_flow_5m"):
        return [ROOT / f"data_v2/normalized/agg_trades_flow/1m/venue=binance/symbol={s}/manifest.json" for s in symbols]
    if dataset == "funding":
        return [ROOT / f"data/derivatives_backfill/binance/funding/{s}_manifest.json" for s in symbols]
    return []


def _corpus_manifest_hash(paths: list) -> str:
    """Single sha256 fingerprint of every manifest file's content for this
    dataset, sorted by path for determinism -- a manifest that doesn't
    exist contributes nothing (not an error: not every symbol has a
    confirmed-unavailable/failed manifest, most just have real data)."""
    h = hashlib.sha256()
    for p in sorted(paths, key=str):
        if p.exists():
            h.update(str(p.relative_to(ROOT)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def build_dataset_entry(dataset: str, rows: list, symbols: list) -> dict:
    spec = DATASET_MANIFEST_SPECS.get(dataset, {})
    failed_days_total = sum((r.get("failed_days") or 0) for r in rows)
    missing_days_total = sum((r.get("missing_days") or 0) for r in rows)
    corruption_total = sum((r.get("corruption") or 0) for r in rows)
    n_pass = sum(1 for r in rows if r.get("verdict") == "PASS")
    n_fail = sum(1 for r in rows if r.get("verdict") == "FAIL")

    # "remaining fetchable" = failed_days (genuine retry-pending network
    # failures the backfiller itself recorded) -- missing_days means
    # CONFIRMED-404 at the source (data_v2.validation.manifest_gaps'
    # `missing` key), not "not yet attempted", so it is NOT actionable.
    remaining_fetchable = failed_days_total

    paths = _manifest_paths_for(dataset, symbols)
    manifest_hash = _corpus_manifest_hash(paths)

    entry = {
        **ACQUISITION_META.get(dataset, {}),
        "publication_watermark": (
            "daily_publication_watermark (Phase 2 section 2), applied"
            if dataset in ("oi_vision_5m", "agg_trades_flow_1m", "agg_trades_flow_5m")
            else "_monthly_publication_watermark, applied" if dataset in ("perp_5m", "spot_5m")
            else "none -- live REST accretion, not a lagged batch archive"
        ),
        "symbols_attempted": len(symbols),
        "periods_attempted_note": "tracked per-symbol at " + spec.get("granularity", "n/a") + " granularity by the acquisition script's own manifest",
        "n_pass": n_pass,
        "n_fail": n_fail,
        "confirmed_unavailable_days_or_months": missing_days_total,
        "failed_retryable_days": failed_days_total,
        "remaining_fetchable_periods": remaining_fetchable,
        "corruption": corruption_total,
        "manifest_corpus_sha256": manifest_hash,
    }
    return entry


def main() -> None:
    readiness = json.loads(READINESS.read_text())
    funding_audit = json.loads(FUNDING_AUDIT.read_text()) if FUNDING_AUDIT.exists() else None
    im = pd.read_parquet(INSTRUMENT_MASTER, columns=["symbol"])
    all_symbols = sorted(im["symbol"].tolist())

    rows_by_dataset = {}
    for r in readiness["rows"]:
        rows_by_dataset.setdefault(r["dataset"], []).append(r)

    datasets = {}
    total_remaining_fetchable = 0
    for dataset, rows in rows_by_dataset.items():
        symbols = sorted({r["symbol"] for r in rows})
        entry = build_dataset_entry(dataset, rows, symbols)
        datasets[dataset] = entry
        total_remaining_fetchable += entry["remaining_fetchable_periods"]

    # funding's 5 remaining FAILs were individually live-probed (not just
    # manifest bookkeeping) -- fold that stronger evidence in explicitly.
    funding_note = None
    if funding_audit is not None:
        cs = funding_audit["classification_summary"]
        funding_note = (
            f"5 funding FAILs individually live-probed against Binance's fundingRate "
            f"endpoint (see reports/FUNDING_FAILURE_AUDIT.json): "
            f"{cs['FETCHABLE']} FETCHABLE, {cs['SOURCE_UNAVAILABLE']} SOURCE_UNAVAILABLE, "
            f"{cs['BOUNDARY_BUG']} BOUNDARY_BUG. FETCHABLE + BOUNDARY_BUG must be 0 for "
            f"funding to be considered exhausted despite failed_retryable_days==0."
        )
        if cs["FETCHABLE"] > 0 or cs["BOUNDARY_BUG"] > 0:
            total_remaining_fetchable += cs["FETCHABLE"] + cs["BOUNDARY_BUG"]
        datasets["funding"]["live_probe_audit"] = funding_note

    out = {
        "git_sha": _git_sha(),
        "timestamp": str(pd.Timestamp.now(tz="UTC")),
        "pit_universe_size": len(all_symbols),
        "datasets": datasets,
        "remaining_fetchable_periods_total": total_remaining_fetchable,
        "DATA_V2_ACQUISITION_EXHAUSTED": total_remaining_fetchable == 0,
        "note": (
            "EXHAUSTED means every period the protocol and the currently available "
            "sources permit has been attempted and correctly classified -- it does "
            "NOT mean full-universe coverage gates are met. See "
            "reports/DATA_V2_READINESS.json for the honest (possibly False) "
            "coverage verdict, which this report does not change."
        ),
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"DATA_V2_ACQUISITION_EXHAUSTED = {out['DATA_V2_ACQUISITION_EXHAUSTED']}")
    print(f"remaining_fetchable_periods_total = {total_remaining_fetchable}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
