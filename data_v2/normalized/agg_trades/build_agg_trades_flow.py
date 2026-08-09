#!/usr/bin/env python3
"""
data_v2/normalized/agg_trades/build_agg_trades_flow.py
─────────────────────────────────────────────────────────────────────────────
Data V2 steps 7+8: real aggressor-side flow from Binance Vision Futures UM
daily aggTrades archives -- the single most important data gap after OI per
the plan (nothing on disk anywhere before this, per research/edge_factory/
liquidation_relative_reversal_v1/DATA_INVENTORY.yaml). Never derives buy/
sell from OHLCV; uses the real `is_buyer_maker` field from the source
schema directly (agg_trade_id,price,quantity,first_trade_id,last_trade_id,
transact_time,is_buyer_maker -- verified against real 2021 and 2026 daily
files, both the pre-2025 headerless and 2025+ headered CSV formats).

Aggressor convention (Binance): is_buyer_maker=True means the BUYER posted
the resting order, so the SELLER crossed the spread -- an aggressive SELL.
is_buyer_maker=False -> the BUYER crossed the spread -- an aggressive BUY.
Step 8 gate: aggressive_buy_usd + aggressive_sell_usd must equal total
traded USD for the bar (every trade is classified, none dropped, no
artificial 50/50 split) -- enforced by build_agg_trades_flow_test.py.

Per-bar columns (1m and 5m, computed independently from raw trades -- p95/
vwap are not re-derivable from already-aggregated 1m rows by summing):
  aggressive_buy_usd, aggressive_sell_usd, signed_volume (buy-sell, USD),
  CVD (running cumulative signed_volume, carried across days via manifest
  state -- see _CVD_STATE_KEY), CVD_delta (= signed_volume, kept as an
  explicit alias per the plan's column list), trade_count,
  large_trade_buy_usd/sell_usd (trades >= LARGE_TRADE_USD, a fixed
  threshold -- a design choice, not derived from data, see LARGE_TRADE_USD),
  avg_trade_size_usd, p95_trade_size_usd, buy_vwap, sell_vwap.

Aggregate-only per the 2026-08-09 disk-budget decision: each day's raw zip
is fetched into memory and discarded after aggregation -- nothing raw is
kept on disk. Vision daily archives are public/permanent and can be
re-fetched later if raw ticks are ever needed. This is explicitly the
heaviest step in the whole plan (CPU + network); expect a multi-hour to
multi-day background run for the full 312-symbol history.

Usage:
    /home/qbee/futur/.venv/bin/python3 \\
        data_v2/normalized/agg_trades/build_agg_trades_flow.py --min-free-gb 15
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_1M = ROOT / "data_v2/normalized/agg_trades_flow/1m/venue=binance"
OUT_5M = ROOT / "data_v2/normalized/agg_trades_flow/5m/venue=binance"
BASE_URL = "https://data.binance.vision/data/futures/um/daily/aggTrades"

AGG_TRADES_COLUMNS = [
    "agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
    "transact_time", "is_buyer_maker",
]
LARGE_TRADE_USD = 10_000.0  # fixed design threshold, not empirically derived


def fetch_day(symbol: str, d: date, retries: int = 3) -> pd.DataFrame | None:
    url = f"{BASE_URL}/{symbol}/{symbol}-aggTrades-{d.isoformat()}.zip"
    raw = None
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last_err = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # transient (DNS blip, read timeout, connection reset) -- one
            # bad day used to kill the WHOLE symbol for this run (4/5
            # symbols failed outright to a few-second network hiccup on
            # 2026-08-09); retry with backoff before giving up on this day.
            last_err = e
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    if raw is None:
        raise last_err

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = [n for n in zf.namelist() if n.endswith(".csv")][0]
        with zf.open(name) as fh:
            first_line = fh.readline()
    has_header = not first_line.split(b",")[0].strip().isdigit()

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        with zf.open(name) as fh:
            if has_header:
                df = pd.read_csv(fh, low_memory=False)
                df.columns = [c.lower() for c in df.columns]
            else:
                df = pd.read_csv(fh, header=None, names=AGG_TRADES_COLUMNS, low_memory=False)
    if df.empty:
        return None

    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["is_buyer_maker"] = df["is_buyer_maker"].astype(str).str.lower().isin(["true", "1"])
    unit = "us" if len(str(int(df["transact_time"].iloc[0]))) >= 16 else "ms"
    df["timestamp"] = pd.to_datetime(df["transact_time"].astype("int64"), unit=unit, utc=True)
    df["usd"] = df["price"] * df["quantity"]
    # is_buyer_maker=True -> seller is the aggressor (aggressive sell)
    df["aggressive_buy_usd"] = np.where(~df["is_buyer_maker"], df["usd"], 0.0)
    df["aggressive_sell_usd"] = np.where(df["is_buyer_maker"], df["usd"], 0.0)
    df["aggressive_buy_qty"] = np.where(~df["is_buyer_maker"], df["quantity"], 0.0)
    df["aggressive_sell_qty"] = np.where(df["is_buyer_maker"], df["quantity"], 0.0)
    return df.set_index("timestamp").sort_index()


def _p95(s: pd.Series) -> float:
    return float(np.percentile(s, 95)) if len(s) else np.nan


def aggregate_bars(trades: pd.DataFrame, freq: str) -> pd.DataFrame:
    g = trades.groupby(pd.Grouper(freq=freq))
    out = pd.DataFrame({
        "aggressive_buy_usd": g["aggressive_buy_usd"].sum(),
        "aggressive_sell_usd": g["aggressive_sell_usd"].sum(),
        "trade_count": g.size(),
        "avg_trade_size_usd": g["usd"].mean(),
        "p95_trade_size_usd": g["usd"].apply(_p95),
    })
    large = trades[trades["usd"] >= LARGE_TRADE_USD]
    if len(large):
        gl = large.groupby(pd.Grouper(freq=freq))
        out["large_trade_buy_usd"] = gl["aggressive_buy_usd"].sum()
        out["large_trade_sell_usd"] = gl["aggressive_sell_usd"].sum()
    else:
        out["large_trade_buy_usd"] = 0.0
        out["large_trade_sell_usd"] = 0.0
    out[["large_trade_buy_usd", "large_trade_sell_usd"]] = out[["large_trade_buy_usd", "large_trade_sell_usd"]].fillna(0.0)

    buy_qty = g["aggressive_buy_qty"].sum()
    sell_qty = g["aggressive_sell_qty"].sum()
    out["buy_vwap"] = (out["aggressive_buy_usd"] / buy_qty.replace(0, np.nan))
    out["sell_vwap"] = (out["aggressive_sell_usd"] / sell_qty.replace(0, np.nan))

    out["signed_volume"] = out["aggressive_buy_usd"] - out["aggressive_sell_usd"]
    out["CVD_delta"] = out["signed_volume"]
    out = out.dropna(how="all")
    return out


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def rss_gb() -> float:
    """Current resident memory of this process, from /proc -- a live guard
    against the exact OOM-kill this script already caused once (see
    build_symbol docstring): swap on this host was already fully used
    (2.0/2.0GB) independent of this job, so headroom is tighter than the
    31GB total RAM suggests."""
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / (1024 ** 2)
    return 0.0


def load_manifest(symbol_dir: Path) -> dict:
    mf = symbol_dir / "manifest.json"
    return json.loads(mf.read_text()) if mf.exists() else {
        "done_days": [], "missing_days": [], "last_cvd_1m": 0.0, "last_cvd_5m": 0.0,
    }


def save_manifest(symbol_dir: Path, manifest: dict) -> None:
    (symbol_dir / "manifest.json").write_text(json.dumps(manifest))


def _write_year_partitions(out_root: Path, symbol: str, frame_by_day: dict[date, pd.DataFrame], last_cvd: float) -> tuple[int, float]:
    if not frame_by_day:
        return 0, last_cvd
    combined = pd.concat(frame_by_day.values()).sort_index()
    combined["CVD"] = last_cvd + combined["signed_volume"].cumsum()
    new_last_cvd = float(combined["CVD"].iloc[-1])

    total_rows = 0
    for y, chunk in combined.groupby(combined.index.year):
        year_dir = out_root / f"symbol={symbol}" / f"year={y}"
        year_dir.mkdir(parents=True, exist_ok=True)
        out_path = year_dir / "flow.parquet"
        new = chunk.copy()
        if out_path.exists():
            old = pd.read_parquet(out_path).set_index("timestamp")
            new = pd.concat([old, new]).sort_index()
            new = new[~new.index.duplicated(keep="last")]
        tmp = out_path.with_suffix(".tmp.parquet")
        new.reset_index().rename(columns={"index": "timestamp"}).to_parquet(tmp, index=False)
        tmp.replace(out_path)
        total_rows += len(new)
    return total_rows, new_last_cvd


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def build_symbol(symbol: str, start: date, end: date, workers: int = 6, batch_days: int = 10,
                  max_rss_gb: float = 8.0) -> dict:
    """Fetches+aggregates in bounded day-batches (batch_days), never the
    whole symbol's history at once. A first version submitted every
    outstanding day to the ThreadPoolExecutor in one shot -- for a
    high-history symbol that's 1000+ raw per-day trade DataFrames in
    flight (downloads run faster than the single-threaded aggregation
    consumer could drain them), and it OOM-killed the process at ~29GB RSS
    on a 31GB host after finishing only the first (tiny) symbol. Batching
    caps how many raw days can be buffered at once; each batch is
    aggregated and flushed to disk (with CVD state carried in the
    manifest) before the next batch is fetched."""
    manifest_dir_1m = OUT_1M / f"symbol={symbol}"
    manifest_dir_1m.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(manifest_dir_1m)
    done = set(manifest["done_days"]) | set(manifest["missing_days"])

    days = []
    d = start
    while d <= end:
        if d.isoformat() not in done:
            days.append(d)
        d += timedelta(days=1)
    if not days:
        return {"symbol": symbol, "new_days": 0, "missing_days": 0, "rows_1m": 0, "rows_5m": 0}

    n_new, n_missing, n_gate_fail = 0, 0, 0
    rows_1m_total, rows_5m_total = 0, 0
    last_cvd_1m = manifest.get("last_cvd_1m", 0.0)
    last_cvd_5m = manifest.get("last_cvd_5m", 0.0)

    for batch in _chunked(days, batch_days * workers):
        mem = rss_gb()
        if mem > max_rss_gb:
            print(f"    [{symbol}] STOP mid-symbol: RSS {mem:.1f}GB > max_rss_gb {max_rss_gb}GB "
                  f"after {n_new} days this call. Checkpointed -- re-run to continue.", flush=True)
            break
        bars_1m_by_day: dict[date, pd.DataFrame] = {}
        bars_5m_by_day: dict[date, pd.DataFrame] = {}

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fetch_day, symbol, d): d for d in batch}
            for fut in as_completed(futs):
                d = futs[fut]
                trades = fut.result()
                if trades is None or trades.empty:
                    manifest["missing_days"].append(d.isoformat())
                    n_missing += 1
                    continue
                b1 = aggregate_bars(trades, "1min")
                b5 = aggregate_bars(trades, "5min")
                # step 8 gate: every trade classified, buy+sell == total, no fabrication
                total_usd = trades["usd"].sum()
                classified = b1["aggressive_buy_usd"].sum() + b1["aggressive_sell_usd"].sum()
                if not np.isclose(total_usd, classified, rtol=1e-6):
                    n_gate_fail += 1
                bars_1m_by_day[d] = b1
                bars_5m_by_day[d] = b5
                manifest["done_days"].append(d.isoformat())
                n_new += 1
                del trades

        r1, last_cvd_1m = _write_year_partitions(OUT_1M, symbol, bars_1m_by_day, last_cvd_1m)
        r5, last_cvd_5m = _write_year_partitions(OUT_5M, symbol, bars_5m_by_day, last_cvd_5m)
        rows_1m_total += r1
        rows_5m_total += r5
        manifest["last_cvd_1m"] = last_cvd_1m
        manifest["last_cvd_5m"] = last_cvd_5m
        manifest["done_days"] = sorted(set(manifest["done_days"]))
        manifest["missing_days"] = sorted(set(manifest["missing_days"]))
        save_manifest(manifest_dir_1m, manifest)  # checkpoint after every batch, not just at symbol end
        del bars_1m_by_day, bars_5m_by_day

    return {
        "symbol": symbol, "new_days": n_new, "missing_days": n_missing,
        "rows_1m": rows_1m_total, "rows_5m": rows_5m_total, "gate_fail_days": n_gate_fail,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-free-gb", type=float, default=15.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--batch-days", type=int, default=3)
    ap.add_argument("--max-rss-gb", type=float, default=6.0)
    ap.add_argument("--symbols", default=None)
    args = ap.parse_args()

    im = pd.read_parquet(INSTRUMENT_MASTER)
    im = im[im["symbol"].str.endswith("USDT")].copy()
    today = date.today() - timedelta(days=2)
    if args.symbols:
        wanted = set(s.strip() for s in args.symbols.split(","))
        im = im[im["symbol"].isin(wanted)]

    print(f"Building aggTrades flow (1m+5m): {len(im)} symbols, min_free_gb={args.min_free_gb}, "
          f"workers={args.workers}, batch_days={args.batch_days}, max_rss_gb={args.max_rss_gb}", flush=True)
    t0 = time.time()
    for i, row in enumerate(im.sort_values("symbol").itertuples(), 1):
        headroom = free_gb(ROOT)
        if headroom < args.min_free_gb:
            print(f"\nSTOP: free space {headroom:.1f}GB < --min-free-gb {args.min_free_gb}GB "
                  f"after {i - 1}/{len(im)} symbols. Resumable -- re-run to continue.", flush=True)
            sys.exit(1)
        mem = rss_gb()
        if mem > args.max_rss_gb:
            print(f"\nSTOP: RSS {mem:.1f}GB > --max-rss-gb {args.max_rss_gb}GB after {i - 1}/{len(im)} "
                  f"symbols (this script OOM-killed the host once already -- see build_symbol docstring). "
                  f"Resumable -- re-run to continue.", flush=True)
            sys.exit(1)
        start = pd.Timestamp(row.listing_ts).date() if pd.notna(row.listing_ts) else date(2019, 9, 1)
        end = pd.Timestamp(row.delisting_ts).date() if pd.notna(row.delisting_ts) else today
        try:
            r = build_symbol(row.symbol, start, min(end, today), workers=args.workers,
                              batch_days=args.batch_days, max_rss_gb=args.max_rss_gb)
        except Exception as e:
            print(f"  [{i:3}/{len(im)}] {row.symbol:14} ERROR {type(e).__name__}: {e}", flush=True)
            continue
        gate_note = f" GATE_FAIL={r['gate_fail_days']}" if r.get("gate_fail_days") else ""
        print(f"  [{i:3}/{len(im)}] {row.symbol:14} new_days={r['new_days']:4} "
              f"missing={r['missing_days']:4} rows_1m={r['rows_1m']:7} rows_5m={r['rows_5m']:6} "
              f"free={headroom:.1f}GB{gate_note}", flush=True)

    print(f"\nDone in {time.time() - t0:.0f}s -> {OUT_1M} / {OUT_5M}", flush=True)


if __name__ == "__main__":
    main()
