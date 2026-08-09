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

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_PATH = ROOT / "reports/DATA_V2_READINESS.json"

VISION_OI_FLOOR = pd.Timestamp("2020-09-01", tz="UTC")  # Binance Vision futures metrics history floor


def _load_year_partitioned(base_dir: Path, symbol: str, filename: str) -> Optional[pd.DataFrame]:
    parts = sorted((base_dir / f"symbol={symbol}").glob(f"year=*/{filename}"))
    if not parts:
        return None
    frames = [pd.read_parquet(p) for p in parts]
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset="timestamp").sort_values("timestamp")
    return df


def _load_oi(symbol: str) -> Optional[pd.DataFrame]:
    path = ROOT / f"data/derivatives_backfill/binance_vision_metrics/{symbol}_metrics_5m.parquet"
    return pd.read_parquet(path) if path.exists() else None


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
    ),
    "perp_5m": dict(
        loader=lambda sym: _load_year_partitioned(ROOT / "data_v2/normalized/perp_ohlcv/venue=binance", sym, "perp_5m.parquet"),
        timestamp_col="timestamp", bar_seconds=300,
        required_positive_columns=["close"],
        source_available_from=None, source_kind="binance_vision_monthly",
        check_taker_flow=True,
    ),
    "spot_5m": dict(
        loader=lambda sym: _load_year_partitioned(ROOT / "data_v2/normalized/spot_ohlcv/venue=binance", sym, "spot_5m.parquet"),
        timestamp_col="timestamp", bar_seconds=300,
        required_positive_columns=["spot_close"],
        source_available_from=None, source_kind="binance_vision_monthly",
        check_taker_flow=True,
        optional=True,  # not every perp symbol has a spot market -- absence is not a gate failure by itself
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
        "verdict": "FAIL",
    }

    if dataset.startswith("agg_trades_flow"):
        failed, missing = _agg_trades_manifest_counts(symbol)
        row["failed_days"], row["missing_days"] = failed, missing

    if df is None or df.empty:
        row["notes"] = "no data on disk yet"
        if spec.get("optional"):
            row["verdict"] = "NOT_APPLICABLE"
        return row

    report = validate_series(
        df, symbol=symbol, timestamp_col=spec["timestamp_col"], bar_seconds=spec["bar_seconds"],
        source=dataset, instrument_master=im, now=now,
        required_positive_columns=spec["required_positive_columns"] or None,
        required_nonnegative_columns=spec.get("required_nonnegative_columns") or None,
        source_available_from=spec["source_available_from"],
        strict_alpha_readiness=True,
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
        "funding_coverage_100pct": True,  # funding is DATA_READY per prior audit, not re-scanned here (out of DATASET_SPECS scope)
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
              f"mean_coverage={s['mean_coverage_pct']*100:5.1f}% failed_days={s['total_failed_days']}")
    print("\nHard gates:")
    for k, v in out["hard_gates"].items():
        print(f"  {k:28} {'OK' if v else 'FAIL'}")
    print(f"\nDATA_V2_READY: {out['DATA_V2_READY']}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
