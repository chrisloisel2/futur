"""src/alpha20/tournament/truth_shadow/product_specs.py -- Phase 4D commit 6:
real, versioned ProductSpec data, never a neutral/invented tick-lot grid.

data/venue_specs/binance_btc_eth_product_specs.json is a small, curated
snapshot of the OFFICIAL venue metadata for exactly the products this
shadow touches (BTCUSDT/ETHUSDT, spot and USD-M perpetual) -- extracted
from a real, timestamped capture of:

  - GET https://fapi.binance.com/fapi/v1/exchangeInfo   (USD-M futures)
  - GET https://api.binance.com/api/v3/exchangeInfo     (spot)

The SHA-256 of each FULL raw response is recorded in the registry file's
own `capture.source_response_sha256` -- the full responses (~1MB each,
all symbols) are not committed (this repo's convention: don't commit
large fetched blobs), but their hash freezes exactly what was read, and
the curated `specs` array below is a byte-for-byte extraction of only the
PRICE_FILTER.tickSize / LOT_SIZE.stepSize / baseAsset fields for the two
symbols this shadow needs -- nothing computed, nothing chosen by hand.

There is no canonical instrument/contract-spec registry anywhere else in
this repository (checked: no hit for "tick_size"/"lot_size"/"stepSize"/
"tickSize"/"minQty" under src/ or configs/ outside src/futur/truth and
this package) -- venue metadata is therefore the correct, and only,
source per this commit's own priority order.

quote_ccy is recorded as "USD" (Truth's mono-currency scope) even though
the venue's own quoteAsset is "USDT" (preserved verbatim in
`quote_asset_raw` in the registry file) -- this is the SAME documented,
shadow-only 1:1 convention as mapping.py's asset-symbol handling, not a
claim that USDT carries no depeg risk (it is not validated or modeled
here at all).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "venue_specs"
    / "binance_btc_eth_product_specs.json"
)


class ProductSpecUnavailableError(Exception):
    """No real, versioned ProductSpec is available for the requested
    (symbol, product_type) -- the BLOCKED_PRODUCT_SPEC condition. Never
    caught to fall back to an invented tick/lot grid."""


@dataclass(frozen=True)
class RealProductSpec:
    venue: str
    symbol: str
    type: str            # "SPOT" | "LINEAR_PERP"
    base_ccy: str
    quote_ccy: str
    quote_asset_raw: str
    tick_size: Decimal
    lot_size: Decimal
    multiplier: Decimal
    captured_at_utc: str
    source_endpoint: str


class ProductSpecRegistry:
    """Loads and indexes data/venue_specs/binance_btc_eth_product_specs.json
    (or an equivalent file) by (symbol, type). Immutable once loaded --
    nothing here computes or defaults a spec that isn't in the file."""

    def __init__(self, entries: dict[tuple[str, str], RealProductSpec], captured_at_utc: str):
        self._entries = entries
        self.captured_at_utc = captured_at_utc

    @classmethod
    def from_json_file(cls, path: Path | str = DEFAULT_REGISTRY_PATH) -> ProductSpecRegistry:
        path = Path(path)
        if not path.exists():
            raise ProductSpecUnavailableError(
                f"no ProductSpec registry file at {path} -- BLOCKED_PRODUCT_SPEC: "
                f"no real, versioned specification is available")
        data = json.loads(path.read_text())
        capture = data["capture"]
        endpoints_by_venue = {
            "binance_usdm": next((e for e in capture["source_endpoints"] if "fapi" in e), ""),
            "binance_spot": next((e for e in capture["source_endpoints"] if "api.binance" in e), ""),
        }
        entries: dict[tuple[str, str], RealProductSpec] = {}
        for raw in data["specs"]:
            key = (raw["symbol"], raw["type"])
            entries[key] = RealProductSpec(
                venue=raw["venue"], symbol=raw["symbol"], type=raw["type"],
                base_ccy=raw["base_ccy"], quote_ccy=raw["quote_ccy"],
                quote_asset_raw=raw["quote_asset_raw"],
                tick_size=Decimal(raw["tick_size"]), lot_size=Decimal(raw["lot_size"]),
                multiplier=Decimal(raw["multiplier"]),
                captured_at_utc=capture["captured_at_utc"],
                source_endpoint=endpoints_by_venue.get(raw["venue"], ""),
            )
        return cls(entries, capture["captured_at_utc"])

    def lookup(self, symbol: str, product_type: str) -> RealProductSpec:
        key = (symbol, product_type)
        if key not in self._entries:
            raise ProductSpecUnavailableError(
                f"no real ProductSpec for symbol={symbol!r} type={product_type!r} -- "
                f"BLOCKED_PRODUCT_SPEC: known specs are {sorted(self._entries)}")
        return self._entries[key]
