#!/usr/bin/env python3
"""Build a historical A8 (leverage topology) panel from data/derivatives_raw's
Binance open_interest stream (price/mark/index/OI/funding, ~5min native cadence,
2026-06-28 onward, 47 symbols) plus Bybit+OKX force_order liquidations.

Reuses alpha_foundry_v5.data_planes.derivatives.DerivativesPlaneState (the same
feature accumulator build_derivatives_plane uses for tick-level windows) and
ChunkedPlaneWriter (the same output format every other Alpha Foundry V5 tensor
uses) -- this script only supplies the driver: convert the raw historical
snapshot files into DerivativeEvent-shaped dicts, feed them chronologically, and
emit a row at each Binance OI snapshot's own timestamp (the native ~5min grid,
not a separately resampled one -- see the note in the module docstring below on
what that approximates).

One methodological approximation, stated plainly: rows are emitted at each raw
OI snapshot's actual timestamp, not a strictly regular grid. Native spacing is
irregular around a ~5min median, so a "horizon_ms=3600000" (1h) request is
approximately, not exactly, 1h of elapsed time on any given row. This matters
less than it would for the ms-scale windows elsewhere in this codebase, but it
is not nothing -- documented here rather than silently assumed away.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.data_planes.common import ChunkedPlaneWriter
from alpha_foundry_v5.data_planes.derivatives import DerivativesPlaneState

DEFAULT_RAW_ROOT = "/home/qbee/futur/data/derivatives_raw"


def _load_binance_oi_events(raw_root: str, symbols: list[str]) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        files = list(Path(raw_root, "exchange=binance/market=usdm/stream=open_interest", f"symbol={symbol}").glob("date=*/*.parquet"))
        if not files:
            continue
        d = ds.dataset([str(f) for f in files], format="parquet")
        df = d.to_table().to_pandas()
        df["symbol"] = symbol
        frames.append(df)
    if not frames:
        raise ValueError("no Binance open_interest data found for any requested symbol")
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("recv_time", kind="mergesort").reset_index(drop=True)
    return out


def _load_liquidations(raw_root: str, venue: str, market_dir: str, symbols: list[str]) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        files = list(Path(raw_root, f"exchange={venue}/market={market_dir}/stream=force_order", f"symbol={symbol}").glob("date=*/*.parquet"))
        if not files:
            continue
        d = ds.dataset([str(f) for f in files], format="parquet")
        df = d.to_table().to_pandas()
        df["symbol"] = symbol
        df["venue"] = venue
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["recv_time", "symbol", "venue", "side_raw", "usd"])
    return pd.concat(frames, ignore_index=True)


def _binance_oi_row_to_events(row) -> list[dict]:
    recv_ns = int(row["recv_time"]) * 1_000_000
    out = []
    for kind, col in (("open_interest", "open_interest"), ("mark", "mark_price"), ("index", "index_price"), ("funding", "funding_rate")):
        value = row[col]
        if value is None or (isinstance(value, float) and not np.isfinite(value)):
            continue
        out.append({"venue": "binance", "symbol": row["symbol"], "kind": kind, "value": float(value), "receive_ts_ns": recv_ns})
    return out


def _liquidation_row_to_event(row) -> dict:
    recv_ns = int(row["recv_time"]) * 1_000_000
    return {
        "venue": row["venue"],
        "symbol": row["symbol"],
        "kind": "liquidation",
        "value": float(row["usd"]),
        # side_raw convention ('sell'/'buy') matches DerivativesPlaneState's
        # existing expectation (sell=long liquidated, buy=short liquidated) --
        # same mapping market_physics_v3/collectors/normalize.py's Binance
        # forceOrder parser uses. Not independently re-derived here.
        "side": str(row["side_raw"]).lower(),
        "receive_ts_ns": recv_ns,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", default=DEFAULT_RAW_ROOT)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--chunk-rows", type=int, default=50000)
    args = ap.parse_args()

    all_symbols = sorted(p.name.split("=", 1)[1] for p in Path(args.raw_root, "exchange=binance/market=usdm/stream=open_interest").glob("symbol=*"))
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or all_symbols
    print(f"[a8-panel] symbols={len(symbols)}", flush=True)

    oi = _load_binance_oi_events(args.raw_root, symbols)
    print(f"[a8-panel] loaded binance open_interest rows={len(oi)}", flush=True)
    bybit_liq = _load_liquidations(args.raw_root, "bybit", "linear", symbols)
    okx_liq = _load_liquidations(args.raw_root, "okx", "swap", symbols)
    liq = pd.concat([bybit_liq, okx_liq], ignore_index=True)
    if len(liq):
        liq = liq.sort_values("recv_time", kind="mergesort").reset_index(drop=True)
    print(f"[a8-panel] loaded liquidation rows bybit={len(bybit_liq)} okx={len(okx_liq)}", flush=True)

    state = DerivativesPlaneState(venues=["binance", "bybit", "okx"], symbols=symbols)
    writer = ChunkedPlaneWriter(args.out, chunk_rows=args.chunk_rows)

    oi_iter = oi.itertuples(index=False)
    liq_iter = liq.itertuples(index=False)
    oi_row = next(oi_iter, None)
    liq_row = next(liq_iter, None)

    def _recv(row) -> int:
        return int(row.recv_time)

    n_out = 0
    while oi_row is not None:
        # Drain every liquidation event that happened at or before this OI
        # snapshot's receive time, so the emitted row reflects everything
        # known as of that instant -- same "ingest strictly before observing"
        # discipline build_derivatives_plane's tick-aligned loop uses.
        while liq_row is not None and _recv(liq_row) <= _recv(oi_row):
            state.ingest(_liquidation_row_to_event(liq_row._asdict()))
            liq_row = next(liq_iter, None)
        for event in _binance_oi_row_to_events(oi_row._asdict()):
            state.ingest(event)
        recv_ns = int(oi_row.recv_time) * 1_000_000
        out_row = state.row(recv_ns, oi_row.symbol)
        out_row["price_fair_value"] = out_row.get("binance__index")
        writer.append(out_row)
        n_out += 1
        if n_out % 100000 == 0:
            print(f"[a8-panel] emitted rows={n_out}", flush=True)
        oi_row = next(oi_iter, None)

    start_ns = int(oi["recv_time"].min()) * 1_000_000
    stop_ns = int(oi["recv_time"].max()) * 1_000_000
    summary = writer.close({
        "plane": "a8_leverage_panel",
        "start_ns": start_ns,
        "stop_ns": stop_ns,
        "symbols": symbols,
        "venues": ["binance", "bybit", "okx"],
        "native_cadence_note": "irregular, ~5min median (Binance OI snapshot arrival), not a strict grid",
        "source": args.raw_root,
    })
    print(json.dumps(summary, indent=2)[:2000], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
