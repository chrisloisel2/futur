#!/usr/bin/env python3
"""
data_v2/instruments/build_instrument_master.py
─────────────────────────────────────────────────────────────────────────────
Builds data_v2/instruments/instrument_master.parquet: one row per
(venue, symbol) with the point-in-time facts needed to gate every later
Data V2 backfill against real listing/delisting dates (never fetch or
label data before a symbol existed, never treat a gap after delisting as
a data hole).

Universe: reuses research/edge_factory/cross_sectional_momentum_v1/results/
PIT_UNIVERSE_MANIFEST.json (symbols_ever_member, 312 names, delisted
retained) as-is -- this is the same PIT-audited universe already cited by
research/edge_factory/liquidation_relative_reversal_v1/DATA_INVENTORY.yaml
("311/312") as the 5m-OI/perp-5m/aggTrades target universe. Not rebuilt.

InstrumentMaster V2 (2026-08-10): `onboardDate` alone is not sufficient --
found on AIAUSDT, whose real funding history starts 2025-09-18 while its
exchangeInfo onboardDate is 2026-01-20 (~4 months later than the market
actually, provably existed). A symbol can be "SETTLING" or relisted, or
Binance's own onboardDate can simply be wrong/stale, and there is no
principled reason to trust exchangeInfo over the data itself when the data
disagrees. So instead of one source, FOUR independent proofs of existence
are collected per symbol and reconciled:

  exchangeinfo_onboard_ts   -- fapi/v1/exchangeInfo onboardDate (live call)
  first_perp_kline_ts       -- earliest bar across data_v2 perp_5m AND the
                               legacy um_klines_1d daily panel (union --
                               either one proving an earlier existence
                               counts)
  first_funding_ts          -- earliest row in data/derivatives_backfill/
                               binance/funding/{symbol}.parquet
  first_oi_ts               -- earliest row in data/derivatives_backfill/
                               binance_vision_metrics/{symbol}_metrics_5m.
                               parquet
        |
        v
  first_proven_market_ts = min(whichever of the four are available)
        |
        v
  listing_ts (canonical, unchanged column name/consumers) = first_proven_market_ts

listing_ts_source names which of the four produced that minimum.
metadata_conflict is True when the available sources disagree by more than
24h (the same grace period data_v2/validation/validator.py already uses
for listing/delisting boundary slop -- OI genuinely starting ~9h before
onboardDate is normal PIT-boundary jitter, not a conflict; AIAUSDT's ~4
months is). No source is ever discarded for disagreeing -- disagreement is
recorded, not resolved by picking a favorite; the EARLIEST proven instant
always wins because that's what "the market existed" means.

One exception, found while building this: first_funding_ts and first_oi_ts
each have their own backfill/archive-wide FLOOR (104/312 symbols shared
first_oi_ts == 2021-12-01 00:00:00 exactly, 18/312 shared first_funding_ts
== 2021-01-01 00:00:00 exactly, before this was handled) -- that is not
per-symbol proof, it is "this source's backfill doesn't reach further back
for ANY symbol". Detected empirically per run (FLOOR_MIN_SYMBOLS symbols
sharing the exact same instant) and excluded from reconciliation; the raw
value is still kept in the output column, nothing is hidden.

Symmetric for the end of life, deliberately asymmetric in one respect
(NEVER invent a delisting_ts that isn't demonstrated):

  last_perp_kline_ts, last_funding_ts, last_oi_ts  -- latest row per source
  exchangeinfo_status                              -- literal status string,
                                                       or the sentinel
                                                       "ABSENT" when the
                                                       symbol is not present
                                                       in the current live
                                                       exchangeInfo response
                                                       at all (verified: a
                                                       genuinely delisted
                                                       symbol like BTTUSDT/
                                                       LUNAUSDT is ABSENT,
                                                       never present under
                                                       some other status --
                                                       this is the reliable
                                                       discriminant)
        |
        v
  last_proven_market_ts = max(whichever of the three data sources are available)

delisting_ts (canonical) = last_proven_market_ts, but ONLY when
exchangeinfo_status == "ABSENT" -- a symbol still present in exchangeInfo
(TRADING, SETTLING, PENDING_TRADING, ...) is still alive by definition,
however stale its last observed data row is; staleness is a data-gap
question (data_v2.validation.validator's staleness_gate), never grounds to
fabricate a delisting date here.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_MANIFEST = (
    ROOT / "research/edge_factory/cross_sectional_momentum_v1/results/PIT_UNIVERSE_MANIFEST.json"
)
KLINES_1D_DIR = ROOT / "data/derivatives_backfill/um_klines_1d"
PERP_5M_DIR = ROOT / "data_v2/normalized/perp_ohlcv/venue=binance"
FUNDING_DIR = ROOT / "data/derivatives_backfill/binance/funding"
OI_METRICS_DIR = ROOT / "data/derivatives_backfill/binance_vision_metrics"
OUT_PATH = ROOT / "data_v2/instruments/instrument_master.parquet"
EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

ABSENT = "ABSENT"  # sentinel exchangeinfo_status: symbol not in the live response at all
# same grace period as data_v2/validation/validator.py's listing/delisting
# boundary check -- real PIT sources routinely disagree by a few hours
# (e.g. OI data observed starting ~9h before exchangeInfo's onboardDate);
# only a disagreement bigger than that is a genuine conflict worth flagging.
CONFLICT_THRESHOLD = pd.Timedelta(hours=24)
# A first_funding_ts/first_oi_ts that lands on the EXACT same instant for
# many unrelated symbols is not proof of THAT symbol's listing -- it is the
# backfill/archive's own starting floor (verified empirically: 104/312
# symbols share first_oi_ts == 2021-12-01 00:00:00 exactly, 18/312 share
# first_funding_ts == 2021-01-01 00:00:00 exactly -- organic listing dates
# do not collide like that). Detected empirically per run (not hardcoded),
# so it stays correct if a backfill is ever extended further back.
FLOOR_MIN_SYMBOLS = 3


def _detect_source_floor(first_values: list) -> Optional[pd.Timestamp]:
    vals = [v for v in first_values if pd.notna(v)]
    if not vals:
        return None
    counts = pd.Series(vals).value_counts()
    return counts.index[0] if counts.iloc[0] >= FLOOR_MIN_SYMBOLS else None


def load_pit_universe() -> list[str]:
    manifest = json.loads(UNIVERSE_MANIFEST.read_text())
    symbols = sorted(set(manifest["symbols_ever_member"]))
    return symbols


def fetch_exchange_info() -> dict[str, dict]:
    with urllib.request.urlopen(EXCHANGE_INFO_URL, timeout=30) as resp:
        payload = json.load(resp)
    by_symbol = {}
    for s in payload.get("symbols", []):
        filters = {f["filterType"]: f for f in s.get("filters", [])}
        by_symbol[s["symbol"]] = {
            "base": s.get("baseAsset"),
            "quote": s.get("quoteAsset"),
            "status": s.get("status"),
            "contract_type": s.get("contractType"),
            "onboard_ts": pd.to_datetime(s.get("onboardDate"), unit="ms", utc=True)
            if s.get("onboardDate")
            else pd.NaT,
            "tick_size": float(filters["PRICE_FILTER"]["tickSize"]) if "PRICE_FILTER" in filters else float("nan"),
            "step_size": float(filters["LOT_SIZE"]["stepSize"]) if "LOT_SIZE" in filters else float("nan"),
            "min_notional": float(filters["MIN_NOTIONAL"]["notional"]) if "MIN_NOTIONAL" in filters else float("nan"),
        }
    return by_symbol


def _bounds_from_single_file(path: Path, ts_col: str) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=[ts_col])
    if df.empty:
        return None
    ts = pd.to_datetime(df[ts_col], utc=True)
    return ts.min(), ts.max()


def _bounds_from_year_partitioned(
    base_dir: Path, symbol: str, filename: str, ts_col: str = "timestamp"
) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    parts = sorted((base_dir / f"symbol={symbol}").glob(f"year=*/{filename}"))
    if not parts:
        return None
    mins, maxs = [], []
    for p in parts:
        df = pd.read_parquet(p, columns=[ts_col])
        if df.empty:
            continue
        ts = pd.to_datetime(df[ts_col], utc=True)
        mins.append(ts.min())
        maxs.append(ts.max())
    if not mins:
        return None
    return min(mins), max(maxs)


def klines_1d_bounds(symbol: str) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    path = KLINES_1D_DIR / f"{symbol}_1d.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["open_time"])
    if df.empty:
        return None
    ts = pd.to_datetime(df["open_time"], utc=True)
    return ts.min(), ts.max()


def perp_kline_bounds(symbol: str) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    """Union of every source that carries a real perp price series for this
    symbol -- data_v2's own perp_5m pipeline (partial, still backfilling)
    AND the legacy um_klines_1d daily panel (complete). Either one proving
    an earlier/later bound than the other counts -- this is proof-
    gathering, not "prefer the new pipeline"."""
    candidates = [
        b for b in (
            _bounds_from_year_partitioned(PERP_5M_DIR, symbol, "perp_5m.parquet"),
            klines_1d_bounds(symbol),
        )
        if b is not None
    ]
    if not candidates:
        return None
    return min(b[0] for b in candidates), max(b[1] for b in candidates)


def funding_bounds(symbol: str) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    return _bounds_from_single_file(FUNDING_DIR / f"{symbol}.parquet", "timestamp")


def oi_bounds(symbol: str) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    return _bounds_from_single_file(OI_METRICS_DIR / f"{symbol}_metrics_5m.parquet", "create_time")


def reconcile_start(
    exchangeinfo_onboard_ts: pd.Timestamp,
    first_perp_kline_ts: pd.Timestamp,
    first_funding_ts: pd.Timestamp,
    first_oi_ts: pd.Timestamp,
) -> tuple[pd.Timestamp, Optional[str], bool]:
    """Returns (first_proven_market_ts, listing_ts_source, metadata_conflict).
    first_proven_market_ts is the MIN of whichever sources are available --
    any source proving an earlier existence wins, none is ever discarded
    for disagreeing. Ties broken by the priority order below (exchangeInfo
    first) purely for a deterministic listing_ts_source label."""
    candidates = {
        "exchangeinfo_onboard_ts": exchangeinfo_onboard_ts,
        "first_perp_kline_ts": first_perp_kline_ts,
        "first_funding_ts": first_funding_ts,
        "first_oi_ts": first_oi_ts,
    }
    available = {k: v for k, v in candidates.items() if pd.notna(v)}
    if not available:
        return pd.NaT, None, False

    first_proven_market_ts = min(available.values())
    listing_ts_source = next(
        name for name in
        ("exchangeinfo_onboard_ts", "first_perp_kline_ts", "first_funding_ts", "first_oi_ts")
        if name in available and available[name] == first_proven_market_ts
    )
    spread = max(available.values()) - min(available.values())
    metadata_conflict = len(available) >= 2 and spread > CONFLICT_THRESHOLD
    return first_proven_market_ts, listing_ts_source, metadata_conflict


def reconcile_end(
    exchangeinfo_status: str,
    last_perp_kline_ts: pd.Timestamp,
    last_funding_ts: pd.Timestamp,
    last_oi_ts: pd.Timestamp,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Returns (last_proven_market_ts, delisting_ts). delisting_ts is NEVER
    set unless exchangeinfo_status == ABSENT -- a symbol still present in
    exchangeInfo (any status) is still alive; a stale/missing recent row is
    a data-gap question for the validator's staleness gate, not proof of
    delisting."""
    candidates = [t for t in (last_perp_kline_ts, last_funding_ts, last_oi_ts) if pd.notna(t)]
    last_proven_market_ts = max(candidates) if candidates else pd.NaT
    confirmed_delisted = exchangeinfo_status == ABSENT and pd.notna(last_proven_market_ts)
    delisting_ts = last_proven_market_ts if confirmed_delisted else pd.NaT
    return last_proven_market_ts, delisting_ts


