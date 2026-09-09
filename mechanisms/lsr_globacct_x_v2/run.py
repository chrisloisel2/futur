"""Crowd positioning, market-neutral, one day.

Reads already-produced files. Imports nothing from the legacy tree.

Sources
    ``derivatives_backfill/um_klines_1d/<SYM>_1d.parquet``      prices, volumes
    ``derivatives_backfill/binance_vision_metrics/<SYM>_metrics_5m.parquet``
                                                                crowd ratio
    ``positioning/<SYM>_global_account.parquet``                live feed freshness

The daily crowd panel is expensive to rebuild, so it is cached under
``data_lake/normalized/``. The cache is keyed by the screen, not by the rule, so
it can be reused by a different hypothesis without becoming a hidden parameter.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from research_kernel.data_contracts import DataQualityReport, Manifest, check_frame
from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.run_mechanism import DataUnavailable, MechanismRun, RunContext

BPS = 1e4
CACHE_DIR = Path(__file__).resolve().parents[2] / "data_lake" / "normalized"


# --------------------------------------------------------------------- prices
def _load_daily_prices(data_root: Path, quality: DataQualityReport) -> pd.DataFrame:
    d = Path(data_root) / "derivatives_backfill" / "um_klines_1d"
    files = sorted(glob.glob(str(d / "*_1d.parquet")))
    if not files:
        raise DataUnavailable("no daily klines under %s" % d)
    frames: List[pd.DataFrame] = []
    for f in files:
        sym = Path(f).name.replace("_1d.parquet", "")
        try:
            df = pd.read_parquet(f, columns=["open_time", "open", "close", "quote_volume"])
        except Exception as exc:  # a corrupt file is a data fact, not a crash
            quality.warnings.append("unreadable %s (%s)" % (Path(f).name, exc))
            continue
        if df.empty:
            continue
        df["symbol"] = sym
        frames.append(df)
    if not frames:
        raise DataUnavailable("every daily kline file failed to read")
    out = pd.concat(frames, ignore_index=True)
    out = out.rename(columns={"open_time": "timestamp"})
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    out = out.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    quality.stats["n_symbols_klines"] = int(out["symbol"].nunique())
    return out


# ----------------------------------------------------------------- crowd panel
def _load_crowd_panel(
    data_root: Path,
    symbols: List[str],
    metric: str,
    quality: DataQualityReport,
    cache_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Daily mean of the crowd ratio, one row per symbol and day.

    The value stamped on day ``t`` is the mean over ``[t-1, t)``, so it is fully
    published before the bar that trades on it opens.
    """
    if cache_path and cache_path.exists():
        cached = pd.read_parquet(cache_path)
        have = set(cached["symbol"].unique())
        if set(symbols).issubset(have):
            quality.warnings.append("crowd panel read from cache %s" % cache_path.name)
            return cached[cached["symbol"].isin(symbols)].copy()

    d = Path(data_root) / "derivatives_backfill" / "binance_vision_metrics"
    if not d.exists():
        raise DataUnavailable("no vision metrics under %s" % d)

    frames: List[pd.DataFrame] = []
    missing: List[str] = []
    for sym in symbols:
        f = d / ("%s_metrics_5m.parquet" % sym)
        if not f.exists():
            missing.append(sym)
            continue
        try:
            df = pd.read_parquet(f, columns=["create_time", metric])
        except Exception as exc:
            quality.warnings.append("unreadable %s (%s)" % (f.name, exc))
            continue
        if df.empty:
            continue
        df["create_time"] = pd.to_datetime(df["create_time"], utc=True)
        df = df.dropna(subset=[metric])
        if df.empty:
            continue
        # mean over [t-1, t): label the day the observation window ENDS on
        daily = (
            df.set_index("create_time")[metric]
            .resample("1D")
            .mean()
            .dropna()
            .rename("crowd_ratio")
            .reset_index()
        )
        daily["timestamp"] = daily["create_time"] + pd.Timedelta(days=1)
        daily["symbol"] = sym
        frames.append(daily[["timestamp", "symbol", "crowd_ratio"]])

    if missing:
        quality.warnings.append(
            "%d screened symbols have no vision metrics file (%s...)"
            % (len(missing), ", ".join(sorted(missing)[:5]))
        )
    if not frames:
        raise DataUnavailable("no vision metrics for any screened symbol")

    panel = pd.concat(frames, ignore_index=True)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(cache_path, index=False)
    return panel


