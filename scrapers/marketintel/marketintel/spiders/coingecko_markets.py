import json
import scrapy
from datetime import datetime, date, timedelta, timezone

from marketintel.items import SignalItem

_BASE = "https://api.coingecko.com/api/v3"
_COIN_MAP = {
    "bitcoin":  "BTC",
    "ethereum": "ETH",
    "solana":   "SOL",
}
_CHUNK_DAYS = 365   # > 90 jours → CoinGecko retourne des données journalières


def _date_to_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


class CoinGeckoMarketsSpider(scrapy.Spider):
    name = "coingecko_markets"
    allowed_domains = ["api.coingecko.com"]

    def __init__(self, from_date=None, to_date=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_date = from_date
        self.to_date = to_date

    def start_requests(self):
        if self.from_date:
            # ── Historique : chunks annuels par coin ─────────────────────────
            from_d = date.fromisoformat(self.from_date)
            to_d = date.fromisoformat(self.to_date) if self.to_date else date.today()

            for coin_id, symbol in _COIN_MAP.items():
                chunk_start = from_d
                while chunk_start < to_d:
                    chunk_end = min(chunk_start + timedelta(days=_CHUNK_DAYS), to_d)
                    from_ts = _date_to_ts(chunk_start)
                    to_ts = _date_to_ts(chunk_end)
                    url = (
                        f"{_BASE}/coins/{coin_id}/market_chart/range"
                        f"?vs_currency=usd&from={from_ts}&to={to_ts}"
                    )
                    yield scrapy.Request(
                        url,
                        callback=self._parse_history,
                        meta={"coin_id": coin_id, "symbol": symbol},
                    )
                    chunk_start = chunk_end + timedelta(days=1)
        else:
            # ── Live : snapshot courant ───────────────────────────────────────
            yield scrapy.Request(
                f"{_BASE}/coins/markets?vs_currency=usd&ids=bitcoin,ethereum,solana",
                callback=self._parse_live,
            )

    def _parse_live(self, response):
        for row in json.loads(response.text):
            symbol = row.get("symbol", "").upper()
            yield SignalItem(
                source="coingecko",
                source_type="market",
                asset=symbol,
                title=f"{symbol} market snapshot",
                text=None,
                url=response.url,
                published_at=None,
                language="en",
                event_type="market_snapshot",
                importance=0.85,
                confidence=0.95,
                feature_name="market_cap_rank",
                value=row.get("market_cap_rank"),
                unit="rank",
                metadata={
                    "current_price":              row.get("current_price"),
                    "market_cap":                 row.get("market_cap"),
                    "total_volume":               row.get("total_volume"),
                    "price_change_percentage_24h": row.get("price_change_percentage_24h"),
                },
                raw=row,
            )

    def _parse_history(self, response):
        payload = json.loads(response.text)
        coin_id = response.meta["coin_id"]
        symbol = response.meta["symbol"]

        series = {
            "price_usd":      payload.get("prices", []),
            "market_cap_usd": payload.get("market_caps", []),
            "volume_usd":     payload.get("total_volumes", []),
        }

        for feature_name, points in series.items():
            for ts_ms, value in points:
                published_at = datetime.fromtimestamp(
                    ts_ms / 1000, tz=timezone.utc
                ).isoformat()
                yield SignalItem(
                    source="coingecko",
                    source_type="market",
                    asset=symbol,
                    title=f"{symbol} {feature_name} history",
                    text=None,
                    url=response.url,
                    published_at=published_at,
                    language="en",
                    event_type="market_history",
                    importance=0.85,
                    confidence=0.95,
                    feature_name=feature_name,
                    value=value,
                    unit="usd",
                    metadata={"coin_id": coin_id},
                    raw={"timestamp_ms": ts_ms, "value": value},
                )
