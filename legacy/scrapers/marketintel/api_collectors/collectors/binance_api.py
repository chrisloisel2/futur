import time
from datetime import date, datetime, timezone
from typing import List, Dict, Any, Optional

from api_collectors import http
from api_collectors.config import BINANCE_BASE_URL
from api_collectors.utils import normalize_doc

_DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
_FUNDING_LIMIT = 1000
_INTER_REQUEST_SLEEP = 0.25


def _to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _build_doc(row: dict, asset: str, url: str) -> dict:
    funding_time = row.get("fundingTime")
    published_at = (
        datetime.fromtimestamp(int(funding_time) / 1000, tz=timezone.utc).isoformat()
        if funding_time else None
    )
    return normalize_doc(
        source="binance",
        source_type="market",
        asset=asset,
        title=f"{asset} funding rate",
        url=url,
        published_at=published_at,
        event_type="funding_rate",
        importance=0.95,
        confidence=0.99,
        feature_name="funding_rate",
        value=float(row["fundingRate"]),
        unit="ratio",
        metadata={"symbol": row.get("symbol"), "mark_price": row.get("markPrice")},
        raw=row,
    )


def fetch_binance_funding_rates(
    symbols: List[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    if symbols is None:
        symbols = _DEFAULT_SYMBOLS

    docs = []

    for symbol in symbols:
        asset = symbol.replace("USDT", "")
        url = f"{BINANCE_BASE_URL}/fapi/v1/fundingRate"

        if from_date and to_date:
            cursor_ms = _to_ms(from_date)
            end_ms = _to_ms(to_date)

            while cursor_ms < end_ms:
                params = {"symbol": symbol, "startTime": cursor_ms,
                          "endTime": end_ms, "limit": _FUNDING_LIMIT}
                resp = http.get(url, params=params)
                resp.raise_for_status()
                rows = resp.json()

                if not rows:
                    break

                for row in rows:
                    docs.append(_build_doc(row, asset, resp.url))

                last_ms = int(rows[-1]["fundingTime"])
                if last_ms <= cursor_ms:
                    break
                cursor_ms = last_ms + 1
                time.sleep(_INTER_REQUEST_SLEEP)
        else:
            resp = http.get(url, params={"symbol": symbol, "limit": 50})
            resp.raise_for_status()
            for row in resp.json():
                docs.append(_build_doc(row, asset, resp.url))

    return docs
