import pandas as pd

from infra.storage.timeseries_db import MongoBufferReader, MongoBufferWriter


class _FakeCollection:
    def __init__(self):
        self.data = []

    def find(self, query, sort=None, limit=0):
        symbol = query.get("symbol")
        start = query.get("event_time", {}).get("$gte")
        end = query.get("event_time", {}).get("$lte")
        source_filter = query.get("source", {}).get("$in") if "$in" in query.get("source", {}) else None
        out = []
        for doc in self.data:
            if doc["symbol"] != symbol:
                continue
            if doc["event_time"] < start or doc["event_time"] > end:
                continue
            if source_filter and doc.get("source") not in source_filter:
                continue
            out.append(doc)
        if sort:
            field, direction = sort[0]
            out = sorted(out, key=lambda x: x[field], reverse=direction < 0)
        return out[:limit] if limit else out

    def update_one(self, key, obj, upsert=False):
        for i, doc in enumerate(self.data):
            if all(doc.get(k) == key.get(k) for k in key):
                self.data[i].update(obj.get("$set", {}))
                return
        if upsert:
            rec = key.copy()
            rec.update(obj.get("$set", {}))
            self.data.append(rec)

    def create_index(self, *args, **kwargs):
        return None

    def estimated_document_count(self):
        return len(self.data)


class _FakeClient(dict):
    def __getitem__(self, item):
        if item not in self:
            self[item] = {}
        return self[item]


def test_mongo_buffer_io_roundtrip():
    client = _FakeClient()
    collection = _FakeCollection()
    client.setdefault("db", {})["coll"] = collection
    writer = MongoBufferWriter("uri", "db", "coll", client=client)
    reader = MongoBufferReader("uri", "db", "coll", client=client)
    df = pd.DataFrame(
        {
            "event_time": pd.to_datetime(["2024-01-01T00:00:00Z"]),
            "recv_time": pd.to_datetime(["2024-01-01T00:00:00Z"]),
            "symbol": ["BTCUSDT"],
            "venue": ["binance"],
            "source": ["spot"],
            "event_type": ["trade"],
            "seq": [1],
        }
    )
    writer.write_events(df, ttl_seconds=60)
    out = reader.fetch_events("BTCUSDT", df.event_time.min(), df.event_time.max())
    assert len(out) == 1
