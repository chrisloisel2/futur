import json
import os

import scrapy
from marketintel.items import SignalItem

_SERIES_MAP = {
    "CPIAUCSL": "cpi_us",
    "FEDFUNDS": "fed_funds_rate",
    "UNRATE":   "unemployment_rate",
    "M2SL":     "m2_money_supply",
    "DGS10":    "us10y_yield",
    "T10YIE":   "breakeven_inflation_10y",
    "DEXUSEU":  "eur_usd",
    "DEXJPUS":  "jpy_usd",
}
_LIVE_TAIL = 24   # nb d'observations en mode live


class FredCalendarSpider(scrapy.Spider):
    name = "fred_calendar"
    allowed_domains = ["api.stlouisfed.org"]

    def __init__(self, from_date=None, to_date=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_date = from_date
        self.to_date = to_date

    def start_requests(self):
        api_key = os.getenv("FRED_API_KEY", "")
        if not api_key:
            self.logger.warning("FRED_API_KEY non défini — spider fred_calendar ignoré")
            return

        for series_id, feature_name in _SERIES_MAP.items():
            params = f"series_id={series_id}&api_key={api_key}&file_type=json"
            if self.from_date:
                params += f"&observation_start={self.from_date}"
            if self.to_date:
                params += f"&observation_end={self.to_date}"

            url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
            yield scrapy.Request(url, callback=self.parse, meta={
                "series_id": series_id,
                "feature_name": feature_name,
            })

    def parse(self, response):
        data = json.loads(response.text)
        feature_name = response.meta["feature_name"]
        series_id = response.meta["series_id"]
        observations = data.get("observations", [])

        # En mode live, on garde seulement les N dernières
        if not self.from_date:
            observations = observations[-_LIVE_TAIL:]

        for row in observations:
            value = row.get("value")
            if value in (None, ".", ""):
                continue

            yield SignalItem(
                source="fred",
                source_type="macro",
                asset="MACRO",
                title=series_id,
                text=None,
                url=response.url,
                published_at=row.get("date"),
                language="en",
                event_type="macro_series",
                importance=0.95,
                confidence=0.99,
                feature_name=feature_name,
                value=float(value),
                unit="macro",
                metadata={"series_id": series_id},
                raw=row,
            )
