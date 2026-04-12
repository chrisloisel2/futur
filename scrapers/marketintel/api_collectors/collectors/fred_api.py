from datetime import date
from typing import List, Dict, Any, Optional

from api_collectors import http
from api_collectors.config import FRED_BASE_URL, FRED_API_KEY
from api_collectors.utils import normalize_doc

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
_LIVE_TAIL = 24


def fetch_fred_macro(from_date: Optional[date] = None) -> List[Dict[str, Any]]:
    if not FRED_API_KEY:
        return []

    docs = []

    for series_id, feature_name in _SERIES_MAP.items():
        params: Dict[str, Any] = {
            "series_id":  series_id,
            "api_key":    FRED_API_KEY,
            "file_type":  "json",
        }
        if from_date:
            params["observation_start"] = from_date.isoformat()

        resp = http.get(f"{FRED_BASE_URL}/fred/series/observations", params=params)
        resp.raise_for_status()
        observations = resp.json().get("observations", [])

        if not from_date:
            observations = observations[-_LIVE_TAIL:]

        for row in observations:
            value = row.get("value")
            if value in (None, "", "."):
                continue

            docs.append(normalize_doc(
                source="fred",
                source_type="macro",
                asset="MACRO",
                title=series_id,
                url=resp.url,
                published_at=row.get("date"),
                event_type="macro_series",
                importance=0.95,
                confidence=0.99,
                feature_name=feature_name,
                value=float(value),
                unit="macro",
                metadata={"series_id": series_id},
                raw=row,
            ))

    return docs
