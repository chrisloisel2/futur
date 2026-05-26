from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

DEFAULT_QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
DEFAULT_COLLECTION = os.getenv("QDRANT_COLLECTION", "market_context_v1")
DEFAULT_MONGO_URI = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
DEFAULT_MONGO_DB = os.getenv("MARKETINTEL_MONGO_DB", os.getenv("MONGO_DB", "market_intel"))
DEFAULT_MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "signals")
DEFAULT_MODEL = os.getenv("CONTEXT_EMBEDDING_MODEL", "BAAI/bge-small-en")
HASH_FALLBACK_DIM = int(os.getenv("CONTEXT_HASH_DIM", "384"))


def _json_compact(value: Any, max_chars: int = 900) -> str:
    if value in (None, "", {}, []):
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        text = str(value)
    return text[:max_chars]


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _point_id(doc: Dict[str, Any]) -> str:
    raw = doc.get("fingerprint") or doc.get("_id") or "|".join(
        str(doc.get(k, "")) for k in ("source", "asset", "published_at", "feature_name", "value")
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw)))


def signal_to_context_text(doc: Dict[str, Any]) -> str:
    parts = [
        f"source={doc.get('source')}",
        f"type={doc.get('source_type')}",
        f"asset={doc.get('asset')}",
        f"event={doc.get('event_type')}",
        f"feature={doc.get('feature_name')}",
        f"value={doc.get('value')} {doc.get('unit') or ''}".strip(),
        f"title={doc.get('title') or ''}",
        f"text={doc.get('text') or ''}",
        f"published_at={doc.get('published_at') or ''}",
        f"metadata={_json_compact(doc.get('metadata'))}",
        f"raw={_json_compact(doc.get('raw'), max_chars=500)}",
    ]
    return "\n".join(part for part in parts if part and not part.endswith("=None"))


def signal_payload(doc: Dict[str, Any], text: str, encoder_name: str) -> Dict[str, Any]:
    published_at = _parse_dt(doc.get("published_at"))
    scraped_at = _parse_dt(doc.get("scraped_at"))
    payload = {
        "mongo_id": str(doc.get("_id", "")),
        "fingerprint": str(doc.get("fingerprint", "")),
        "source": doc.get("source"),
        "source_type": doc.get("source_type"),
        "asset": doc.get("asset"),
        "event_type": doc.get("event_type"),
        "feature_name": doc.get("feature_name"),
        "value": doc.get("value"),
        "unit": doc.get("unit"),
        "title": doc.get("title"),
        "url": doc.get("url"),
        "published_at": published_at.isoformat() if published_at else doc.get("published_at"),
        "published_ts": int(published_at.timestamp()) if published_at else None,
        "scraped_at": scraped_at.isoformat() if scraped_at else doc.get("scraped_at"),
        "importance": doc.get("importance"),
        "confidence": doc.get("confidence"),
        "sentiment": doc.get("sentiment"),
        "text": text,
        "encoder": encoder_name,
    }
    return {key: value for key, value in payload.items() if value is not None}


class HashingTextEncoder:
    """Dependency-free fallback for operational tests; prefer FastEmbed in production."""

    name = "feature_hashing_fallback"

    def __init__(self, dim: int = HASH_FALLBACK_DIM) -> None:
        self.dim = int(dim)

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for token in text.lower().replace("/", " ").replace("_", " ").split():
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vec[bucket] += sign
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
            vectors.append(vec.astype(float).tolist())
        return vectors


class FastEmbedTextEncoder:
    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from fastembed import TextEmbedding

        self.model_name = model_name
        self.name = f"fastembed:{model_name}"
        self._model = TextEmbedding(model_name=model_name)
        probe = next(iter(self._model.embed(["dimension probe"])))
        self.dim = len(probe)

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [np.asarray(vec, dtype=np.float32).astype(float).tolist() for vec in self._model.embed(list(texts))]


def make_encoder(mode: str = "auto", model_name: str = DEFAULT_MODEL):
    if mode == "hash":
        return HashingTextEncoder()
    if mode in {"auto", "fastembed"}:
        try:
            return FastEmbedTextEncoder(model_name=model_name)
        except Exception as exc:
            if mode == "fastembed":
                raise RuntimeError(
                    "FastEmbed is unavailable. Use Python 3.10+ and install requirements-contextual.txt"
                ) from exc
            return HashingTextEncoder()
    raise ValueError(f"Unknown encoder mode: {mode}")


