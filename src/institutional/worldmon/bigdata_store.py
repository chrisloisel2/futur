"""
src/institutional/worldmon/bigdata_store.py
─────────────────────────────────────────────────────────────────────────────
Store big-data PLUGGABLE pour le World Monitor.

Deux couches, honnêtes :
  • DOCUMENTS  : MongoDB (world_events, quality_reports, signal_candidates,
    agent_health) — schéma flexible + provenance, indexé (ts, source, hash).
    Dégradation gracieuse : si Mongo injoignable → JSONL append-only local
    (aucune perte, aucune fausse fiabilité).
  • COLONNAIRE : Parquet partitionné (features quotidiennes agrégées) = le
    format big-data que Spark/HDFS lisent nativement. Le store est pluggable :
    un vrai cluster lira les mêmes Parquet sans réécriture.

Aucune donnée fabriquée ; provenance (source, fetch_time, content_hash) sur
chaque document ; écritures idempotentes (upsert sur hash).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
JSONL_DIR = ROOT / "data" / "worldmon_jsonl"
PARQUET_DIR = ROOT / "data" / "worldmon_features"
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "futur_worldmon"


def content_hash(*parts) -> str:
    return hashlib.sha256("||".join(str(p) for p in parts).encode()).hexdigest()[:20]


class BigDataStore:
    """Couche documents Mongo + fallback JSONL. Idempotent (upsert sur _id=hash)."""

    def __init__(self):
        self.mongo = None
        self.backend = "jsonl"
        try:
            import pymongo
            c = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
            c.admin.command("ping")
            self.mongo = c[DB_NAME]
            self.backend = "mongodb"
            self._ensure_indexes()
        except Exception:
            self.mongo = None
            JSONL_DIR.mkdir(parents=True, exist_ok=True)

    def _ensure_indexes(self):
        import pymongo
        self.mongo.world_events.create_index([("ts", pymongo.DESCENDING)])
        self.mongo.world_events.create_index([("source", 1), ("ts", -1)])
        self.mongo.world_events.create_index([("symbols", 1)])
        self.mongo.signal_candidates.create_index([("computed_at", -1)])

    # ── documents ────────────────────────────────────────────────────────────
    def upsert_events(self, docs: List[Dict]) -> int:
        """Événements avec provenance. _id = content_hash → idempotent."""
        if not docs:
            return 0
        for d in docs:
            d.setdefault("ingested_at", datetime.now(timezone.utc).isoformat())
            # priorité au content_hash calculé par la source (identifie l'ITEM,
            # pas seulement source+ts → évite l'effondrement des métriques macro)
            d["_id"] = d.get("_id") or d.get("content_hash") or content_hash(
                d.get("source"), d.get("url", ""), d.get("title", ""),
                d.get("metric", ""), d.get("ts"))
        if self.mongo is not None:
            from pymongo import ReplaceOne
            ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs]
            res = self.mongo.world_events.bulk_write(ops, ordered=False)
            return res.upserted_count
        # fallback JSONL : dédup sur hash connu du jour
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        f = JSONL_DIR / f"world_events_{day}.jsonl"
        seen = set()
        if f.exists():
            for line in f.read_text().splitlines():
                try:
                    seen.add(json.loads(line)["_id"])
                except Exception:
                    pass
        n = 0
        with open(f, "a") as fh:
            for d in docs:
                if d["_id"] in seen:
                    continue
                fh.write(json.dumps(d, default=str) + "\n")
                n += 1
        return n

    def write_doc(self, collection: str, doc: Dict) -> None:
        doc["computed_at"] = datetime.now(timezone.utc).isoformat()
        if self.mongo is not None:
            self.mongo[collection].insert_one(doc)
        else:
            JSONL_DIR.mkdir(parents=True, exist_ok=True)
            with open(JSONL_DIR / f"{collection}.jsonl", "a") as fh:
                fh.write(json.dumps(doc, default=str) + "\n")

    def latest_doc(self, collection: str) -> Optional[Dict]:
        if self.mongo is not None:
            return self.mongo[collection].find_one(sort=[("computed_at", -1)])
        f = JSONL_DIR / f"{collection}.jsonl"
        if not f.exists():
            return None
        lines = f.read_text().splitlines()
        return json.loads(lines[-1]) if lines else None

    def events_df(self, since_days: int = 3650):
        import pandas as pd
        cutoff = time.time() - since_days * 86400
        rows = []
        if self.mongo is not None:
            for d in self.mongo.world_events.find({}, {"_id": 0}):
                rows.append(d)
        else:
            for f in sorted(JSONL_DIR.glob("world_events_*.jsonl")):
                for line in f.read_text().splitlines():
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        return df.dropna(subset=["ts"])

    def count(self, collection: str = "world_events") -> int:
        if self.mongo is not None:
            return self.mongo[collection].estimated_document_count()
        return sum(1 for f in JSONL_DIR.glob(f"{collection}*.jsonl")
                   for _ in f.read_text().splitlines())

    # ── colonnaire (Parquet, lisible Spark/HDFS) ─────────────────────────────
    def write_features(self, name: str, df) -> Path:
        PARQUET_DIR.mkdir(parents=True, exist_ok=True)
        p = PARQUET_DIR / f"{name}.parquet"
        tmp = p.with_suffix(".tmp.parquet")
        df.to_parquet(tmp)
        tmp.replace(p)
        return p
