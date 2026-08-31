"""
src/institutional/engines/short_covering_continuation/live_data.py
─────────────────────────────────────────────────────────────────────────────
Live OI+price loader for SHORT_COVERING_CONTINUATION_V1.

Reads data/derivatives_raw/ (written continuously by the live derivatives
collector -- scripts/run_derivatives_collector.py, process confirmed running
2026-08-29 -> today, REST poll every ~300s/symbol, verified against real
files with real mtimes as of the freeze investigation, see freeze_spec.json
`data_reconstruction_notes`). Read-only: never writes to derivatives_raw,
never imports src/institutional/data/derivatives_collector/writer.py.

This is a DIFFERENT data source than the original discovery panel
(data_v2/normalized/event_feature_panel -- a static, non-live-updating
backfill that lives only in the separate futur-data-v2 worktree). See
freeze_spec.json for the full accounting of what is/isn't verified
equivalent between the two.

Perf note: this store is many-small-files (one row per REST poll, ~240
parquet files/symbol/day). A naive per-file pd.read_parquet loop measured
~24.7s for one symbol's ~2-month history; batching the same files through
pyarrow.dataset(files).to_table() measured ~1.37s for the same data (~18x
faster) -- that's what `load_open_interest_raw` uses below.
"""
from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import List

import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = (ROOT / "data" / "derivatives_raw" / "exchange=binance"
            / "market=usdm" / "stream=open_interest")

_EMPTY_COLS = ["ts", "open_interest", "mark_price"]


def _date_strs(start: pd.Timestamp, end: pd.Timestamp) -> List[str]:
    days = pd.date_range(start.floor("D"), end.floor("D"), freq="D", tz="UTC")
    return [d.strftime("%Y-%m-%d") for d in days]


def load_open_interest_raw(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Read live open_interest parts for `symbol` over the UTC-day
    partitions spanning [start, end] (inclusive), filtered down to exactly
    that timestamp window afterwards. Returns columns: ts (tz-aware UTC),
    open_interest, mark_price -- sorted, deduped on ts.

    Returns an EMPTY DataFrame (never raises) if the symbol has no partition
    directory at all, or no rows in-window -- e.g. MKRUSDT (delisted from
    Binance USDM futures), PEPEUSDT/RNDRUSDT (collector requests the wrong
    symbol name -- Binance now lists 1000PEPEUSDT / RENDERUSDT; see
    freeze_spec.json). Callers must treat empty as "no live data for this
    symbol", not as an error -- and must not silently substitute another
    symbol's data.
    """
    sym_dir = RAW_ROOT / f"symbol={symbol}"
    if not sym_dir.exists():
        return pd.DataFrame(columns=_EMPTY_COLS)

    files: List[str] = []
    for d in _date_strs(start, end):
        files.extend(_glob.glob(str(sym_dir / f"date={d}" / "part-*.parquet")))
    if not files:
        return pd.DataFrame(columns=_EMPTY_COLS)

    table = ds.dataset(sorted(files), format="parquet").to_table(
        columns=["timestamp", "open_interest", "mark_price"])
    df = table.to_pandas()
    if df.empty:
        return pd.DataFrame(columns=_EMPTY_COLS)

    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("ts").drop_duplicates(subset="ts", keep="last")
    mask = (df["ts"] >= start) & (df["ts"] <= end)
    return df.loc[mask, _EMPTY_COLS].reset_index(drop=True)


def to_hourly_bars(raw: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Causal resample to top-of-hour bars: each hourly grid timestamp gets
    the LAST observation AT OR BEFORE it (merge_asof direction="backward"),
    matching this repo's established causal-join convention (e.g. the
    funding settlement join in data_v2's build_event_feature_panel.py) --
    structurally cannot pick a future observation. `tolerance=2h` means a
    grid hour with no observation in the trailing 2h (collector outage,
    symbol gap) correctly gets NaN rather than a stale value pinned from
    days ago. Empty-input-safe (returns an all-NaN grid)."""
    grid = pd.date_range(start.floor("h"), end.ceil("h"), freq="1h", tz="UTC")
    if raw.empty:
        return pd.DataFrame({
            "ts": grid,
            "open_interest": pd.Series([pd.NA] * len(grid), dtype="float64"),
            "mark_price": pd.Series([pd.NA] * len(grid), dtype="float64"),
        })
    grid_df = pd.DataFrame({"ts": grid})
    merged = pd.merge_asof(
        grid_df, raw.sort_values("ts"), on="ts", direction="backward",
        tolerance=pd.Timedelta(hours=2),
    )
    return merged[["ts", "open_interest", "mark_price"]]
