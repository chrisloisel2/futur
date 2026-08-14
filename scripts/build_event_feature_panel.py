#!/usr/bin/env python3
"""
scripts/build_event_feature_panel.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 10 (mission section 10): the canonical, causal per-(symbol,
timestamp) event feature panel -- the SOLE historical entry point for the
Event Scanner. Populates data_v2.events.schema.REQUIRED_COLUMNS for real,
from real Data V2 sources, replacing the synthetic fixtures every detector/
label/scanner test has used so far.

Anchor: a REGULAR 5m grid per symbol, spanning [first real perp bar, last
real perp bar]. This is deliberately NOT just "whatever rows exist in
perp_5m.parquet" -- both residuals.py (`.shift(n)`) and labels.py
(`log_ret_5m[i:i+n_bars]`) assume row-offset == wall-clock offset (n rows
ahead means exactly n*5 minutes ahead). If the grid silently skipped a gap
bar instead of representing it, a shift(12)/12-bar slice spanning that gap
would silently compute a return over MORE than 1h of wall-clock time while
still being labelled "1h". Reindexing onto a dense grid makes every real
gap an explicit NaN row instead -- the correct, fail-closed representation
-- without changing what any downstream positional-offset math means.

Every other source is LEFT-joined onto that grid by reindexing onto the
SAME timestamps (an exact-match join -- a source with no row at a given
grid timestamp contributes NaN there, never a nearest-neighbour guess),
except funding, which is explicitly a discrete settlement event per
mission section 11 and is joined via a strictly-causal
merge_asof(direction="backward") instead -- never a forward/nearest join.

Sources:
  perp_ohlcv          -- open, close, volume (the grid itself)
  spot_ohlcv + basis  -- basis, basis_z_1d/_7d (from the already-built
                         data_v2/normalized/basis store: exact perp/spot
                         join, shift(1) z-scores, see data_v2/features/
                         basis.py)
  oi_vision_5m        -- oi = sum_open_interest; oi_delta_pct_1h =
                         oi.pct_change(12) computed AFTER reindexing onto
                         the dense grid, so a gap correctly yields NaN
                         rather than silently pairing non-adjacent-in-time
                         observations
  agg_trades_flow_5m  -- aggressive_buy_usd/sell_usd, signed_volume, CVD
  funding             -- funding_rate = the latest real settlement's rate,
                         causally forward-filled ONLY from that
                         settlement's own bar onward (never before);
                         funding_is_settlement, time_since_last_funding
  residuals           -- residual_logret_5m/_15m/_1h vs BTC/ETH
                         (data_v2.events.residuals), fed the dense-grid-
                         reindexed close series so its own shift-based
                         causal betas mean what they claim to mean
  liquidations        -- NOT wired into this panel yet -- a separate raw
                         stream, not part of the Data V2 P0 corpus this
                         mission covers. liq_feed_available=False for
                         every row: correctly "unknown", never a
                         fabricated "0 liquidations". Documented
                         limitation, not a silently dropped column.

feature_available_at (mission section 10's term) is stored AS the schema's
`research_available_at` column -- the name every existing, already-tested
detector/label/scanner call site reads as THE causal cutoff. It is the
row-wise max of research_available_at across every source that actually
contributed a non-null value to that row (a row with no OI yet, say, is
gated only by whichever sources really fed it). Each source's own
research_available_at is masked to NaN wherever that source itself is
null at that row before the max, so an absent source never drags the row
backward in time or silently gets skipped from the max by accident.

CROWDING percentile (mission section 11, fixed 2026-08-14): funding_rate_
percentile_90d is computed here from the REAL settlement history only --
for each settlement, its percentile rank of |funding_rate| among the
settlements in the strictly-prior 90 days (current settlement excluded
from its own reference population, same discipline as detectors.py's
_trailing_percentile_rank), then causally forward-filled onto the grid
exactly like funding_rate itself. detect_crowding (data_v2/events/
detectors.py) reads this column directly instead of ranking the panel's
forward-filled bar copies, which would otherwise repeat one real
settlement ~96x for an 8h-cadence symbol, ~48x for 4h, ~12x for 1h --
distorting the population for any non-uniform-cadence symbol (e.g.
AIAUSDT, which mixes 1h/4h settlements). Same P90 threshold, same 90d
window -- no tuning, just the correct population.

Disk-safety: run in --min-free-gb guarded batches, symbol by symbol, same
idiom as the P0 backfills. Writes are atomic (tmp-then-replace) per
symbol, so a run that stops partway through is resumable and never leaves
a half-written file.

    python3 scripts/build_event_feature_panel.py --min-free-gb 15
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_v2.temporal.available_at import add_temporal_columns  # noqa: E402
from data_v2.events.residuals import (  # noqa: E402
    compute_residual_returns, BETA_WINDOW_BARS, BTC_SYMBOL, ETH_SYMBOL,
)
from data_v2.validation.manifest_gaps import load_oi, load_funding, load_year_partitioned  # noqa: E402
from src.institutional.data.atomic_parquet import atomic_write_parquet  # noqa: E402

PERP_DIR = ROOT / "data_v2/normalized/perp_ohlcv/venue=binance"
BASIS_DIR = ROOT / "data_v2/normalized/basis/venue=binance"
AGG_5M_DIR = ROOT / "data_v2/normalized/agg_trades_flow/5m/venue=binance"
INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_DIR = ROOT / "data_v2/normalized/event_feature_panel/venue=binance"

GRID_FREQ = "5min"


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def _research_available_at(ts: pd.Series, bar_seconds: int, source_kind: str, now: pd.Timestamp) -> pd.Series:
    """Thin wrapper around add_temporal_columns for a bare timestamp
    Series -- returns just the research_available_at column, index-aligned
    to `ts`."""
    tmp = pd.DataFrame({"__event_time__": pd.to_datetime(ts, utc=True).to_numpy()}, index=ts.index)
    out = add_temporal_columns(
        tmp, event_time_col="__event_time__", source_kind=source_kind,
        bar_seconds=bar_seconds, provably_live_observable=True, now=now,
    )
    return out["research_available_at"]


def load_perp_close(symbol: str) -> Optional[pd.Series]:
    df = load_year_partitioned(PERP_DIR, symbol, "perp_5m.parquet")
    if df is None or df.empty:
        return None
    df = df[["timestamp", "close"]].dropna(subset=["timestamp"]).sort_values("timestamp")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.drop_duplicates(subset="timestamp", keep="last")
    return df.set_index("timestamp")["close"]


def _dense_grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq=GRID_FREQ, tz="UTC")


def _settlement_percentile_rank(abs_rate: pd.Series, lookback_days: int = 90) -> pd.Series:
    """Percentile rank (0-1) of each REAL settlement's |funding_rate|
    against the settlements in the STRICTLY PRIOR `lookback_days` --
    current settlement excluded from its own reference population, same
    discipline as detectors.py's _trailing_percentile_rank (never judge a
    value against a window containing itself). `abs_rate` must be indexed
    by real settlement time, sorted ascending. O(n) via a two-pointer
    sliding window -- settlements are sparse (at most a few thousand per
    symbol over the full history), so this is cheap regardless."""
    times = abs_rate.index
    values = abs_rate.to_numpy()
    window = pd.Timedelta(days=lookback_days)
    out = np.full(len(values), np.nan)
    start_ptr = 0
    for i in range(len(values)):
        window_start = times[i] - window
        while times[start_ptr] < window_start:
            start_ptr += 1
        hist = values[start_ptr:i]  # [start_ptr, i) -- strictly prior, i itself excluded
        if len(hist) > 0:
            out[i] = float((hist <= values[i]).mean())
    return pd.Series(out, index=times)


def _build_funding_columns(grid: pd.DatetimeIndex, symbol: str, now: pd.Timestamp) -> pd.DataFrame:
    """Discrete settlement -> causal forward-fill onto the dense grid, per
    mission section 11: a grid bar's funding_rate is only ever the most
    recent settlement AT OR BEFORE that bar (merge_asof direction=
    "backward" -- structurally cannot pick a future settlement), never a
    fabricated intermediate observation. funding_rate_percentile_90d is
    computed on the real settlement series (_settlement_percentile_rank)
    and forward-filled the same way -- see CROWDING percentile note in the
    module docstring."""
    out = pd.DataFrame(index=grid)
    fund = load_funding(symbol)
    if fund is None or fund.empty or "funding_rate" not in fund.columns:
        out["funding_rate"] = np.nan
        out["funding_rate_percentile_90d"] = np.nan
        out["funding_is_settlement"] = False
        out["time_since_last_funding"] = pd.NaT
        out["funding_research_available_at"] = pd.NaT
        return out

    f = fund[["timestamp", "funding_rate"]].dropna(subset=["funding_rate"]).copy()
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
    f = f.sort_values("timestamp").drop_duplicates(subset="timestamp", keep="last")
    if f.empty:
        out["funding_rate"] = np.nan
        out["funding_rate_percentile_90d"] = np.nan
        out["funding_is_settlement"] = False
        out["time_since_last_funding"] = pd.NaT
        out["funding_research_available_at"] = pd.NaT
        return out

    f["settlement_research_available_at"] = _research_available_at(
        f["timestamp"], bar_seconds=0, source_kind="binance_vision_daily", now=now
    )
    f["percentile_90d"] = _settlement_percentile_rank(
        pd.Series(f["funding_rate"].abs().to_numpy(), index=f["timestamp"])
    ).to_numpy()
    # floor ONLY for grid alignment -- floor never moves a timestamp
    # forward, so it cannot create a causality leak. A settlement posted at
    # 16:00:00.003 belongs to the 16:00:00 5m bar that had already started
    # when it posted (same precedent as data_v2/features/basis.py's
    # mark_price join).
    f["bar_ts"] = f["timestamp"].dt.floor(GRID_FREQ)
    f = f.rename(columns={"timestamp": "settlement_ts"}).sort_values("bar_ts")

    grid_df = pd.DataFrame({"timestamp": grid})
    merged = pd.merge_asof(grid_df, f, left_on="timestamp", right_on="bar_ts", direction="backward")
    merged.index = grid
    is_settlement = merged["timestamp"] == merged["bar_ts"]
    time_since = merged["timestamp"] - merged["settlement_ts"]
    # settlement jitter (a few ms past the canonical mark) can otherwise
    # make "time since" trivially negative at the exact settlement bar --
    # clip at zero for a metric that must never read as "in the future".
    time_since = time_since.clip(lower=pd.Timedelta(0))

    out["funding_rate"] = merged["funding_rate"].to_numpy()
    out["funding_rate_percentile_90d"] = merged["percentile_90d"].to_numpy()
    out["funding_is_settlement"] = is_settlement.to_numpy()
    out["time_since_last_funding"] = time_since.to_numpy()
    out["funding_research_available_at"] = merged["settlement_research_available_at"].to_numpy()
    return out


def build_symbol_panel(symbol: str, *, btc_close: pd.Series, eth_close: pd.Series, now: pd.Timestamp) -> Optional[pd.DataFrame]:
    perp = load_year_partitioned(PERP_DIR, symbol, "perp_5m.parquet")
    if perp is None or perp.empty:
        return None
    perp = perp[["timestamp", "open", "close", "volume"]].dropna(subset=["timestamp"]).copy()
    perp["timestamp"] = pd.to_datetime(perp["timestamp"], utc=True)
    perp = perp.sort_values("timestamp").drop_duplicates(subset="timestamp", keep="last").set_index("timestamp")
    if perp.empty:
        return None

    grid = _dense_grid(perp.index.min(), perp.index.max())
    perp_g = perp.reindex(grid)
    perp_ra = _research_available_at(
        pd.Series(grid, index=grid), bar_seconds=300, source_kind="binance_vision_monthly", now=now
    ).where(perp_g["close"].notna())

    basis = load_year_partitioned(BASIS_DIR, symbol, "basis_5m.parquet")
    if basis is not None and not basis.empty:
        basis = basis[["timestamp", "perp_spot_basis", "basis_z_1d", "basis_z_7d"]].copy()
        basis["timestamp"] = pd.to_datetime(basis["timestamp"], utc=True)
        basis = basis.drop_duplicates(subset="timestamp", keep="last").set_index("timestamp")
        basis_g = basis.reindex(grid).rename(columns={"perp_spot_basis": "basis"})
    else:
        basis_g = pd.DataFrame(index=grid, columns=["basis", "basis_z_1d", "basis_z_7d"], dtype="float64")
    basis_ra = _research_available_at(
        pd.Series(grid, index=grid), bar_seconds=300, source_kind="binance_vision_monthly", now=now
    ).where(basis_g["basis"].notna())

    oi = load_oi(symbol)
    if oi is not None and not oi.empty:
        oi = oi[["create_time", "sum_open_interest"]].rename(
            columns={"create_time": "timestamp", "sum_open_interest": "oi"}
        )
        oi["timestamp"] = pd.to_datetime(oi["timestamp"], utc=True)
        oi = oi.sort_values("timestamp").drop_duplicates(subset="timestamp", keep="last").set_index("timestamp")
        oi_g = oi.reindex(grid)
    else:
        oi_g = pd.DataFrame(index=grid, columns=["oi"], dtype="float64")
    # fill_method=None is required: pandas<2.2's pct_change() defaults to
    # fill_method="pad", silently forward-filling a real gap before
    # computing the ratio -- exactly the "unknown treated as known"
    # fabrication this pipeline exists to avoid. Post-reindex + no fill: a
    # gap correctly yields NaN, never a false baseline.
    oi_g["oi_delta_pct_1h"] = oi_g["oi"].pct_change(12, fill_method=None)
    oi_ra = _research_available_at(
        pd.Series(grid, index=grid), bar_seconds=300, source_kind="binance_vision_daily", now=now
    ).where(oi_g["oi"].notna())

    flow = load_year_partitioned(AGG_5M_DIR, symbol, "flow.parquet")
    if flow is not None and not flow.empty:
        flow = flow[["timestamp", "aggressive_buy_usd", "aggressive_sell_usd", "signed_volume", "CVD"]].copy()
        flow["timestamp"] = pd.to_datetime(flow["timestamp"], utc=True)
        flow = flow.drop_duplicates(subset="timestamp", keep="last").set_index("timestamp")
        flow_g = flow.reindex(grid)
    else:
        flow_g = pd.DataFrame(
            index=grid, columns=["aggressive_buy_usd", "aggressive_sell_usd", "signed_volume", "CVD"], dtype="float64"
        )
    flow_ra = _research_available_at(
        pd.Series(grid, index=grid), bar_seconds=300, source_kind="binance_vision_daily", now=now
    ).where(flow_g["signed_volume"].notna())

    funding_g = _build_funding_columns(grid, symbol, now)
    funding_ra = funding_g["funding_research_available_at"]

    close_reindexed = perp_g["close"]
    close_by_symbol = {BTC_SYMBOL: btc_close.reindex(grid), ETH_SYMBOL: eth_close.reindex(grid), symbol: close_reindexed}
    # min_periods=BETA_WINDOW_BARS (the FULL 60d window), not
    # compute_residual_returns' own default (BETA_MIN_PERIODS=20 bars,
    # explicitly documented in residuals.py as a test-only shortcut "small
    # enough to be testable on short synthetic panels"). Bug found
    # 2026-08-14: this panel had inherited that default, so
    # residual_return_1h/_15m started populating after ~100 minutes of
    # history instead of a full 60-day warmup -- directly contradicting
    # the mission's "warmup complet requis, jamais une valeur prématurée"
    # rule (section 12). residuals.py itself is untouched (its own tests
    # still get the fast default); only this production call site now
    # requires the real full window.
    residuals = compute_residual_returns(close_by_symbol, min_periods=BETA_WINDOW_BARS)[symbol]

    row_ra = pd.concat([perp_ra, basis_ra, oi_ra, flow_ra, funding_ra], axis=1).max(axis=1, skipna=True)

    panel = pd.DataFrame(index=grid)
    panel["timestamp"] = grid
    panel["research_available_at"] = row_ra.to_numpy()
    panel["symbol"] = symbol
    panel["open"] = perp_g["open"].to_numpy()
    panel["close"] = perp_g["close"].to_numpy()
    panel["volume"] = perp_g["volume"].to_numpy()
    panel["oi"] = oi_g["oi"].to_numpy()
    panel["oi_delta_pct_1h"] = oi_g["oi_delta_pct_1h"].to_numpy()
    panel["aggressive_buy_usd"] = flow_g["aggressive_buy_usd"].to_numpy()
    panel["aggressive_sell_usd"] = flow_g["aggressive_sell_usd"].to_numpy()
    panel["signed_volume"] = flow_g["signed_volume"].to_numpy()
    panel["CVD"] = flow_g["CVD"].to_numpy()
    panel["funding_rate"] = funding_g["funding_rate"].to_numpy()
    panel["funding_rate_percentile_90d"] = funding_g["funding_rate_percentile_90d"].to_numpy()
    panel["funding_is_settlement"] = funding_g["funding_is_settlement"].to_numpy()
    panel["time_since_last_funding"] = funding_g["time_since_last_funding"].to_numpy()
    panel["basis"] = basis_g["basis"].to_numpy()
    panel["basis_z_1d"] = basis_g["basis_z_1d"].to_numpy()
    panel["basis_z_7d"] = basis_g["basis_z_7d"].to_numpy()
    panel["residual_logret_5m"] = residuals["residual_logret_5m"].to_numpy()
    panel["residual_return_15m"] = residuals["residual_return_15m"].to_numpy()
    panel["residual_return_1h"] = residuals["residual_return_1h"].to_numpy()
    # liquidation feed not wired into this panel yet (separate raw stream,
    # outside the Data V2 P0 corpus this mission covers) -- False here
    # correctly means "unknown", the detectors' own liq_confirmed logic
    # (data_v2/events/detectors.py) already treats liq_feed_available=False
    # as "cannot tell a real quiet bar from a bar with no feed", never as
    # "0 liquidations".
    panel["liq_feed_available"] = False

    return panel.reset_index(drop=True)


def write_symbol_panel(symbol: str, *, btc_close: pd.Series, eth_close: pd.Series, now: pd.Timestamp) -> int:
    panel = build_symbol_panel(symbol, btc_close=btc_close, eth_close=eth_close, now=now)
    if panel is None or panel.empty:
        return 0
    total = 0
    for y, chunk in panel.groupby(panel["timestamp"].dt.year):
        out_path = OUT_DIR / f"symbol={symbol}" / f"year={y}" / "event_feature_panel_5m.parquet"
        atomic_write_parquet(chunk.reset_index(drop=True), out_path)
        total += len(chunk)
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None, help="comma-separated; default = full PIT universe")
    ap.add_argument("--min-free-gb", type=float, default=15.0)
    args = ap.parse_args()

    im = pd.read_parquet(INSTRUMENT_MASTER)
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else sorted(
        im.loc[im["symbol"].str.endswith("USDT"), "symbol"]
    )
    now = pd.Timestamp.now(tz="UTC")

    btc_close = load_perp_close(BTC_SYMBOL)
    eth_close = load_perp_close(ETH_SYMBOL)
    if btc_close is None or eth_close is None:
        print(f"FATAL: {BTC_SYMBOL}/{ETH_SYMBOL} perp close series required as regressors, one is missing.")
        sys.exit(1)

    built, skipped, stopped = 0, 0, False
    for i, symbol in enumerate(symbols, 1):
        headroom = free_gb(ROOT)
        if headroom < args.min_free_gb:
            print(f"\nSTOP: free space {headroom:.1f}GB < --min-free-gb {args.min_free_gb}GB "
                  f"after {i - 1}/{len(symbols)} symbols. Resumable -- re-run to continue.", flush=True)
            stopped = True
            break
        n = write_symbol_panel(symbol, btc_close=btc_close, eth_close=eth_close, now=now)
        if n:
            built += 1
            print(f"  [{i:3}/{len(symbols)}] {symbol:<14} rows={n:>8} free={headroom:.1f}GB", flush=True)
        else:
            skipped += 1

    print(f"\nevent feature panel built for {built} symbols, {skipped} skipped (no perp data) "
          f"-> {OUT_DIR}{' [STOPPED on disk floor]' if stopped else ''}", flush=True)
    if stopped:
        sys.exit(1)


if __name__ == "__main__":
    main()
