"""Reader for the reduced microstructure capture.

Layout on disk::

    <data_root>/microstructure_reduced/raw/bbo/venue=<v>/symbol=<s>/date=<d>/events-HH.jsonl.gz
    <data_root>/microstructure_reduced/raw/trades/venue=<v>/...

Each line carries ``event_ts_ns`` (exchange stamp) and ``receive_ts_ns`` (local
stamp), so latency is measured rather than assumed.

Quantities are **not** comparable across venues: Binance quotes base units,
OKX quotes contracts.  Anything depending on size must stay within one venue
until a contract multiplier table exists.  Mid prices are comparable.

Normalised snapshots are cached under ``data_lake/normalized/`` because parsing
a day of Binance top-of-book is about thirty million lines.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import pyarrow.json as pj

BPS = 1e4
BBO_FIELDS = [
    "event_ts_ns",
    "receive_ts_ns",
    "bid_price",
    "bid_qty",
    "ask_price",
    "ask_qty",
]
CACHE_ROOT = Path(__file__).resolve().parents[2] / "data_lake" / "normalized" / "microstructure"


def raw_dir(data_root: Path, kind: str, venue: str, symbol: str) -> Path:
    return (
        Path(data_root)
        / "microstructure_reduced"
        / "raw"
        / kind
        / ("venue=%s" % venue)
        / ("symbol=%s" % symbol)
    )


def available_dates(data_root: Path, kind: str, venue: str, symbol: str) -> List[str]:
    d = raw_dir(data_root, kind, venue, symbol)
    if not d.exists():
        return []
    return sorted(p.name.split("=", 1)[1] for p in d.iterdir() if p.name.startswith("date="))


def _read_hour(path: Path) -> Optional[pd.DataFrame]:
    try:
        with gzip.open(str(path), "rb") as fh:
            table = pj.read_json(fh, read_options=pj.ReadOptions(block_size=1 << 26))
    except Exception:
        return None
    cols = [c for c in BBO_FIELDS if c in table.column_names]
    if len(cols) < len(BBO_FIELDS):
        return None
    return table.select(cols).to_pandas()


def load_bbo_grid(
    data_root: Path,
    venue: str,
    symbol: str,
    dates: Sequence[str],
    grid_ms: int = 1000,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Last quote of every ``grid_ms`` bucket, one row per bucket.

    Returns ``timestamp, venue, symbol, bid, ask, bid_qty, ask_qty, mid,
    spread_bps, latency_ms``.  Buckets with no quote are absent, not
    forward-filled: a gap is a fact about the capture.
    """
    frames: List[pd.DataFrame] = []
    for date in dates:
        cache = CACHE_ROOT / ("bbo_%s_%s_%s_%dms.parquet" % (venue, symbol, date, grid_ms))
        if use_cache and cache.exists():
            frames.append(pd.read_parquet(cache))
            continue

        d = raw_dir(data_root, "bbo", venue, symbol) / ("date=%s" % date)
        if not d.exists():
            continue
        hours: List[pd.DataFrame] = []
        for f in sorted(d.glob("events-*.jsonl.gz")):
            df = _read_hour(f)
            if df is None or df.empty:
                continue
            bucket_ns = int(grid_ms) * 1_000_000
            df["bucket"] = (df["event_ts_ns"] // bucket_ns) * bucket_ns
            df = df.sort_values("event_ts_ns").groupby("bucket", as_index=False).last()
            hours.append(df)
        if not hours:
            continue
        day = pd.concat(hours, ignore_index=True)
        day = day.sort_values("bucket").drop_duplicates("bucket", keep="last")
        day["timestamp"] = pd.to_datetime(day["bucket"], unit="ns", utc=True)
        day["latency_ms"] = (day["receive_ts_ns"] - day["event_ts_ns"]) / 1e6
        day["mid"] = 0.5 * (day["bid_price"] + day["ask_price"])
        day["spread_bps"] = (day["ask_price"] - day["bid_price"]) / day["mid"] * BPS
        day["venue"] = venue
        day["symbol"] = symbol
        day = day.rename(columns={"bid_price": "bid", "ask_price": "ask"})
        day = day[
            [
                "timestamp",
                "venue",
                "symbol",
                "bid",
                "ask",
                "bid_qty",
                "ask_qty",
                "mid",
                "spread_bps",
                "latency_ms",
            ]
        ].reset_index(drop=True)
        if use_cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            day.to_parquet(cache, index=False)
        frames.append(day)

    if not frames:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "venue",
                "symbol",
                "bid",
                "ask",
                "bid_qty",
                "ask_qty",
                "mid",
                "spread_bps",
                "latency_ms",
            ]
        )
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values("timestamp").reset_index(drop=True)


def rolling_z(
    series: pd.Series,
    window,
    min_periods: int = 60,
    timestamps: Optional[pd.Series] = None,
) -> pd.Series:
    """Trailing z-score, strictly causal: the window excludes the current point.

    ``window`` may be a row count or a pandas offset string such as ``"300s"``.
    Prefer the offset form: the capture has gaps, so three hundred rows is not
    three hundred seconds.
    """
    shifted = series.shift(1)
    if isinstance(window, str):
        if timestamps is None:
            raise ValueError("a time-based window needs the timestamps")
        idx = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))
        shifted = pd.Series(shifted.to_numpy(), index=idx)
        mu = shifted.rolling(window, min_periods=min_periods).mean()
        sd = shifted.rolling(window, min_periods=min_periods).std(ddof=1)
        mu = pd.Series(mu.to_numpy(), index=series.index)
        sd = pd.Series(sd.to_numpy(), index=series.index)
    else:
        mu = shifted.rolling(window, min_periods=min_periods).mean()
        sd = shifted.rolling(window, min_periods=min_periods).std(ddof=1)
    return (series - mu) / sd.replace(0.0, np.nan)


def forward_value(
    timestamps: pd.Series,
    values: pd.Series,
    seconds: float,
    tolerance_seconds: float = 5.0,
) -> pd.Series:
    """Value of ``values`` about ``seconds`` later, or NaN when the capture has
    no quote within ``tolerance_seconds`` of that instant."""
    # tz-aware timestamps come back as object arrays, so work in nanoseconds
    ts = pd.to_datetime(timestamps, utc=True).astype("int64").to_numpy()
    target = ts + int(seconds * 1_000_000_000)
    idx = np.searchsorted(ts, target, side="left")
    out = np.full(len(ts), np.nan)
    valid = idx < len(ts)
    got = idx[valid]
    gap = (ts[got] - target[valid]) / 1e9
    ok = np.abs(gap) <= tolerance_seconds
    sel = np.where(valid)[0][ok]
    out[sel] = values.to_numpy()[got[ok]]
    return pd.Series(out, index=values.index)
