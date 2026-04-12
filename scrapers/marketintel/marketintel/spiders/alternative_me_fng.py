import json
from datetime import date, datetime, timezone

import scrapy
from marketintel.items import SignalItem

_LIVE_LIMIT    = 30
_HISTORY_LIMIT = 0   # 0 = tout le disponible (~900 jours)


class AlternativeMeFearGreedSpider(scrapy.Spider):
    name = "alternative_me_fng"
    allowed_domains = ["api.alternative.me"]

    def __init__(self, from_date=None, to_date=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_date = from_date
        self.to_date = to_date

    def start_requests(self):
        limit = _HISTORY_LIMIT if self.from_date else _LIVE_LIMIT
        url = f"https://api.alternative.me/fng/?limit={limit}&format=json"
        yield scrapy.Request(url, callback=self.parse)

    def parse(self, response):
        payload = json.loads(response.text)
        from_d = date.fromisoformat(self.from_date) if self.from_date else None

        for row in payload.get("data", []):
            ts = row.get("timestamp")

            # Filtre côté client si mode historique avec from_date
            if from_d and ts:
                try:
                    row_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
                    if row_date < from_d:
                        continue
                except (ValueError, OSError):
                    pass

            value = row.get("value")
            yield SignalItem(
                source="alternative_me",
                source_type="sentiment",
                asset="BTC",
                title="Fear and Greed Index",
                text=None,
                url=response.url,
                published_at=ts,
                language="en",
                event_type="fear_greed",
                importance=0.75,
                confidence=0.90,
                feature_name="fear_greed_index",
                value=float(value) if value is not None else None,
                unit="index",
                metadata={"classification": row.get("value_classification")},
                raw=row,
            )
