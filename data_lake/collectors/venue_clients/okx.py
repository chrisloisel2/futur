#!/usr/bin/env python3
"""OKX : `listTime` est publie pour le spot et les swaps. Deux appels groupes, aucune cle."""
from __future__ import annotations

from typing import Any, Dict, List

from data_lake.collectors.venue_clients import QUOTES, http_json, instrument, ms_to_iso

VENUE = "okx"
BASE = "https://www.okx.com/api/v5/public/instruments?instType=%s"
HAS_NATIVE_LISTING_TIME = True


def fetch_instruments() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for inst_type, market in (("SPOT", "spot"), ("SWAP", "perp")):
        d = http_json(BASE % inst_type)
        for x in d.get("data") or []:
            if market == "spot":
                base, quote = x.get("baseCcy"), x.get("quoteCcy")
            else:
                if x.get("ctType") != "linear":
                    continue
                fam = (x.get("instFamily") or "").split("-")
                base, quote = (fam[0] if fam else None), (fam[1] if len(fam) > 1 else x.get("settleCcy"))
            if not base or (quote or "").upper() not in QUOTES:
                continue
            out.append(instrument(VENUE, market, x["instId"], base, quote, x.get("state"), ms_to_iso(x.get("listTime")), "listTime", x))
    return out
