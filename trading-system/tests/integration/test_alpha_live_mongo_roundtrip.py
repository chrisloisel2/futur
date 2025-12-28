from datetime import datetime

import pandas as pd

from infra.storage.timeseries_db import TargetPositionsCacheWriter, TargetPositionsCacheReader


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


def test_target_positions_cache_roundtrip():
    client = _FakeClient()
    client.setdefault("db", {})["target_positions_cache"] = _FakeCollection()
    writer = TargetPositionsCacheWriter("uri", "db", "target_positions_cache", client=client)
    reader = TargetPositionsCacheReader("uri", "db", "target_positions_cache", client=client)
    payload = {"scope": "portfolio", "event_time": datetime.utcnow(), "targets": []}
    writer.write_target_positions(payload, ttl_seconds=60)
    out = reader.fetch_latest()
    assert out is not None
