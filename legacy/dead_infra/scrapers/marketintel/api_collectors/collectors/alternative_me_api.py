from datetime import date, datetime, timezone
from typing import List, Dict, Any, Optional

from api_collectors import http
from api_collectors.config import ALTERNATIVE_ME_BASE_URL
from api_collectors.utils import normalize_doc

_LIVE_LIMIT    = 30
_HISTORY_LIMIT = 0


def fetch_fear_greed(
    limit: Optional[int] = None,
    from_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    effective_limit = (
        limit if limit is not None
        else (_HISTORY_LIMIT if from_date else _LIVE_LIMIT)
    )

    resp = http.get(
        f"{ALTERNATIVE_ME_BASE_URL}/fng/",
        params={"limit": effective_limit, "format": "json"},
    )
    resp.raise_for_status()
    payload = resp.json()

    docs = []
    for row in payload.get("data", []):
        ts = row.get("timestamp")

        if from_date and ts:
            try:
                row_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
                if row_date < from_date:
                    continue
            except (ValueError, OSError):
                pass

        value = row.get("value")
        docs.append(normalize_doc(
            source="alternative_me",
            source_type="sentiment",
            asset="BTC",
            title="Fear and Greed Index",
            url=resp.url,
            published_at=ts,
            event_type="fear_greed",
            importance=0.80,
            confidence=0.95,
            feature_name="fear_greed_index",
            value=float(value) if value is not None else None,
            unit="index",
            metadata={
                "classification":    row.get("value_classification"),
                "time_until_update": row.get("time_until_update"),
            },
            raw=row,
        ))

    return docs