# ------------------------------------------------------------- live freshness
def _measure_live_latency_ms(data_root: Path, quality: DataQualityReport) -> Optional[float]:
    """Freshness of the live REST feed, from its own archive.

    Gate 1 is applied to this number, not to the T+1 publication delay of the
    historical archive.
    """
    d = Path(data_root) / "positioning"
    files = sorted(glob.glob(str(d / "*_global_account.parquet")))[:20]
    if not files:
        quality.warnings.append(
            "no live positioning archive: latency falls back to the declared figure"
        )
        return None
    gaps: List[float] = []
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["timestamp"])
        except Exception:
            continue
        if len(df) < 10:
            continue
        ts = pd.to_datetime(df["timestamp"], utc=True).sort_values()
        gaps.extend(ts.diff().dt.total_seconds().dropna().tolist())
    if not gaps:
        return None
    p95 = float(np.percentile(gaps, 95))
    quality.stats["live_feed_gap_p95_s"] = p95
    quality.stats["live_feed_gap_median_s"] = float(np.median(gaps))
    # One sampling interval of staleness at worst, plus one polling interval.
    return p95 * 2.0 * 1000.0


# ---------------------------------------------------------------------- build
def build(spec: MechanismSpec, ctx: RunContext) -> MechanismRun:
    e = spec.entry_rule
    quality = DataQualityReport(dataset_id="lsr_globacct_x_v2")

    prices = _load_daily_prices(Path(ctx.data_root), quality)

    win = spec.validation_window
    start = pd.Timestamp(win["start"], tz="UTC")
    end = pd.Timestamp(win["end"], tz="UTC")

    # --- point-in-time liquidity screen, computed on closed bars only
    prices = prices.sort_values(["symbol", "timestamp"])
    prices["qv_median"] = (
        prices.groupby("symbol")["quote_volume"]
        .rolling(int(e["liquidity_lookback_days"]), min_periods=int(e["liquidity_lookback_days"]))
        .median()
        .reset_index(level=0, drop=True)
    )
    # the screen for day t uses the median as of t-1: shift by one bar
    prices["qv_median_lag"] = prices.groupby("symbol")["qv_median"].shift(1)
    prices["ret_bps"] = (prices["close"] - prices["open"]) / prices["open"] * BPS

    window = prices[(prices["timestamp"] >= start) & (prices["timestamp"] <= end)].copy()
    if window.empty:
        raise DataUnavailable("no price bars inside the validation window")

    screened = window.dropna(subset=["qv_median_lag"]).copy()
    screened["rank_liq"] = screened.groupby("timestamp")["qv_median_lag"].rank(
        ascending=False, method="first"
    )
    screened = screened[screened["rank_liq"] <= int(e["liquidity_screen_top_n"])]
    if screened.empty:
        raise DataUnavailable("liquidity screen kept nothing")

    ever_screened = sorted(screened["symbol"].unique())
    quality.stats["n_symbols_ever_screened"] = len(ever_screened)

    crowd = _load_crowd_panel(
        Path(ctx.data_root),
        ever_screened,
        str(e["crowd_metric"]),
        quality,
        cache_path=CACHE_DIR / ("lsr_globacct_daily_%s.parquet" % e["crowd_metric"]),
    )

    merged = screened.merge(crowd, on=["timestamp", "symbol"], how="inner")
    if merged.empty:
        raise DataUnavailable("the crowd panel and the screened universe never overlap")

    # --- rebalance grid: every Nth day, strictly wider than the decluster window
    days = np.array(sorted(merged["timestamp"].unique()))
    step = int(e["rebalance_step_days"])
    rebalance_days = set(pd.Timestamp(d) for d in days[::step])
    book = merged[merged["timestamp"].isin(rebalance_days)].copy()

    counts = book.groupby("timestamp")["symbol"].count()
    keep = counts[counts >= int(e["min_symbols_required"])].index
    book = book[book["timestamp"].isin(keep)]
    if book.empty:
        raise DataUnavailable(
            "no rebalance day carries at least %d screened symbols with crowd data"
            % int(e["min_symbols_required"])
        )

    # --- the rule: long the least-long crowd, short the most-long crowd
    n = int(e["basket_size"])
    book["rank_crowd"] = book.groupby("timestamp")["crowd_ratio"].rank(
        ascending=True, method="first"
    )
    book["rank_crowd_desc"] = book.groupby("timestamp")["crowd_ratio"].rank(
        ascending=False, method="first"
    )
    longs = book[book["rank_crowd"] <= n].copy()
    longs["side"] = 1
    shorts = book[book["rank_crowd_desc"] <= n].copy()
    shorts["side"] = -1
    decisions = pd.concat([longs, shorts], ignore_index=True)
    decisions = decisions[~decisions.duplicated(subset=["timestamp", "symbol"], keep=False)]

    decisions["gross_bps"] = decisions["side"] * decisions["ret_bps"]
    decisions["decision_ts"] = decisions["timestamp"]
    decisions["available_at"] = decisions["timestamp"]
    decisions = decisions[
        ["timestamp", "symbol", "side", "gross_bps", "crowd_ratio", "qv_median_lag"]
    ].sort_values(["timestamp", "symbol"]).reset_index(drop=True)

    # --- panel of forward returns for the shuffle placebos
    panel = merged[["timestamp", "symbol", "ret_bps"]].rename(
        columns={"ret_bps": "fwd_bps"}
    )

    # --- live feed freshness, measured before the manifest is written
    latency = _measure_live_latency_ms(Path(ctx.data_root), quality)
    measured = latency is not None
    if not measured:
        latency = float(spec.declared_data_latency_ms or 0.0)
        quality.warnings.append(
            "live feed latency is the declared figure, not a measurement"
        )

    manifest = Manifest(
        dataset_id="lsr_globacct_x_v2",
        source="binance_vision_metrics + um_klines_1d",
        created_at=ctx.now,
        start=str(decisions["timestamp"].min()),
        end=str(decisions["timestamp"].max()),
        symbols=sorted(decisions["symbol"].unique().tolist()),
        latency_assumption_ms=latency,
        point_in_time=True,
        notes=(
            "universe screened on closed bars only; delisted symbols keep their "
            "history. Latency is the live REST feed, %s."
            % ("measured" if measured else "declared")
        ),
    )
    manifest.write(
        Path(__file__).resolve().parents[2]
        / "data_lake"
        / "manifests"
        / "lsr_globacct_x_v2.json"
    )

    # --- gate 0 checks on what was actually used
    q = check_frame(
        decisions.assign(source_latency_ms=latency),
        dataset_id="lsr_globacct_x_v2_decisions",
        symbol_col="symbol",
        numeric_cols=["gross_bps", "crowd_ratio"],
        manifest=manifest,
    )
    quality.violations.extend(q.violations)
    quality.warnings.extend(q.warnings)
    quality.n_rows = int(len(decisions))
    quality.stats["n_rebalances"] = int(decisions["timestamp"].nunique())
    quality.stats["n_symbols_traded"] = int(decisions["symbol"].nunique())
    quality.measured_latency_ms = latency

    return MechanismRun(
        decisions=decisions,
        quality=quality,
        panel=panel,
        trades=decisions,
        cross_sectional=True,
        measured_latency_ms=latency,
        manifests=["binance_vision_metrics_5m", "binance_um_klines_1d"],
        notes=(
            "Historical replication on archived data this family has already examined. "
            "Promotion requires a forward window on the live REST feed."
        ),
    )
