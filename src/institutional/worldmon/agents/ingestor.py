"""
Agent INGESTOR — récupère les sources world + news, écrit en documents avec
provenance (source, fetch_time, content_hash). Idempotent (upsert sur hash).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from src.institutional.worldmon.bigdata_store import BigDataStore
from src.institutional.worldmon.sources import fetch_all


class Ingestor:
    name = "ingestor"

    def __init__(self, store: BigDataStore):
        self.store = store

    def run(self) -> Dict:
        fetched, written, per = 0, 0, {}
        try:
            bundle = fetch_all()
            for src, docs in bundle.items():
                for d in docs:
                    d["fetch_time"] = datetime.now(timezone.utc).isoformat()
                per[src] = len(docs)
                fetched += len(docs)
                written += self.store.upsert_events(docs)
        except Exception as e:
            return {"agent": self.name, "ok": False, "error": str(e)}
        # intègre aussi le lac news existant (déjà collecté, sources publiques)
        try:
            from src.institutional.data.news_collector.collector import load_news_lake
            nl = load_news_lake()
            if not nl.empty:
                recent = nl.tail(200)
                docs = [{
                    "ts": r["ts"].isoformat(), "source": f"news:{r['source']}",
                    "kind": "news", "title": r["title"], "url": r["url"],
                    "symbols": (r["symbols"].split(",") if r["symbols"] else []),
                    "sentiment": float(r["sentiment"]),
                    "content_hash": r["url_hash"],
                } for _, r in recent.iterrows()]
                n = self.store.upsert_events(docs)
                written += n
                per["news"] = len(docs)
        except Exception:
            pass
        return {"agent": self.name, "ok": True, "fetched": fetched,
                "written_new": written, "per_source": per,
                "total_events": self.store.count()}
