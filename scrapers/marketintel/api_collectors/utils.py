import hashlib
from datetime import datetime, timezone
from typing import Any, Dict


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_fingerprint(doc: Dict[str, Any]) -> str:
    """
    Fingerprint unique par événement.
    Si published_at est vide (snapshot live sans timestamp source),
    on utilise scraped_at pour garantir un document distinct à chaque collecte.
    """
    published_at = doc.get("published_at") or doc.get("scraped_at", "")
    raw_key = "|".join([
        str(doc.get("source", "")),
        str(doc.get("source_type", "")),
        str(doc.get("asset", "")),
        str(doc.get("url", "")),
        str(doc.get("title", "")),
        str(published_at),
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
    scraped_at = utc_now_iso()

    # Données sans timestamp source (snapshots live) → on utilise scraped_at
    # Cela crée une série temporelle : chaque run produit un document distinct.
    effective_published_at = published_at if published_at is not None else scraped_at

    doc = {
        "source": source,
        "source_type": source_type,
        "asset": asset,
        "title": title,
        "text": text,
        "url": url,
        "author": author,
        "published_at": effective_published_at,
        "scraped_at": scraped_at,
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
