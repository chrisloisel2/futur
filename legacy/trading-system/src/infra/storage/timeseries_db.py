from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

import pandas as pd

from common.logging.setup import get_logger

logger = get_logger(__name__)


class MongoBufferReader:
    def __init__(self, uri: str, db: str, collection: str, client: Optional[Any] = None) -> None:
        self.uri = uri
        self.db = db
        self.collection = collection
        if client is not None:
            self.client = client
        else:  # pragma: no cover - optional dependency
            try:
                import pymongo
            except ImportError as exc:  # pragma: no cover
                raise ImportError("pymongo is required for MongoBufferReader") from exc
            self.client = pymongo.MongoClient(uri, tz_aware=True)

    def fetch_events(self, symbol: str, start: datetime, end: datetime, sources: Optional[Iterable[str]] = None, limit: int = 500_000) -> pd.DataFrame:
        coll = self.client[self.db][self.collection]
        query: dict[str, Any] = {
            "symbol": symbol,
            "event_time": {"$gte": start, "$lte": end},
        }
        if sources:
            query["source"] = {"$in": list(sources)}
        cursor = coll.find(query, sort=[("event_time", 1)], limit=limit)
        docs = list(cursor)
        if not docs:
            return pd.DataFrame()
        df = pd.DataFrame(docs)
        for col in ("event_time", "recv_time", "event_time_aligned", "expires_at"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        logger.info({"msg": "fetched from mongo buffer", "rows": len(df), "symbol": symbol})
        return df


class MongoBufferWriter(MongoBufferReader):
    def write_events(self, df: pd.DataFrame, ttl_seconds: int = 3600) -> None:
        if df.empty:
            return
        coll = self.client[self.db][self.collection]
        self._ensure_indexes(coll, ttl_seconds)
        records = df.to_dict(orient="records")
        for rec in records:
            rec["expires_at"] = rec.get("recv_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {
                "symbol": rec.get("symbol"),
                "venue": rec.get("venue"),
                "source": rec.get("source"),
                "event_type": rec.get("event_type"),
                "seq": rec.get("seq", 0),
                "event_time": rec.get("event_time"),
            }
            coll.update_one(key, {"$set": rec}, upsert=True)
        logger.info({"msg": "wrote to mongo buffer", "rows": len(records)})

    def _ensure_indexes(self, coll: Any, ttl_seconds: int) -> None:
        if coll.estimated_document_count() == 0:
            coll.create_index([("symbol", 1), ("event_time", 1)])
            coll.create_index([("venue", 1), ("source", 1), ("event_type", 1), ("event_time", 1)])
            coll.create_index("expires_at", expireAfterSeconds=ttl_seconds)

    def write_quality_snapshots(self, snapshots_df: pd.DataFrame, ttl_seconds: int = 3600) -> None:
        if snapshots_df.empty:
            return
        coll = self.client[self.db].get("buffer_quality_flags")
        if coll is None:
            self.client[self.db]["buffer_quality_flags"] = self.client[self.db].get("buffer_quality_flags", self.client[self.db].get("coll", None))
            coll = self.client[self.db]["buffer_quality_flags"]
        self._ensure_indexes(coll, ttl_seconds)
        records = snapshots_df.to_dict(orient="records")
        for rec in records:
            rec["expires_at"] = rec.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"symbol": rec.get("symbol"), "event_time": rec.get("event_time"), "venue": rec.get("venue")}
            coll.update_one(key, {"$set": rec}, upsert=True)

    def fetch_latest_quality(self, symbol: str, lookback_minutes: int = 60) -> pd.DataFrame:
        coll = self.client[self.db]["buffer_quality_flags"]
        end = datetime.utcnow()
        start = end - timedelta(minutes=lookback_minutes)
        cursor = coll.find({"symbol": symbol, "event_time": {"$gte": start, "$lte": end}}, sort=[("event_time", -1)])
        docs = list(cursor)
        if not docs:
            return pd.DataFrame()
        df = pd.DataFrame(docs)
        for col in ("event_time", "expires_at"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        return df


class SignalCacheWriter(MongoBufferReader):
    def write_signal(self, signal: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("signal_cache") or self.client[self.db].setdefault("signal_cache", {})
        if hasattr(coll, "update_one"):
            signal = signal.copy()
            signal["expires_at"] = signal.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"symbol": signal.get("symbol")}
            coll.update_one(key, {"$set": signal}, upsert=True)


class SignalCacheReader(MongoBufferReader):
    def fetch_latest_signal(self, symbol: str):
        coll = self.client[self.db].get("signal_cache") or self.client[self.db].setdefault("signal_cache", {})
        if hasattr(coll, "find_one"):
            doc = coll.find_one({"symbol": symbol})
            if not doc:
                return None
            if "event_time" in doc:
                doc["event_time"] = pd.to_datetime(doc["event_time"])
            return doc
        return None


class MetaStateCacheWriter(MongoBufferReader):
    def write_meta_state(self, meta_state: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("meta_state_cache") or self.client[self.db].setdefault("meta_state_cache", {})
        if hasattr(coll, "update_one"):
            meta_state = meta_state.copy()
            meta_state["expires_at"] = meta_state.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"scope": meta_state.get("scope", "portfolio")}
            coll.update_one(key, {"$set": meta_state}, upsert=True)


class MetaStateCacheReader(MongoBufferReader):
    def fetch_latest_meta_state(self, scope: str = "portfolio"):
        coll = self.client[self.db].get("meta_state_cache") or self.client[self.db].setdefault("meta_state_cache", {})
        if hasattr(coll, "find_one"):
            doc = coll.find_one({"scope": scope})
            if doc and "event_time" in doc:
                doc["event_time"] = pd.to_datetime(doc["event_time"])
            return doc
        return None


class AllocCacheWriter(MongoBufferReader):
    def write_alloc(self, alloc: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("alloc_cache") or self.client[self.db].setdefault("alloc_cache", {})
        if hasattr(coll, "update_one"):
            alloc = alloc.copy()
            alloc["expires_at"] = alloc.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"scope": alloc.get("scope", "portfolio")}
            coll.update_one(key, {"$set": alloc}, upsert=True)


class AllocCacheReader(MongoBufferReader):
    def fetch_latest_alloc(self, scope: str = "portfolio"):
        coll = self.client[self.db].get("alloc_cache") or self.client[self.db].setdefault("alloc_cache", {})
        if hasattr(coll, "find_one"):
            doc = coll.find_one({"scope": scope})
            if doc and "event_time" in doc:
                doc["event_time"] = pd.to_datetime(doc["event_time"])
            return doc
        return None


class TargetPositionsCacheWriter(MongoBufferReader):
    def write_target_positions(self, payload: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("target_positions_cache") or self.client[self.db].setdefault("target_positions_cache", {})
        if hasattr(coll, "update_one"):
            payload = payload.copy()
            payload["expires_at"] = payload.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"scope": payload.get("scope", "portfolio")}
            coll.update_one(key, {"$set": payload}, upsert=True)


class TargetPositionsCacheReader(MongoBufferReader):
    def fetch_latest(self, scope: str = "portfolio"):
        coll = self.client[self.db].get("target_positions_cache") or self.client[self.db].setdefault("target_positions_cache", {})
        if hasattr(coll, "find_one"):
            doc = coll.find_one({"scope": scope})
            if doc and "event_time" in doc:
                doc["event_time"] = pd.to_datetime(doc["event_time"])
            return doc
        return None


class AllocatorDecisionCacheWriter(MongoBufferReader):
    def write_allocator_decision(self, decision: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("allocator_decision_cache") or self.client[self.db].setdefault("allocator_decision_cache", {})
        if hasattr(coll, "update_one"):
            decision = decision.copy()
            decision["expires_at"] = decision.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"scope": decision.get("scope", "portfolio")}
            coll.update_one(key, {"$set": decision}, upsert=True)


class BooksStateCacheWriter(MongoBufferReader):
    def write_books_state(self, books_state: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("books_state_cache") or self.client[self.db].setdefault("books_state_cache", {})
        if hasattr(coll, "update_one"):
            books_state = books_state.copy()
            books_state["expires_at"] = books_state.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"scope": books_state.get("scope", "portfolio")}
            coll.update_one(key, {"$set": books_state}, upsert=True)


class RiskStateCacheWriter(MongoBufferReader):
    def write_risk_state(self, risk_state: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("risk_state_cache") or self.client[self.db].setdefault("risk_state_cache", {})
        if hasattr(coll, "update_one"):
            risk_state = risk_state.copy()
            risk_state["expires_at"] = risk_state.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"scope": risk_state.get("scope", "portfolio")}
            coll.update_one(key, {"$set": risk_state}, upsert=True)


class OrdersPlanCacheWriter(MongoBufferReader):
    def write_orders_plan(self, orders_plan: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("orders_plan_cache") or self.client[self.db].setdefault("orders_plan_cache", {})
        if hasattr(coll, "update_one"):
            orders_plan = orders_plan.copy()
            orders_plan["expires_at"] = orders_plan.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"scope": orders_plan.get("scope", "portfolio")}
            coll.update_one(key, {"$set": orders_plan}, upsert=True)


class ScenarioResultsCacheWriter(MongoBufferReader):
    def write_scenario_results(self, results: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("scenario_results_cache") or self.client[self.db].setdefault("scenario_results_cache", {})
        if hasattr(coll, "update_one"):
            results = results.copy()
            results["expires_at"] = results.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            key = {"scope": results.get("scope", "portfolio")}
            coll.update_one(key, {"$set": results}, upsert=True)


class OrdersPlanReader(MongoBufferReader):
    def read_latest(self):
        coll = self.client[self.db].get("orders_plan_cache") or self.client[self.db].setdefault("orders_plan_cache", {})
        if hasattr(coll, "find_one"):
            doc = coll.find_one()
            return doc
        return None


class ExecutionStateWriter(MongoBufferReader):
    def write_state(self, state: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("execution_state_cache") or self.client[self.db].setdefault("execution_state_cache", {})
        if hasattr(coll, "update_one"):
            state = state.copy()
            state["expires_at"] = state.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            coll.update_one({"scope": state.get("scope", "portfolio")}, {"$set": state}, upsert=True)


class OrderEventsWriter(MongoBufferReader):
    def write_events(self, events: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("order_events_cache") or self.client[self.db].setdefault("order_events_cache", {})
        if hasattr(coll, "update_one"):
            events = events.copy()
            events["expires_at"] = events.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            coll.update_one({"scope": events.get("scope", "portfolio")}, {"$set": events}, upsert=True)


class ExecutedFillsWriter(MongoBufferReader):
    def write_fills(self, fills: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("executed_fills_cache") or self.client[self.db].setdefault("executed_fills_cache", {})
        if hasattr(coll, "update_one"):
            fills = fills.copy()
            fills["expires_at"] = fills.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            coll.update_one({"scope": fills.get("scope", "portfolio")}, {"$set": fills}, upsert=True)


class ExecutionCostsWriter(MongoBufferReader):
    def write_costs(self, costs: dict, ttl_seconds: int = 300) -> None:
        coll = self.client[self.db].get("execution_costs_cache") or self.client[self.db].setdefault("execution_costs_cache", {})
        if hasattr(coll, "update_one"):
            costs = costs.copy()
            costs["expires_at"] = costs.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            coll.update_one({"scope": costs.get("scope", "portfolio")}, {"$set": costs}, upsert=True)


class MonitoringStateWriter(MongoBufferReader):
    def write_monitoring_state(self, payload: dict, ttl_seconds: int = 600) -> None:
        coll = self.client[self.db].get("monitoring_state_cache") or self.client[self.db].setdefault("monitoring_state_cache", {})
        if hasattr(coll, "update_one"):
            payload = payload.copy()
            payload["expires_at"] = payload.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            coll.update_one({"scope": payload.get("scope", "portfolio")}, {"$set": payload}, upsert=True)


class DriftReportsWriter(MongoBufferReader):
    def write_reports(self, payload: dict, ttl_seconds: int = 600) -> None:
        coll = self.client[self.db].get("drift_reports_cache") or self.client[self.db].setdefault("drift_reports_cache", {})
        if hasattr(coll, "update_one"):
            payload = payload.copy()
            payload["expires_at"] = payload.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            coll.update_one({"scope": payload.get("scope", "portfolio")}, {"$set": payload}, upsert=True)


class ActionPlansWriter(MongoBufferReader):
    def write_actions(self, payload: dict, ttl_seconds: int = 600) -> None:
        coll = self.client[self.db].get("action_plans_cache") or self.client[self.db].setdefault("action_plans_cache", {})
        if hasattr(coll, "update_one"):
            payload = payload.copy()
            payload["expires_at"] = payload.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            coll.update_one({"scope": payload.get("scope", "portfolio")}, {"$set": payload}, upsert=True)


class AlertsWriter(MongoBufferReader):
    def write_alerts(self, payload: dict, ttl_seconds: int = 600) -> None:
        coll = self.client[self.db].get("alerts_cache") or self.client[self.db].setdefault("alerts_cache", {})
        if hasattr(coll, "update_one"):
            payload = payload.copy()
            payload["expires_at"] = payload.get("event_time", datetime.utcnow()) + timedelta(seconds=ttl_seconds)
            coll.update_one({"scope": payload.get("scope", "portfolio")}, {"$set": payload}, upsert=True)
