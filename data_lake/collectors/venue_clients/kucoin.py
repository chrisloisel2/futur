#!/usr/bin/env python3
"""KuCoin : `firstOpenDate` sur les contrats a terme. Le spot n'expose pas de date fiable
(`tradingStartTime` est nul sur les anciens marches) : rendu sans date."""
from __future__ import annotations

from typing import Any, Dict, List

from data_lake.collectors.venue_clients import QUOTES, VenueError, http_json, instrument, ms_to_iso

VENUE = "kucoin"
FUTURES = "https://api-futures.kucoin.com/api/v1/contracts/active"
SPOT = "https://api.kucoin.com/api/v2/symbols"
HAS_NATIVE_LISTING_TIME = True


def fetch_instruments() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for x in http_json(FUTURES).get("data") or []:
        base, quote = x.get("baseCurrency"), x.get("quoteCurrency")
        if not base or (quote or "").upper() not in QUOTES or x.get("expireDate"):
            continue
        base = "BTC" if base == "XBT" else base
        out.append(instrument(VENUE, "perp", x["symbol"], base, quote, x.get("status"), ms_to_iso(x.get("firstOpenDate")), "firstOpenDate", x))
    try:
        for x in http_json(SPOT).get("data") or []:
            base, quote = x.get("baseCurrency"), x.get("quoteCurrency")
            if not base or (quote or "").upper() not in QUOTES:
                continue
            ts = ms_to_iso(x.get("tradingStartTime"))
            out.append(instrument(VENUE, "spot", x["symbol"], base, quote, "enabled" if x.get("enableTrading") else "disabled", ts, "tradingStartTime" if ts else None, x))
    except VenueError:
        pass
    return out
