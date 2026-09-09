"""Binance against OKX, same symbol, sixty seconds.

Both legs are priced by the spec's cost model. Mid prices only: displayed
quantities are not comparable across the two venues, so nothing here depends on
size.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

from mechanisms._common.microstructure import (
    available_dates,
    load_bbo_grid,
    rolling_z,
)
from research_kernel.data_contracts import DataQualityReport, Manifest, check_frame
from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.run_mechanism import DataUnavailable, MechanismRun, RunContext

BPS = 1e4
MANIFEST_DIR = Path(__file__).resolve().parents[2] / "data_lake" / "manifests"


def _exit_dislocation(
    ts: np.ndarray,
    disloc: np.ndarray,
    entry_idx: np.ndarray,
    hold_seconds: float,
    revert_fraction: float,
) -> np.ndarray:
    """Dislocation at the exit: whichever comes first, half the gap closed or the
    holding time elapsed."""
    out = np.full(len(entry_idx), np.nan)
    horizon = int(hold_seconds * 1_000_000_000)  # ts is in nanoseconds
    for k, i in enumerate(entry_idx):
        d0 = disloc[i]
        stop = ts[i] + horizon
        j_end = np.searchsorted(ts, stop, side="right") - 1
        if j_end <= i:
            continue
        window = disloc[i + 1 : j_end + 1]
        target = abs(d0) * (1.0 - revert_fraction)
        hit = np.where(np.abs(window) <= target)[0]
        out[k] = window[hit[0]] if len(hit) else window[-1]
    return out


def build(spec: MechanismSpec, ctx: RunContext) -> MechanismRun:
    e = spec.entry_rule
    x = spec.exit_rule
    va, vb = str(e["venue_a"]), str(e["venue_b"])
    hold = float(x["holding_seconds"])
    quality = DataQualityReport(dataset_id="cross_exchange_dislocation_v1")

    start, end = str(spec.validation_window["start"]), str(spec.validation_window["end"])

    frames: List[pd.DataFrame] = []
    panels: List[pd.DataFrame] = []
    latencies: List[float] = []

    for symbol in spec.universe:
        dates_a = [d for d in available_dates(ctx.data_root, "bbo", va, symbol) if start <= d <= end]
        dates_b = [d for d in available_dates(ctx.data_root, "bbo", vb, symbol) if start <= d <= end]
        common = sorted(set(dates_a) & set(dates_b))
        if not common:
            quality.warnings.append("no day where both %s and %s captured %s" % (va, vb, symbol))
            continue

        a = load_bbo_grid(ctx.data_root, va, symbol, common)
        b = load_bbo_grid(ctx.data_root, vb, symbol, common)
        if a.empty or b.empty:
            quality.warnings.append("one venue is empty for %s" % symbol)
            continue
        latencies.append(float(max(a["latency_ms"].quantile(0.95), b["latency_ms"].quantile(0.95))))

        merged = pd.merge_asof(
            a.sort_values("timestamp")[["timestamp", "mid", "spread_bps"]],
            b.sort_values("timestamp")[["timestamp", "mid", "spread_bps"]],
            on="timestamp",
            suffixes=("_a", "_b"),
            tolerance=pd.Timedelta(seconds=float(e["max_clock_skew_seconds"])),
            direction="nearest",
        ).dropna(subset=["mid_a", "mid_b"])
        if merged.empty:
            quality.warnings.append("the two captures never align for %s" % symbol)
            continue

        merged["symbol"] = symbol
        merged["disloc_bps"] = (merged["mid_b"] - merged["mid_a"]) / merged["mid_a"] * BPS
        merged["z"] = rolling_z(
            merged["disloc_bps"],
            "%ds" % int(e["zscore_window_seconds"]),
            min_periods=60,
            timestamps=merged["timestamp"],
        )
        quality.stats["median_abs_dislocation_bps_%s" % symbol] = float(
            merged["disloc_bps"].abs().median()
        )
        quality.stats["p99_abs_dislocation_bps_%s" % symbol] = float(
            merged["disloc_bps"].abs().quantile(0.99)
        )

        ts = pd.to_datetime(merged["timestamp"], utc=True).astype("int64").to_numpy()
        disloc = merged["disloc_bps"].to_numpy()

        fires = (
            (merged["z"].abs() >= float(e["zscore_min_abs"]))
            & (merged["disloc_bps"].abs() >= float(e["min_dislocation_bps"]))
            & (merged["spread_bps_a"] <= float(e["max_spread_bps"]))
            & (merged["spread_bps_b"] <= float(e["max_spread_bps"]))
        ).to_numpy()
        entry_idx = np.where(fires)[0]
        if len(entry_idx) == 0:
            continue

        exit_disloc = _exit_dislocation(
            ts, disloc, entry_idx, hold, float(x["mean_reversion_target_fraction"])
        )
        keep = ~np.isnan(exit_disloc)
        entry_idx, exit_disloc = entry_idx[keep], exit_disloc[keep]
        if len(entry_idx) == 0:
            continue

        d0 = disloc[entry_idx]
        gross = np.sign(d0) * (d0 - exit_disloc)

        frames.append(
            pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(ts[entry_idx], unit="ns", utc=True),
                    "symbol": symbol,
                    # +1 means long venue_a and short venue_b
                    "side": np.sign(d0).astype(int),
                    "gross_bps": gross,
                    "disloc_entry_bps": d0,
                    "disloc_exit_bps": exit_disloc,
                    "z": merged["z"].to_numpy()[entry_idx],
                }
            )
        )

        # placebo panel: the sixty-second change in dislocation at every instant
        fwd = pd.Series(disloc).shift(-int(hold)).to_numpy()
        panel_bps = disloc - fwd
        panels.append(
            pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(ts, unit="ns", utc=True),
                    "symbol": symbol,
                    "fwd_bps": panel_bps,
                }
            ).dropna()
        )

    if not frames:
        raise DataUnavailable(
            "the rule never fired: no instant cleared both the z threshold and the "
            "minimum dislocation with an exit inside the holding window"
        )

    decisions = pd.concat(frames, ignore_index=True).sort_values(
        ["timestamp", "symbol"]
    ).reset_index(drop=True)
    panel = pd.concat(panels, ignore_index=True) if panels else None
    latency_ms = float(np.max(latencies)) if latencies else float(spec.declared_data_latency_ms or 0)

    manifest = Manifest(
        dataset_id="cross_exchange_dislocation_v1",
        source="microstructure_reduced/raw/bbo venues %s and %s" % (va, vb),
        created_at=ctx.now,
        start=str(decisions["timestamp"].min()),
        end=str(decisions["timestamp"].max()),
        symbols=sorted(decisions["symbol"].unique().tolist()),
        latency_assumption_ms=latency_ms,
        point_in_time=True,
        notes=(
            "mid prices only; displayed quantities are not comparable across venues "
            "(Binance quotes base units, OKX quotes contracts)"
        ),
    )
    manifest.write(MANIFEST_DIR / "cross_exchange_dislocation_v1.json")

    q = check_frame(
        decisions.assign(source_latency_ms=latency_ms),
        dataset_id="cross_exchange_dislocation_v1_decisions",
        symbol_col="symbol",
        numeric_cols=["gross_bps", "disloc_entry_bps", "z"],
        manifest=manifest,
    )
    quality.violations.extend(q.violations)
    quality.warnings.extend(q.warnings)
    quality.n_rows = int(len(decisions))
    quality.measured_latency_ms = latency_ms
    quality.measured_latency_p95_ms = latency_ms

    return MechanismRun(
        decisions=decisions,
        quality=quality,
        panel=panel,
        trades=decisions,
        cross_sectional=False,
        measured_latency_ms=latency_ms,
        manifests=["microstructure_reduced_bbo_%s" % va, "microstructure_reduced_bbo_%s" % vb],
        notes="Both legs priced. Ten days of capture on three symbols.",
    )
