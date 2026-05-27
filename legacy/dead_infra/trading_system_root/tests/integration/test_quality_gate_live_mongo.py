import pandas as pd

from pipeline.quality.gate import QualityGate
from pipeline.quality.checks import (
    BookSanityCheck,
    ClockSkewCheck,
    CrossSourceConsistencyCheck,
    DuplicateCheck,
    HaltDetectionCheck,
    MissingnessCheck,
    MicrostructureToxicityCheck,
    OutlierCheck,
    SchemaValidationCheck,
    SequenceGapCheck,
    StalenessCheck,
    TimeTravelCheck,
)


class _FakeCollection:
    def __init__(self):
        self.data = []

    def find(self, query, sort=None, limit=0):
        return []

    def update_one(self, key, obj, upsert=False):
        self.data.append(obj.get("$set", {}))

    def create_index(self, *args, **kwargs):
        return None

    def estimated_document_count(self):
        return len(self.data)


class _FakeClient(dict):
    def __getitem__(self, item):
        if item not in self:
            self[item] = {}
        return self[item]


def test_quality_gate_live_writes_snapshots():
    client = _FakeClient()
    client.setdefault("db", {})["buffer_quality_flags"] = _FakeCollection()
    from infra.storage.timeseries_db import MongoBufferWriter

    df = pd.DataFrame(
        {
            "event_time": pd.to_datetime(["2024-01-01T00:00:00Z"]),
            "recv_time": pd.to_datetime(["2024-01-01T00:00:00Z"]),
            "symbol": ["BTCUSDT"],
            "venue": ["binance"],
            "source": ["spot"],
            "event_type": ["trade"],
            "seq": [1],
            "price": [100.0],
            "qty": [1.0],
        }
    )
    checks = [
        SchemaValidationCheck(["event_time", "recv_time", "symbol", "venue", "source", "event_type"]),
        MissingnessCheck(["event_time", "recv_time", "symbol", "venue", "source", "event_type"]),
        ClockSkewCheck(2000),
        StalenessCheck(30_000),
        DuplicateCheck(),
        SequenceGapCheck(),
        TimeTravelCheck(),
        OutlierCheck(),
        BookSanityCheck(10_000),
        MicrostructureToxicityCheck(20_000),
        CrossSourceConsistencyCheck(100_000),
        HaltDetectionCheck(300),
    ]
    mwriter = MongoBufferWriter("uri", "db", "buffer_quality_flags", client=client)
    gate = QualityGate(
        checks=checks,
        mode="live",
        watermark_ms=5000,
        run_id="test",
        output_clean_path="",
        output_flags_path="",
        mongo_writer=mwriter,
    )
    out = gate.run_live(df)
    assert len(client["db"]["buffer_quality_flags"].data) > 0
