"""Top-of-book imbalance on Binance perpetuals, sixty-second horizon.

Reads the reduced capture through ``mechanisms._common.microstructure``.
Everything that decides is here and is covered by the mechanism's rules hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

from mechanisms._common.microstructure import (
    available_dates,
    forward_value,
    load_bbo_grid,
    rolling_z,
)
from research_kernel.data_contracts import DataQualityReport, Manifest, check_frame
from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.run_mechanism import DataUnavailable, MechanismRun, RunContext

BPS = 1e4
MANIFEST_DIR = Path(__file__).resolve().parents[2] / "data_lake" / "manifests"


def build(spec: MechanismSpec, ctx: RunContext) -> MechanismRun:
    e = spec.entry_rule
    x = spec.exit_rule
    venue = str(e["venue"])
    hold = float(x["holding_seconds"])
    quality = DataQualityReport(dataset_id="microstructure_imbalance_v1")

    start, end = str(spec.validation_window["start"]), str(spec.validation_window["end"])

    frames: List[pd.DataFrame] = []
    panels: List[pd.DataFrame] = []
    latencies: List[float] = []

    for symbol in spec.universe:
        dates = [
            d
            for d in available_dates(ctx.data_root, "bbo", venue, symbol)
            if start <= d <= end
        ]
        if not dates:
            quality.warnings.append("no capture for %s on %s" % (symbol, venue))
            continue
        book = load_bbo_grid(ctx.data_root, venue, symbol, dates)
        if book.empty:
            quality.warnings.append("empty capture for %s" % symbol)
            continue

        book = book.sort_values("timestamp").reset_index(drop=True)
        latencies.append(float(book["latency_ms"].quantile(0.95)))

        # coverage is a fact about the capture, not about the rule
        span = (book["timestamp"].max() - book["timestamp"].min()).total_seconds()
        coverage = len(book) / span if span else 0.0
        quality.stats["coverage_%s" % symbol] = round(coverage, 4)
        if coverage < 0.5:
            quality.warnings.append(
                "%s: the capture holds %.0f%% of one-second buckets" % (symbol, 100 * coverage)
            )

        bid_notional = book["bid_qty"] * book["bid"]
        ask_notional = book["ask_qty"] * book["ask"]
        total = bid_notional + ask_notional
        book["imbalance"] = np.where(total > 0, (bid_notional - ask_notional) / total, np.nan)
        book["z"] = rolling_z(
            book["imbalance"],
            "%ds" % int(e["zscore_window_seconds"]),
            min_periods=int(e["min_quotes_in_window"]),
            timestamps=book["timestamp"],
        )
        book["mid_fwd"] = forward_value(book["timestamp"], book["mid"], hold)
        book["fwd_bps"] = (book["mid_fwd"] - book["mid"]) / book["mid"] * BPS

        panels.append(
            book.loc[book["fwd_bps"].notna(), ["timestamp", "symbol", "fwd_bps"]].copy()
        )

        fired = book[
            (book["z"].abs() >= float(e["zscore_min_abs"]))
            & (book["spread_bps"] <= float(e["max_spread_bps"]))
            & book["fwd_bps"].notna()
        ].copy()
        if fired.empty:
            continue
        fired["side"] = np.sign(fired["z"]).astype(int)
        fired["gross_bps"] = fired["side"] * fired["fwd_bps"]
        frames.append(
            fired[
                ["timestamp", "symbol", "side", "gross_bps", "z", "imbalance", "spread_bps"]
            ]
        )

    if not frames:
        raise DataUnavailable(
            "the rule never fired: no bucket cleared the z threshold with a quote sixty "
            "seconds later"
        )

    decisions = pd.concat(frames, ignore_index=True).sort_values(
        ["timestamp", "symbol"]
    ).reset_index(drop=True)
    panel = pd.concat(panels, ignore_index=True) if panels else None

    latency_ms = float(np.max(latencies)) if latencies else float(spec.declared_data_latency_ms or 0)

    manifest = Manifest(
        dataset_id="microstructure_imbalance_v1",
        source="microstructure_reduced/raw/bbo/venue=%s" % venue,
        created_at=ctx.now,
        start=str(decisions["timestamp"].min()),
        end=str(decisions["timestamp"].max()),
        symbols=sorted(decisions["symbol"].unique().tolist()),
        latency_assumption_ms=latency_ms,
        point_in_time=True,
        notes="latency is measured as receive_ts_ns minus event_ts_ns, 95th percentile",
    )
    manifest.write(MANIFEST_DIR / "microstructure_imbalance_v1.json")

    q = check_frame(
        decisions.assign(source_latency_ms=latency_ms),
        dataset_id="microstructure_imbalance_v1_decisions",
        symbol_col="symbol",
        numeric_cols=["gross_bps", "z", "imbalance"],
        manifest=manifest,
    )
    quality.violations.extend(q.violations)
    quality.warnings.extend(q.warnings)
    quality.n_rows = int(len(decisions))
    quality.measured_latency_ms = latency_ms
    quality.measured_latency_p95_ms = latency_ms
    quality.stats["n_decisions"] = int(len(decisions))
    quality.stats["median_spread_bps"] = float(decisions["spread_bps"].median())

    return MechanismRun(
        decisions=decisions,
        quality=quality,
        panel=panel,
        trades=decisions,
        cross_sectional=False,
        measured_latency_ms=latency_ms,
        manifests=["microstructure_reduced_bbo_%s" % venue],
        notes="Ten days of capture. Whatever the verdict, the window is short.",
    )
