import json
import scrapy
from datetime import datetime, timezone

from marketintel.items import SignalItem

_BASE = "https://fapi.binance.com"
_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
_LIMIT = 1000   # max par appel Binance


def _date_to_ms(date_str: str) -> int:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _ms_to_iso(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


class BinanceFundingSpider(scrapy.Spider):
    name = "binance_funding"
    allowed_domains = ["fapi.binance.com"]

    def __init__(self, from_date=None, to_date=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_date = from_date   # "YYYY-MM-DD" ou None
        self.to_date = to_date

    def start_requests(self):
        for symbol in _SYMBOLS:
            if self.from_date:
                # ── Historique : première page de pagination ─────────────────
                start_ms = _date_to_ms(self.from_date)
                end_ms = _date_to_ms(self.to_date) if self.to_date else int(datetime.now(timezone.utc).timestamp() * 1000)
                yield scrapy.Request(
                    f"{_BASE}/fapi/v1/fundingRate"
                    f"?symbol={symbol}&startTime={start_ms}&endTime={end_ms}&limit={_LIMIT}",
                    callback=self._parse_historical,
                    meta={"symbol": symbol, "end_ms": end_ms},
                )
            else:
                # ── Live : derniers 50 ────────────────────────────────────────
                yield scrapy.Request(
                    f"{_BASE}/fapi/v1/fundingRate?symbol={symbol}&limit=50",
                    callback=self._parse_live,
                    meta={"symbol": symbol},
                )

    def _parse_live(self, response):
        symbol = response.meta["symbol"]
        asset = symbol.replace("USDT", "")
        for row in json.loads(response.text):
            yield self._make_item(row, asset, response.url)

    def _parse_historical(self, response):
        symbol = response.meta["symbol"]
        asset = symbol.replace("USDT", "")
        end_ms = response.meta["end_ms"]
        rows = json.loads(response.text)

        for row in rows:
            yield self._make_item(row, asset, response.url)

        # Pagination : si on a reçu exactement _LIMIT résultats, il y en a peut-être d'autres
        if len(rows) == _LIMIT:
            last_ms = int(rows[-1]["fundingTime"])
            if last_ms < end_ms:
                yield scrapy.Request(
                    f"{_BASE}/fapi/v1/fundingRate"
                    f"?symbol={symbol}&startTime={last_ms + 1}&endTime={end_ms}&limit={_LIMIT}",
                    callback=self._parse_historical,
                    meta={"symbol": symbol, "end_ms": end_ms},
                )

    @staticmethod
    def _make_item(row: dict, asset: str, url: str) -> SignalItem:
        funding_time = row.get("fundingTime")
        return SignalItem(
            source="binance",
            source_type="market",
            asset=asset,
            title=f"{asset} funding rate",
            text=None,
            url=url,
            published_at=_ms_to_iso(funding_time) if funding_time else None,
            language="en",
            event_type="funding_rate",
            importance=0.95,
            confidence=0.98,
            feature_name="funding_rate",
            value=float(row["fundingRate"]),
            unit="ratio",
            metadata={"symbol": row.get("symbol"), "mark_price": row.get("markPrice")},
            raw=row,
        )