def build() -> pd.DataFrame:
    symbols = load_pit_universe()
    exchange_info = fetch_exchange_info()

    # pass 1: collect every source's raw bounds for every symbol up front --
    # needed before per-symbol reconciliation so source-wide backfill
    # floors (see FLOOR_MIN_SYMBOLS) can be detected across the WHOLE
    # corpus, not guessed from one symbol at a time.
    perp_bounds_by_symbol = {s: perp_kline_bounds(s) for s in symbols}
    funding_bounds_by_symbol = {s: funding_bounds(s) for s in symbols}
    oi_bounds_by_symbol = {s: oi_bounds(s) for s in symbols}

    funding_floor = _detect_source_floor([b[0] for b in funding_bounds_by_symbol.values() if b])
    oi_floor = _detect_source_floor([b[0] for b in oi_bounds_by_symbol.values() if b])
    if funding_floor is not None:
        print(f"detected first_funding_ts source floor at {funding_floor} -- excluded from reconciliation as proof")
    if oi_floor is not None:
        print(f"detected first_oi_ts source floor at {oi_floor} -- excluded from reconciliation as proof")

    rows = []
    n_conflict, n_pushed_back, n_confirmed_delisted, n_unresolved = 0, 0, 0, 0
    for symbol in symbols:
        info = exchange_info.get(symbol)
        exchangeinfo_status = info["status"] if info is not None else ABSENT
        exchangeinfo_onboard_ts = info["onboard_ts"] if info is not None else pd.NaT

        perp_b = perp_bounds_by_symbol[symbol]
        first_perp_kline_ts, last_perp_kline_ts = perp_b if perp_b else (pd.NaT, pd.NaT)
        funding_b = funding_bounds_by_symbol[symbol]
        first_funding_ts, last_funding_ts = funding_b if funding_b else (pd.NaT, pd.NaT)
        oi_b = oi_bounds_by_symbol[symbol]
        first_oi_ts, last_oi_ts = oi_b if oi_b else (pd.NaT, pd.NaT)

        # floor-artifact exclusion: a first_* value exactly on the detected
        # source floor is not this symbol's own proof, it's the source's
        # blanket starting point -- excluded from reconciliation, kept
        # as-is in the raw first_funding_ts/first_oi_ts output columns so
        # nothing is silently hidden.
        reconcile_funding_ts = pd.NaT if (funding_floor is not None and first_funding_ts == funding_floor) else first_funding_ts
        reconcile_oi_ts = pd.NaT if (oi_floor is not None and first_oi_ts == oi_floor) else first_oi_ts

        first_proven_market_ts, listing_ts_source, metadata_conflict = reconcile_start(
            exchangeinfo_onboard_ts, first_perp_kline_ts, reconcile_funding_ts, reconcile_oi_ts
        )
        last_proven_market_ts, delisting_ts = reconcile_end(
            exchangeinfo_status, last_perp_kline_ts, last_funding_ts, last_oi_ts
        )

        if metadata_conflict:
            n_conflict += 1
        if pd.notna(first_proven_market_ts) and pd.notna(exchangeinfo_onboard_ts) and (
            first_proven_market_ts < exchangeinfo_onboard_ts - CONFLICT_THRESHOLD
        ):
            n_pushed_back += 1
        if pd.notna(delisting_ts):
            n_confirmed_delisted += 1
        if pd.isna(first_proven_market_ts):
            n_unresolved += 1

        listing_ts = first_proven_market_ts
        if info is not None:
            base, quote = info["base"], info["quote"]
            tick_size, step_size, min_notional = info["tick_size"], info["step_size"], info["min_notional"]
            contract_type = info["contract_type"]
        else:
            base = symbol[:-4] if symbol.endswith("USDT") else symbol
            quote = "USDT" if symbol.endswith("USDT") else ""
            tick_size = step_size = min_notional = float("nan")
            contract_type = "PERPETUAL"

        rows.append({
            "venue": "binance",
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "market_type": "perpetual" if contract_type == "PERPETUAL" else str(contract_type).lower(),
            "exchangeinfo_status": exchangeinfo_status,
            "exchangeinfo_onboard_ts": exchangeinfo_onboard_ts,
            "first_perp_kline_ts": first_perp_kline_ts,
            "first_funding_ts": first_funding_ts,
            "first_oi_ts": first_oi_ts,
            "first_proven_market_ts": first_proven_market_ts,
            "listing_ts": listing_ts,
            "listing_ts_source": listing_ts_source,
            "metadata_conflict": metadata_conflict,
            "last_perp_kline_ts": last_perp_kline_ts,
            "last_funding_ts": last_funding_ts,
            "last_oi_ts": last_oi_ts,
            "last_proven_market_ts": last_proven_market_ts,
            "delisting_ts": delisting_ts,
            "tick_size": tick_size,
            "step_size": step_size,
            "min_notional": min_notional,
            "contract_size": 1.0,
            "valid_from": listing_ts,
            "valid_until": delisting_ts,
        })

    out = pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)
    print(
        f"instrument_master V2: {len(out)} symbols, "
        f"{n_conflict} metadata_conflict (sources disagree > {CONFLICT_THRESHOLD}), "
        f"{n_pushed_back} listing_ts pushed back vs exchangeinfo_onboard_ts by > {CONFLICT_THRESHOLD}, "
        f"{n_confirmed_delisted} confirmed delisted (ABSENT from live exchangeInfo), "
        f"{n_unresolved} unresolved (no proof from any of the 4 sources)"
    )
    if n_pushed_back:
        pushed = out.loc[
            out["listing_ts"] < (out["exchangeinfo_onboard_ts"] - CONFLICT_THRESHOLD)
        ][["symbol", "exchangeinfo_onboard_ts", "listing_ts", "listing_ts_source"]]
        print("symbols with listing_ts pushed back by a proven earlier source:")
        print(pushed.to_string(index=False))
    if n_unresolved:
        print("unresolved symbols (listing_ts left NaT, needs a dedicated backfill):")
        print(out.loc[out["listing_ts"].isna(), "symbol"].tolist())
    return out


def main() -> None:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT_PATH, index=False)
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
