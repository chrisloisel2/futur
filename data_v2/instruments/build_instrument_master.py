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

Per-symbol facts, in priority order:
  1. onboardDate / status from a LIVE fapi/v1/exchangeInfo call (free, no
     auth) -- this is Binance's own PIT listing timestamp, the most
     reliable listing_ts available. Only covers symbols currently listed
     (status == TRADING); tick_size/step_size/min_notional also only
     available this way, and only reflect TODAY's filters (Binance does
     not expose historical filter values -- documented caveat, matches
     DATA_INVENTORY.yaml's tick_size_lot_size classification
     DATA_READY_WITH_CAVEAT).
  2. For symbols NOT in current exchangeInfo (delisted): fall back to
     data/derivatives_backfill/um_klines_1d/{SYM}_1d.parquet (1575 files,
     verified present) -- first row = listing_ts proxy, last row =
     delisting_ts proxy (the same daily panel scripts/backtest_ctrend_v1.py
     already reuses for PIT membership). tick_size/step_size/min_notional
     stay null for these rows -- genuinely not reproducible, do not
     fabricate a value.

Usage:
    /home/qbee/futur/.venv/bin/python3 data_v2/instruments/build_instrument_master.py
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_MANIFEST = (
    ROOT / "research/edge_factory/cross_sectional_momentum_v1/results/PIT_UNIVERSE_MANIFEST.json"
)
KLINES_1D_DIR = ROOT / "data/derivatives_backfill/um_klines_1d"
OUT_PATH = ROOT / "data_v2/instruments/instrument_master.parquet"
EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"


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


def klines_1d_bounds(symbol: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    path = KLINES_1D_DIR / f"{symbol}_1d.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["open_time"])
    if df.empty:
        return None
    ts = pd.to_datetime(df["open_time"], utc=True)
    return ts.min(), ts.max()


def build() -> pd.DataFrame:
    symbols = load_pit_universe()
    exchange_info = fetch_exchange_info()

    rows = []
    n_live, n_delisted_klines, n_unresolved = 0, 0, 0
    for symbol in symbols:
        info = exchange_info.get(symbol)
        bounds = klines_1d_bounds(symbol)

        if info is not None and info["status"] == "TRADING":
            n_live += 1
            listing_ts = info["onboard_ts"]
            if pd.isna(listing_ts) and bounds is not None:
                listing_ts = bounds[0]
            delisting_ts = pd.NaT
            valid_until = pd.NaT
            base, quote = info["base"], info["quote"]
            tick_size, step_size, min_notional = (
                info["tick_size"],
                info["step_size"],
                info["min_notional"],
            )
            contract_type = info["contract_type"]
        elif bounds is not None:
            n_delisted_klines += 1
            listing_ts, delisting_ts = bounds
            valid_until = delisting_ts
            base = symbol[:-4] if symbol.endswith("USDT") else symbol
            quote = "USDT" if symbol.endswith("USDT") else ""
            tick_size = step_size = min_notional = float("nan")
            contract_type = "PERPETUAL"
        else:
            n_unresolved += 1
            listing_ts = delisting_ts = valid_until = pd.NaT
            base = symbol[:-4] if symbol.endswith("USDT") else symbol
            quote = "USDT" if symbol.endswith("USDT") else ""
            tick_size = step_size = min_notional = float("nan")
            contract_type = "PERPETUAL"

        rows.append(
            {
                "venue": "binance",
                "symbol": symbol,
                "base": base,
                "quote": quote,
                "market_type": "perpetual" if contract_type == "PERPETUAL" else str(contract_type).lower(),
                "listing_ts": listing_ts,
                "delisting_ts": delisting_ts,
                "tick_size": tick_size,
                "step_size": step_size,
                "min_notional": min_notional,
                "contract_size": 1.0,
                "valid_from": listing_ts,
                "valid_until": valid_until,
                "source": "exchange_info_live" if (info is not None and info["status"] == "TRADING") else (
                    "um_klines_1d_bounds" if bounds is not None else "unresolved"
                ),
            }
        )

    out = pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)
    print(
        f"instrument_master: {len(out)} symbols "
        f"({n_live} live/exchange_info, {n_delisted_klines} delisted/um_klines_1d, "
        f"{n_unresolved} unresolved -- no exchangeInfo entry AND no um_klines_1d file)"
    )
    if n_unresolved:
        print("unresolved symbols (listing_ts/delisting_ts left NaT, needs a dedicated backfill):")
        print(out.loc[out["source"] == "unresolved", "symbol"].tolist())
    return out


def main() -> None:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT_PATH, index=False)
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
