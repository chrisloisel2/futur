from datetime import datetime, timedelta

import pandas as pd

from infra.storage.timeseries_db import AllocCacheWriter, AllocCacheReader


class _FakeCollection:
    def __init__(self):
        self.data = []

    def update_one(self, key, obj, upsert=False):
        self.data.append(obj.get("$set", {}))

    def find_one(self, query):
        return self.data[-1] if self.data else None

    def create_index(self, *args, **kwargs):
        return None

    def estimated_document_count(self):
        return len(self.data)


class _FakeClient(dict):
    def __getitem__(self, item):
        if item not in self:
            self[item] = {}
        return self[item]


def test_alloc_cache_roundtrip():
    client = _FakeClient()
    client.setdefault("db", {})["alloc_cache"] = _FakeCollection()
    writer = AllocCacheWriter("uri", "db", "alloc_cache", client=client)
    reader = AllocCacheReader("uri", "db", "alloc_cache", client=client)
    alloc = {"scope": "portfolio", "event_time": datetime.utcnow(), "scale": 0.5}
    writer.write_alloc(alloc, ttl_seconds=60)
    out = reader.fetch_latest_alloc("portfolio")
    assert out is not None
