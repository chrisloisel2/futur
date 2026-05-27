import time
from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

from api_collectors import http
from api_collectors.config import COINGECKO_BASE_URL
from api_collectors.utils import normalize_doc

_DEFAULT_IDS = "bitcoin,ethereum,solana"
_COIN_MAP = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}
_CHUNK_DAYS = 365
_INTER_REQUEST_SLEEP = 1.2


def fetch_coingecko_markets(ids: str = _DEFAULT_IDS) -> List[Dict[str, Any]]:
    url = f"{COINGECKO_BASE_URL}/coins/markets"
    resp = http.get(url, params={"vs_currency": "usd", "ids": ids})
    resp.raise_for_status()

    docs = []
    for row in resp.json():
        asset = row.get("symbol", "").upper()
        docs.append(normalize_doc(
            source="coingecko",
            source_type="market",
            asset=asset,
            title=f"{asset} market snapshot",
            url=resp.url,
            event_type="market_snapshot",
            importance=0.85,
            confidence=0.95,
            feature_name="market_cap_rank",
            value=row.get("market_cap_rank"),
            unit="rank",
            metadata={
                "current_price":               row.get("current_price"),
                "market_cap":                  row.get("market_cap"),
                "total_volume":                row.get("total_volume"),
                "price_change_percentage_24h": row.get("price_change_percentage_24h"),
                "circulating_supply":          row.get("circulating_supply"),
            },
            raw=row,
        ))
    return docs


def fetch_coingecko_history(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    ids: str = _DEFAULT_IDS,
) -> List[Dict[str, Any]]:
    if not from_date or not to_date:
        return []

    docs = []

    for coin_id in ids.split(","):
        coin_id = coin_id.strip()
        asset = _COIN_MAP.get(coin_id, coin_id.upper())
        url = f"{COINGECKO_BASE_URL}/coins/{coin_id}/market_chart/range"

        chunk_start = from_date
        while chunk_start < to_date:
            chunk_end = min(chunk_start + timedelta(days=_CHUNK_DAYS), to_date)
            from_ts = int(datetime(chunk_start.year, chunk_start.month, chunk_start.day,
                                   tzinfo=timezone.utc).timestamp())
            to_ts   = int(datetime(chunk_end.year,   chunk_end.month,   chunk_end.day,
                                   tzinfo=timezone.utc).timestamp())

            resp = http.get(url, params={"vs_currency": "usd", "from": from_ts, "to": to_ts})
            resp.raise_for_status()
            payload = resp.json()

            for feature_name, points in {
                "price_usd":      payload.get("prices", []),
                "market_cap_usd": payload.get("market_caps", []),
                "volume_usd":     payload.get("total_volumes", []),
            }.items():
                for ts_ms, value in points:
                    published_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
                    docs.append(normalize_doc(
                        source="coingecko",
                        source_type="market",
                        asset=asset,
                        title=f"{asset} {feature_name} history",
                        url=resp.url,
                        published_at=published_at,
                        event_type="market_history",
                        importance=0.85,
                        confidence=0.95,
                        feature_name=feature_name,
                        value=value,
                        unit="usd",
                        metadata={"coin_id": coin_id},
                        raw={"timestamp_ms": ts_ms, "value": value},
                    ))

            chunk_start = chunk_end + timedelta(days=1)
            time.sleep(_INTER_REQUEST_SLEEP)

    return docs
