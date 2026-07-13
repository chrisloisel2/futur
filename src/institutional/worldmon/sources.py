"""
src/institutional/worldmon/sources.py
─────────────────────────────────────────────────────────────────────────────
Sources WORLD MONITOR — signaux mondiaux EXOGÈNES (indépendants du marché
crypto), APIs publiques documentées uniquement.

  • GDELT DOC 2.0    : volume & tonalité média mondiaux d'une requête
    (timelinevol + timelinetone). Rate-limit 5 s respecté.
  • USGS             : séismes M≥5 (événements géophysiques réels, exogènes).
  • CoinGecko global : état macro-marché (mcap total, dominance BTC) — contexte.

Horodatage CAUSAL : chaque point porte SA date d'occurrence (publication /
mesure), jamais la date d'ingestion → aucun lookahead possible en aval.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

from src.institutional.worldmon.bigdata_store import content_hash

UA = "Mozilla/5.0 (compatible; futur-worldmon/1.0; public-apis)"
_last_gdelt = [0.0]


def _get(url: str, timeout: float = 25.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _gdelt(url: str, retries: int = 2):
    for attempt in range(retries + 1):
        wait = 5.5 - (time.time() - _last_gdelt[0])   # GDELT : 1 req / 5 s
        if wait > 0:
            time.sleep(wait)
        _last_gdelt[0] = time.time()
        raw = _get(url)
        if raw and not raw.lstrip().startswith(b"Please limit"):  # pas un 429
            return raw
        time.sleep(6)   # backoff sur rate-limit
    return raw


def fetch_gdelt(query: str = "(bitcoin OR ethereum OR cryptocurrency)",
                timespan: str = "3d") -> List[Dict]:
    """Volume normalisé + tonalité média mondiale. Points horodatés à leur date."""
    out = []
    q = urllib.parse.quote(query)
    for mode, field in (("timelinevol", "gdelt_vol"), ("timelinetone", "gdelt_tone")):
        url = (f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}"
               f"&mode={mode}&format=json&timespan={timespan}")
        try:
            data = json.loads(_gdelt(url))
        except Exception:
            continue
        series = data.get("timeline", [])
        if not series:
            continue
        for pt in series[0].get("data", []):
            ts = pt.get("date")
            try:
                dt = datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            out.append({
                "ts": dt.isoformat(), "source": f"gdelt:{field}",
                "kind": "media_metric", "metric": field,
                "value": float(pt.get("value", 0)), "symbols": [],
                "query": query,
                "content_hash": content_hash("gdelt", field, ts),
            })
    return out


def fetch_usgs(min_mag: float = 5.0, days: int = 3) -> List[Dict]:
    start = (datetime.now(timezone.utc).timestamp() - days * 86400)
    start_s = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%d")
    url = ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
           f"&starttime={start_s}&minmagnitude={min_mag}")
    try:
        data = json.loads(_get(url))
    except Exception:
        return []
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        t = p.get("time")
        if not t:
            continue
        dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        out.append({
            "ts": dt.isoformat(), "source": "usgs",
            "kind": "geophysical", "metric": "earthquake",
            "value": float(p.get("mag", 0)), "place": p.get("place"),
            "symbols": [], "content_hash": content_hash("usgs", f.get("id")),
        })
    return out


def fetch_global_macro() -> List[Dict]:
    try:
        data = json.loads(_get("https://api.coingecko.com/api/v3/global"))["data"]
    except Exception:
        return []
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for metric, val in (
        ("total_mcap_usd", data.get("total_market_cap", {}).get("usd", 0)),
        ("btc_dominance", data.get("market_cap_percentage", {}).get("btc", 0)),
        ("eth_dominance", data.get("market_cap_percentage", {}).get("eth", 0)),
        ("mcap_change_24h", data.get("market_cap_change_percentage_24h_usd", 0)),
    ):
        out.append({
            "ts": now, "source": "coingecko:global", "kind": "macro_market",
            "metric": metric, "value": float(val or 0), "symbols": [],
            "content_hash": content_hash("cg_global", metric, now[:13]),
        })
    return out


def fetch_all() -> Dict[str, List[Dict]]:
    return {"gdelt": fetch_gdelt(), "usgs": fetch_usgs(),
            "macro": fetch_global_macro()}
