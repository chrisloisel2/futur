#!/usr/bin/env python3
"""
scripts/backfill_binance_derivatives_free.py
─────────────────────────────────────────────────────────────────────────────
Backfill GRATUIT Binance Futures (APIs publiques documentées, hashable, repro).

  fundingRate    : historique MULTI-AN paginé (le gem gratuit) — tous actifs
  openInterestHist : DERNIER MOIS seulement (limite Binance documentée)
  takerlongshortRatio / globalLongShortAccountRatio : dernier mois

Honnêteté : pas de liquidations historiques ici (indisponibles gratuitement).
Sortie consolidée par actif : data/derivatives_backfill/binance/<stream>/<SYM>.parquet
(écriture atomique) + registry. Rien d'inventé ; seulement ce que l'API rend.

This is the CANONICAL writer for data/derivatives_backfill/binance/funding/
{symbol}.parquet -- the exact path reports/DATA_V2_READINESS.json's
"funding" dataset reads (verified by repo inspection 2026-08-11 before
touching anything; scripts/collect_funding_rate_binance.py looks similar
but writes to a DIFFERENT path, data/raw/binance_funding_rate/ -- not this
store, do not use it for a P0 funding top-up).

Fix (2026-08-11): backfill_funding() used to ALWAYS refetch the full
--start..now range and overwrite the file outright each run -- safe today
only because Binance's fundingRate endpoint still happens to serve the
full multi-year history (verified live), but fragile: any future
retention change on Binance's side would silently truncate the on-disk
store on the next run, and every run re-fetches years of already-known
data. Now genuinely incremental: reads the existing parquet (if any),
fetches only from its last known timestamp forward, merges+dedupes+sorts,
atomic-writes the union -- existing history is never lost even if the API
ever serves a shorter window later. Default --symbols is now the full PIT
universe (instrument_master.parquet) instead of a hardcoded 9-symbol
list -- the on-disk store already covers ~312 symbols; the old default
would have silently left all but 9 of them stale on every future run.

    python3 scripts/backfill_binance_derivatives_free.py --start 2021-01-01
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.data.atomic_parquet import atomic_write_parquet

B = "https://fapi.binance.com"
OUT = ROOT / "data" / "derivatives_backfill" / "binance"
REG = ROOT / "artifacts" / "data_registry" / "derivatives_backfill_store.yaml"
INSTRUMENT_MASTER = ROOT / "data_v2" / "instruments" / "instrument_master.parquet"
CORE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"]


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def default_symbols() -> list[str]:
    """Full PIT universe when instrument_master exists (the real target --
    the on-disk funding store already covers ~312 symbols; refreshing only
    CORE_SYMBOLS would silently leave the rest stale), else the small core
    list as an environment-independent fallback."""
    if INSTRUMENT_MASTER.exists():
        im = pd.read_parquet(INSTRUMENT_MASTER, columns=["symbol"])
        return sorted(im["symbol"].unique().tolist())
    return CORE_SYMBOLS


def load_delisting_map() -> dict:
    """symbol -> proven delisting_ts, for symbols instrument_master has
    confirmed ABSENT from live exchangeInfo. Used to cap the funding
    top-up: see the 2026-08-11 fake-post-delisting-feed fix below."""
    if not INSTRUMENT_MASTER.exists():
        return {}
    im = pd.read_parquet(INSTRUMENT_MASTER, columns=["symbol", "delisting_ts"])
    im = im[im["delisting_ts"].notna()]
    return dict(zip(im["symbol"], pd.to_datetime(im["delisting_ts"], utc=True)))


def merge_funding(existing: Optional[pd.DataFrame], new: pd.DataFrame) -> pd.DataFrame:
    """Union of existing on-disk rows and newly-fetched rows, deduplicated
    by timestamp (keep the newer fetch's value on a genuine clash) and
    sorted -- never drops a row that was on disk before this run."""
    if existing is None or existing.empty:
        combined = new
    elif new.empty:
        combined = existing
    else:
        combined = pd.concat([existing, new], ignore_index=True)
    if combined.empty:
        return combined
    return (
        combined.drop_duplicates(subset="timestamp", keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def _get(url: str, tries: int = 3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)


def backfill_funding(sym: str, start_ms: int, end_ms: Optional[int] = None) -> pd.DataFrame:
    """Paginates forward from start_ms. `end_ms`, when given, stops once
    the cursor reaches it -- used for a bounded BACKWARD gap-fill (see
    top_up_funding) so re-discovering an early gap doesn't re-fetch the
    entire already-known history in between just to discard it."""
    rows, cursor = [], start_ms
    now = int(time.time() * 1000)
    stop_at = min(end_ms, now) if end_ms is not None else now
    while cursor < stop_at:
        data = _get(f"{B}/fapi/v1/fundingRate?symbol={sym}&startTime={cursor}&limit=1000")
        if not data:
            break
        rows.extend(data)
        last = data[-1]["fundingTime"]
        if last <= cursor:
            break
        cursor = last + 1
        time.sleep(0.25)
        if len(data) < 1000 and last > stop_at - 8 * 3600 * 1000:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates("fundingTime")
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["mark_price"] = pd.to_numeric(df.get("markPrice", ""), errors="coerce")
    df = df.dropna(subset=["funding_rate"])
    return df[["timestamp", "funding_rate", "mark_price"]].sort_values("timestamp").reset_index(drop=True)


def backfill_oi_hist(sym: str) -> pd.DataFrame:
    data = _get(f"{B}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=500")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["open_interest"] = df["sumOpenInterest"].astype(float)
    df["open_interest_usd"] = df["sumOpenInterestValue"].astype(float)
    return df[["timestamp", "open_interest", "open_interest_usd"]].sort_values("timestamp").reset_index(drop=True)


def symbol_start_ms(symbol: str, im: Optional[pd.DataFrame], fallback_ms: int) -> int:
    """Each symbol's own real listing bound (first_perp_kline_ts), not a
    single global --start applied uniformly -- see top_up_funding's
    2026-08-14 fix note. min() with fallback_ms: never later than the CLI
    default, but goes earlier whenever a symbol's own real bound proves an
    earlier existence. Falls back to fallback_ms unchanged when
    instrument_master or the field itself is unavailable for this symbol
    -- fail-safe, not fail-closed (a backfill floor, not a readiness
    gate)."""
    if im is None:
        return fallback_ms
    row = im.loc[im["symbol"] == symbol]
    if row.empty or pd.isna(row.iloc[0].get("first_perp_kline_ts")):
        return fallback_ms
    ts = pd.Timestamp(row.iloc[0]["first_perp_kline_ts"])
    return min(fallback_ms, int(ts.value // 1_000_000))


def top_up_funding(
    sym: str, start_ms: int, delisting_ts: Optional[pd.Timestamp] = None
) -> pd.DataFrame:
    """Genuinely bidirectional: extends the existing on-disk parquet (if
    any) BOTH forward from its last known timestamp AND backward from its
    first known timestamp down to `start_ms`, merges, returns the union
    sorted and deduplicated. Never re-fetching (or losing) history already
    on disk in the overlap.

    Bug found + fixed 2026-08-14: `start_ms` used to be a single global
    default (2021-01-01) applied to every symbol regardless of its real
    listing date, and was ONLY ever used as the floor for a symbol with no
    existing file at all -- an existing file's own first row was never
    revisited, so an early gap from a too-late historical start_ms could
    never self-heal on a later run even after start_ms was corrected.
    Confirmed via a direct Binance API query: AAVEUSDT has real funding
    settlements from 2020-10-16 (its real first_perp_kline_ts), 77/312
    symbols listed before 2021-01-01 were affected the same way. Callers
    now pass each symbol's own real listing bound as start_ms (see
    main()'s per-symbol start resolution) and this function explicitly
    backfills the gap between start_ms and the existing file's own first
    row, in addition to the existing forward top-up -- bounded (end_ms) so
    filling a small early gap doesn't re-fetch the entire already-known
    history in between just to discard it.

    Bug found + fixed 2026-08-11: Binance's /fapi/v1/fundingRate endpoint
    does NOT stop or 404 once a perp contract is delisted -- it keeps
    emitting a frozen placeholder feed (constant funding_rate=0.0001,
    near-static markPrice) indefinitely, with fresh-looking timestamps
    extending right up to "now" on every call. A pre-fix run of this
    top-up blindly appended that phantom feed for EOSUSDT/MATICUSDT/
    SXPUSDT (61 fake rows each, past their proven delisting_ts) --
    exactly the "no event after proven delisting" fake-fill this store
    must never contain. `delisting_ts`, when instrument_master has
    confirmed it (symbol ABSENT from live exchangeInfo), is now a hard
    upper bound: any existing on-disk row past it is stripped before
    every run (self-healing -- no separate one-off cleanup script or
    hardcoded symbol list needed) and no fetch is issued past it. A
    symbol delisted after the last instrument_master rebuild won't be
    capped until the next rebuild -- a narrow, self-correcting window,
    not a silent gap."""
    path = OUT / "funding" / f"{sym}.parquet"
    existing = pd.read_parquet(path) if path.exists() else None
    if existing is not None and not existing.empty and delisting_ts is not None:
        existing = existing[pd.to_datetime(existing["timestamp"], utc=True) <= delisting_ts]

    new_frames = []

    # backward: fill any gap between start_ms and the existing file's own
    # first row (an earlier run may have used a too-late start_ms).
    if existing is not None and not existing.empty:
        first_ts = pd.to_datetime(existing["timestamp"], utc=True).min()
        first_ms = int(first_ts.value // 1_000_000)
        if start_ms < first_ms:
            backward = backfill_funding(sym, start_ms, end_ms=first_ms)
            if not backward.empty:
                backward = backward[pd.to_datetime(backward["timestamp"], utc=True) < first_ts]
                new_frames.append(backward)

    fetch_from_ms = start_ms
    if existing is not None and not existing.empty:
        last_ts = pd.to_datetime(existing["timestamp"], utc=True).max()
        fetch_from_ms = max(start_ms, int(last_ts.value // 1_000_000) + 1)
    if delisting_ts is not None and fetch_from_ms > int(delisting_ts.value // 1_000_000):
        pass  # nothing more to fetch past delisting
    else:
        forward = backfill_funding(sym, fetch_from_ms)
        if delisting_ts is not None and not forward.empty:
            forward = forward[pd.to_datetime(forward["timestamp"], utc=True) <= delisting_ts]
        new_frames.append(forward)

    new = pd.concat(new_frames, ignore_index=True) if new_frames else pd.DataFrame()
    return merge_funding(existing, new)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--symbols", default=None, help="comma-separated; default = full PIT universe")
    ap.add_argument("--min-free-gb", type=float, default=5.0)
    args = ap.parse_args()
    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    syms = [s.strip() for s in args.symbols.split(",")] if args.symbols else default_symbols()
    delisting_map = load_delisting_map()
    im = pd.read_parquet(INSTRUMENT_MASTER, columns=["symbol", "first_perp_kline_ts"]) if INSTRUMENT_MASTER.exists() else None

    registry = {}
    print(f"Funding top-up: {len(syms)} symbols, start<={args.start} (earlier per-symbol when proven)", flush=True)
    print(f"{'Asset':<14}{'funding pts':>12}{'funding span':>26}{'OI hist pts':>12}")
    print("─" * 66)
    for i, sym in enumerate(syms, 1):
        headroom = free_gb(ROOT)
        if headroom < args.min_free_gb:
            print(f"\nSTOP: free space {headroom:.1f}GB < --min-free-gb {args.min_free_gb}GB "
                  f"after {i - 1}/{len(syms)} symbols. Resumable -- re-run to continue.", flush=True)
            sys.exit(1)
        try:
            sym_start_ms = symbol_start_ms(sym, im, start_ms)
            fund = top_up_funding(sym, sym_start_ms, delisting_ts=delisting_map.get(sym))
            oi = backfill_oi_hist(sym)
        except Exception as e:
            print(f"{sym:<14}  ERREUR {e}"); registry[sym] = {"status": "ERROR", "error": str(e)}; continue
        ent = {"status": "PASS"}
        if len(fund):
            p = OUT / "funding" / f"{sym}.parquet"
            atomic_write_parquet(fund, p)
            ent["funding"] = {"rows": int(len(fund)),
                              "span": [str(fund['timestamp'].min()), str(fund['timestamp'].max())]}
        if len(oi):
            p = OUT / "open_interest_hist" / f"{sym}.parquet"
            atomic_write_parquet(oi, p)
            ent["oi_hist"] = {"rows": int(len(oi)),
                              "span": [str(oi['timestamp'].min()), str(oi['timestamp'].max())]}
        registry[sym] = ent
        fspan = ent.get("funding", {}).get("span", ["", ""])
        print(f"  [{i:3}/{len(syms)}] {sym:<14}{ent.get('funding',{}).get('rows',0):>10}"
              f"{(fspan[0][:10]+'→'+fspan[1][:10]):>26}{ent.get('oi_hist',{}).get('rows',0):>12} "
              f"free={headroom:.1f}GB", flush=True)

    REG.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    REG.write_text(yaml.safe_dump(registry, sort_keys=True, allow_unicode=True))
    n_ok = sum(1 for v in registry.values() if v.get("status") == "PASS")
    print(f"\nBINANCE_FREE_BACKFILL : {n_ok}/{len(syms)} assets → {REG.relative_to(ROOT)}")
    print("  ⚠ liquidations historiques NON incluses (indisponibles gratuitement)")


if __name__ == "__main__":
    main()
