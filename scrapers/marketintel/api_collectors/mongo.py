from datetime import datetime, timezone
from typing import Iterable, List, Dict, Any

from pymongo import MongoClient, UpdateOne

from api_collectors.config import MONGO_URI, MONGO_DB, MONGO_COLLECTION


class MongoWriter:
    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client[MONGO_DB]
        self.collection = self.db[MONGO_COLLECTION]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self.collection.create_index("fingerprint", unique=True)
        self.collection.create_index("source")
        self.collection.create_index("source_type")
        self.collection.create_index("asset")
        self.collection.create_index("published_at")
        self.collection.create_index("feature_name")

    def upsert_many(self, docs: Iterable[Dict[str, Any]]) -> int:
        docs = list(docs)
        if not docs:
            return 0

        ops: List[UpdateOne] = [
            UpdateOne(
                {"fingerprint": doc["fingerprint"]},
                {
                    "$set": doc,
                    "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                },
                upsert=True,
            )
            for doc in docs
        ]

        result = self.collection.bulk_write(ops, ordered=False)
        return result.upserted_count + result.modified_count

    def close(self) -> None:
        self.client.close()
