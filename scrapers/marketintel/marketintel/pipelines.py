import hashlib
from datetime import datetime, timezone

from itemadapter import ItemAdapter
from pymongo import MongoClient, UpdateOne


class MongoPipeline:
    collection_name = "signals"

    def __init__(self, mongo_uri, mongo_db):
        self.mongo_uri = mongo_uri
        self.mongo_db = mongo_db
        self.client = None
        self.db = None
        self.collection = None
        self.buffer = []

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            mongo_uri=crawler.settings.get("MONGO_URI"),
            mongo_db=crawler.settings.get("MONGO_DATABASE", "market_intel"),
        )

    def open_spider(self, spider=None):
        self.client = MongoClient(self.mongo_uri)
        self.db = self.client[self.mongo_db]
        self.collection = self.db[self.collection_name]

        self.collection.create_index("fingerprint", unique=True)
        self.collection.create_index("source")
        self.collection.create_index("source_type")
        self.collection.create_index("asset")
        self.collection.create_index("published_at")
        self.collection.create_index("feature_name")
        self.collection.create_index("scraped_at")

    def close_spider(self, spider=None):
        if self.buffer:
            self.collection.bulk_write(self.buffer, ordered=False)
            self.buffer.clear()
        if self.client:
            self.client.close()

    def process_item(self, item, spider=None):
        adapter = ItemAdapter(item)

        scraped_at = datetime.now(timezone.utc).isoformat()
        adapter["scraped_at"] = scraped_at

        # Données sans published_at (snapshots live) → utiliser scraped_at
        # pour garantir un fingerprint unique à chaque run (série temporelle)
        if not adapter.get("published_at"):
            adapter["published_at"] = scraped_at

        if not adapter.get("fingerprint"):
            published_at = adapter.get("published_at", "")
            raw_key = "|".join([
                str(adapter.get("source", "")),
                str(adapter.get("source_type", "")),
                str(adapter.get("asset", "")),
                str(adapter.get("url", "")),
                str(adapter.get("title", "")),
                str(published_at),
                str(adapter.get("feature_name", "")),
                str(adapter.get("value", "")),
            ])
            adapter["fingerprint"] = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

        doc = dict(adapter)
        self.buffer.append(
            UpdateOne(
                {"fingerprint": doc["fingerprint"]},
                {"$set": doc, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
        )

        if len(self.buffer) >= 100:
            self.collection.bulk_write(self.buffer, ordered=False)
            self.buffer.clear()

        return item
