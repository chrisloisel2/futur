"""tests/test_worldmon.py — store pluggable, agents, rigueur du correlator."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.worldmon.bigdata_store import BigDataStore, content_hash
from src.institutional.worldmon.agents.quality import Quality
from src.institutional.worldmon.agents.correlator import Correlator
from src.institutional.worldmon.agents.enricher import Enricher


class FakeStore:
    """Store mémoire (pas de Mongo) pour tests déterministes."""
    def __init__(self, events):
        self._events = events
        self.docs = {}
        self.mongo = None
        self.backend = "memory"
        self.features = {}
    def events_df(self, since_days=3650):
        df = pd.DataFrame(self._events)
        if df.empty:
            return df
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df
    def write_doc(self, col, doc): self.docs.setdefault(col, []).append(doc)
    def latest_doc(self, col):
        d = self.docs.get(col); return d[-1] if d else None
    def write_features(self, name, df): self.features[name] = df
    def count(self, c="world_events"): return len(self._events)


def test_content_hash_stable():
    assert content_hash("a", "b") == content_hash("a", "b")
    assert content_hash("a", "b") != content_hash("a", "c")
    assert len(content_hash("x")) == 20


def test_store_degrades_to_jsonl_or_mongo():
    s = BigDataStore()
    assert s.backend in ("mongodb", "jsonl")   # jamais un crash


def test_quality_flags_untrustworthy_when_empty():
    q = Quality(FakeStore([]))
    r = q.run()
    assert r["ok"] and r["data_trustworthy"] is False


def test_quality_trustworthy_on_clean_data():
    now = pd.Timestamp.utcnow()
    evs = []
    for i in range(60):
        for src in ("gdelt:gdelt_vol", "coingecko:global", "usgs"):
            evs.append({"ts": (now - pd.Timedelta(days=i)).isoformat(),
                        "source": src, "kind": "media_metric",
                        "content_hash": content_hash(src, i)})
    r = Quality(FakeStore(evs)).run()
    assert r["schema_ok"] and r["dedup_ratio"] > 0.98 and r["source_coverage"] >= 0.5


def test_correlator_gated_by_quality():
    """Sans quality report fiable, le correlator NE tourne PAS (rigueur)."""
    s = FakeStore([])
    r = Correlator(s).run()
    assert r.get("skipped") is True


def test_correlator_reports_multiple_testing_warning(monkeypatch):
    now = pd.Timestamp.utcnow().floor("D")
    evs = []
    for i in range(200):
        d = (now - pd.Timedelta(days=i))
        evs.append({"ts": d.isoformat(), "source": "gdelt:gdelt_vol",
                    "kind": "media_metric", "metric": "gdelt_vol",
                    "value": float(50 + np.sin(i / 5) * 10),
                    "content_hash": content_hash("v", i)})
    s = FakeStore(evs)
    s.write_doc("quality_reports", {"data_trustworthy": True})
    # prix factice
    idx = pd.date_range(now - pd.Timedelta(days=200), now, freq="D", tz="UTC")
    px = pd.Series(50000 * np.cumprod(1 + np.random.default_rng(0).normal(0, .01, len(idx))), index=idx)
    monkeypatch.setattr("src.institutional.worldmon.agents.correlator._price",
                        lambda a: px)
    r = Correlator(s).run()
    assert r["ok"] and "n_tests" in r
    doc = s.latest_doc("signal_candidates")
    assert "WARNING" in doc and "walk-forward" in doc["WARNING"]


def test_enricher_classifies_lexically():
    e = Enricher(FakeStore([]))
    assert "SECURITY" in e._classify("Major exchange hacked, funds stolen")
    assert "REGULATORY" in e._classify("SEC files lawsuit over token")
    assert e._classify("nothing notable here") == ["GENERAL"]