@dataclass
class ContextVectorStore:
    url: str = DEFAULT_QDRANT_URL
    collection: str = DEFAULT_COLLECTION
    encoder_mode: str = "auto"
    model_name: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        from qdrant_client import QdrantClient

        self.encoder = make_encoder(self.encoder_mode, self.model_name)
        self.client = QdrantClient(url=self.url)
        self.ensure_collection()

    def ensure_collection(self) -> None:
        from qdrant_client.http import models

        existing = {collection.name for collection in self.client.get_collections().collections}
        if self.collection in existing:
            return

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=self.encoder.dim, distance=models.Distance.COSINE),
        )
        for field in ("asset", "source", "source_type", "event_type", "feature_name", "published_ts"):
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.INTEGER if field == "published_ts" else models.PayloadSchemaType.KEYWORD,
            )

    def upsert_signals(self, docs: Sequence[Dict[str, Any]], batch_size: int = 128) -> int:
        from qdrant_client.http import models

        total = 0
        for start in range(0, len(docs), batch_size):
            batch = docs[start : start + batch_size]
            texts = [signal_to_context_text(doc) for doc in batch]
            vectors = self.encoder.embed(texts)
            points = [
                models.PointStruct(
                    id=_point_id(doc),
                    vector=vector,
                    payload=signal_payload(doc, text, self.encoder.name),
                )
                for doc, text, vector in zip(batch, texts, vectors)
            ]
            if points:
                self.client.upsert(collection_name=self.collection, points=points, wait=True)
                total += len(points)
        return total

    def search(
        self,
        query: str,
        *,
        asset: Optional[str] = None,
        source: Optional[str] = None,
        since_ts: Optional[int] = None,
        limit: int = 10,
    ):
        from qdrant_client.http import models

        conditions = []
        if asset:
            conditions.append(models.FieldCondition(key="asset", match=models.MatchValue(value=asset.upper())))
        if source:
            conditions.append(models.FieldCondition(key="source", match=models.MatchValue(value=source)))
        if since_ts:
            conditions.append(models.FieldCondition(key="published_ts", range=models.Range(gte=since_ts)))

        query_filter = models.Filter(must=conditions) if conditions else None
        vector = self.encoder.embed([query])[0]
        return self.client.search(
            collection_name=self.collection,
            query_vector=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )


def fetch_market_signals(
    *,
    mongo_uri: str = DEFAULT_MONGO_URI,
    mongo_db: str = DEFAULT_MONGO_DB,
    mongo_collection: str = DEFAULT_MONGO_COLLECTION,
    limit: Optional[int] = None,
    asset: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from pymongo import MongoClient

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        query: Dict[str, Any] = {}
        if asset:
            query["asset"] = asset.upper()
        cursor = client[mongo_db][mongo_collection].find(query).sort("published_at", 1)
        if limit:
            cursor = cursor.limit(int(limit))
        return list(cursor)
    finally:
        client.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Index Mongo market context into Qdrant")
    parser.add_argument("--mongo-uri", default=DEFAULT_MONGO_URI)
    parser.add_argument("--mongo-db", default=DEFAULT_MONGO_DB)
    parser.add_argument("--mongo-collection", default=DEFAULT_MONGO_COLLECTION)
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--asset")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--encoder", choices=["auto", "fastembed", "hash"], default="auto")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--query")
    args = parser.parse_args(argv)

    store = ContextVectorStore(
        url=args.qdrant_url,
        collection=args.collection,
        encoder_mode=args.encoder,
        model_name=args.model,
    )

    docs = fetch_market_signals(
        mongo_uri=args.mongo_uri,
        mongo_db=args.mongo_db,
        mongo_collection=args.mongo_collection,
        limit=args.limit,
        asset=args.asset,
    )
    written = store.upsert_signals(docs)
    print(json.dumps({"collection": args.collection, "encoder": store.encoder.name, "indexed": written}, indent=2))

    if args.query:
        hits = store.search(args.query, asset=args.asset, limit=5)
        for hit in hits:
            payload = hit.payload or {}
            print(f"{hit.score:.4f} {payload.get('asset')} {payload.get('source')} {payload.get('title')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
