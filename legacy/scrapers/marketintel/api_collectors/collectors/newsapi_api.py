from typing import List, Dict, Any

from api_collectors import http
from api_collectors.config import NEWSAPI_BASE_URL, NEWSAPI_API_KEY
from api_collectors.utils import normalize_doc

_DEFAULT_QUERY = "bitcoin OR ethereum OR solana OR crypto"


def _detect_asset(text: str) -> str:
    text = (text or "").upper()
    if "BITCOIN" in text or "BTC" in text:
        return "BTC"
    if "ETHEREUM" in text or "ETH" in text:
        return "ETH"
    if "SOLANA" in text or "SOL" in text:
        return "SOL"
    return "TOTAL"


def fetch_newsapi_everything(query: str = _DEFAULT_QUERY) -> List[Dict[str, Any]]:
    if not NEWSAPI_API_KEY:
        return []

    resp = http.get(
        f"{NEWSAPI_BASE_URL}/everything",
        params={"q": query, "language": "en", "sortBy": "publishedAt", "pageSize": 100},
        headers={"X-Api-Key": NEWSAPI_API_KEY},
    )
    resp.raise_for_status()
    payload = resp.json()

    docs = []
    for article in payload.get("articles", []):
        title       = article.get("title") or ""
        description = article.get("description") or ""
        content     = article.get("content") or ""
        full_text   = " ".join(x for x in [description, content] if x)

        docs.append(normalize_doc(
            source="newsapi",
            source_type="news",
            asset=_detect_asset(f"{title} {full_text}"),
            title=title,
            text=full_text,
            url=article.get("url"),
            author=article.get("author"),
            published_at=article.get("publishedAt"),
            event_type="news",
            importance=0.70,
            confidence=0.75,
            metadata={"source_name": (article.get("source") or {}).get("name")},
            raw=article,
        ))

    return docs
