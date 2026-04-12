import json
import scrapy
from marketintel.items import SignalItem


class MempoolSpaceSpider(scrapy.Spider):
    name = "mempool_space"
    allowed_domains = ["mempool.space"]

    def __init__(self, from_date=None, to_date=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_date = from_date

    def start_requests(self):
        if self.from_date:
            # mempool.space = snapshot temps réel, pas de données historiques
            self.logger.info(
                "mempool_space : données historiques non disponibles — spider ignoré en mode history"
            )
            return

        for url in [
            "https://mempool.space/api/mempool",
            "https://mempool.space/api/v1/fees/recommended",
        ]:
            yield scrapy.Request(url, callback=self.parse)

    def parse(self, response):
        data = json.loads(response.text)

        if "/api/mempool" in response.url:
            for key in ["count", "vsize", "total_fee"]:
                yield SignalItem(
                    source="mempool_space",
                    source_type="onchain",
                    asset="BTC",
                    title=f"BTC mempool {key}",
                    text=None,
                    url=response.url,
                    published_at=None,
                    language="en",
                    event_type="btc_mempool",
                    importance=0.80,
                    confidence=0.95,
                    feature_name=key,
                    value=data.get(key),
                    unit="native",
                    metadata={},
                    raw=data,
                )

        elif "/fees/recommended" in response.url:
            for key, value in data.items():
                yield SignalItem(
                    source="mempool_space",
                    source_type="onchain",
                    asset="BTC",
                    title=f"BTC recommended fee {key}",
                    text=None,
                    url=response.url,
                    published_at=None,
                    language="en",
                    event_type="btc_fee",
                    importance=0.80,
                    confidence=0.95,
                    feature_name=f"recommended_fee_{key}",
                    value=value,
                    unit="sat/vB",
                    metadata={},
                    raw=data,
                )
