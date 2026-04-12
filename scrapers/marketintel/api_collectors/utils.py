import hashlib
from datetime import datetime, timezone
from typing import Any, Dict


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_fingerprint(doc: Dict[str, Any]) -> str:
    raw_key = "|".join([
        str(doc.get("source", "")),
        str(doc.get("source_type", "")),
        str(doc.get("asset", "")),
        str(doc.get("url", "")),
        str(doc.get("title", "")),
        str(doc.get("published_at", "")),
        str(doc.get("feature_name", "")),
        str(doc.get("value", "")),
    ])
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def normalize_doc(
    *,
    source: str,
    source_type: str,
    asset: str,
    title: str = None,
    text: str = None,
    url: str = None,
    author: str = None,
    published_at: str = None,
    language: str = "en",
    event_type: str = None,
    sentiment: float = None,
    importance: float = None,
    confidence: float = None,
    feature_name: str = None,
    value: Any = None,
    unit: str = None,
    metadata: Dict[str, Any] = None,
    raw: Dict[str, Any] = None,
) -> Dict[str, Any]:
    doc = {
        "source": source,
        "source_type": source_type,
        "asset": asset,
        "title": title,
        "text": text,
        "url": url,
        "author": author,
        "published_at": published_at,
        "scraped_at": utc_now_iso(),
        "language": language,
        "event_type": event_type,
        "sentiment": sentiment,
        "importance": importance,
        "confidence": confidence,
        "feature_name": feature_name,
        "value": value,
        "unit": unit,
        "metadata": metadata or {},
        "raw": raw or {},
    }
    doc["fingerprint"] = make_fingerprint(doc)
    return doc
