from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

from market_physics_v3.collectors.normalize import BookDeltaState, PARSERS, SequenceGap
from market_physics_v3.collectors.specs import subscriptions
from market_physics_v3.collectors.writer import AppendOnlyEventWriter, RawMessageWriter
from market_physics_v3.schema import BookEvent, DerivativeEvent, TradeEvent


DEFAULT_FRESH_EVENT_MAX_LAG_MS = 5000.0


class CollectorHealth:
    def __init__(self, venue: str, fresh_event_max_lag_ms: float = DEFAULT_FRESH_EVENT_MAX_LAG_MS):
        self.venue = venue
        self.fresh_event_max_lag_ms = float(fresh_event_max_lag_ms)
        self.started_ns = time.time_ns()
        self.stopped_ns = 0
        self.clean_shutdown = False
        self.connected = False
        self.reconnects = 0
        self.messages = 0
        self.events = 0
        self.book_events = 0
        self.trade_events = 0
        self.derivative_events = 0
        self.fresh_events = 0
        self.stale_events = 0
        self.fresh_book_events = 0
        self.fresh_trade_events = 0
        self.fresh_derivative_events = 0
        self.stale_book_events = 0
        self.stale_trade_events = 0
        self.stale_derivative_events = 0
        self.max_book_lag_ms = 0.0
        self.max_trade_lag_ms = 0.0
        self.max_derivative_lag_ms = 0.0
        self.parse_errors = 0
        self.sequence_gaps = 0
        self.subscription_acks = 0
        self.subscription_errors = 0
        self.last_receive_ns = 0
        self.last_event_ns = 0
        self.last_exception = None

    def observe_event(self, event) -> None:
        self.events += 1
        self.last_event_ns = max(self.last_event_ns, int(event.event_ts_ns))
        lag_ms = max(0.0, (int(event.receive_ts_ns) - int(event.event_ts_ns)) / 1e6)
        fresh = lag_ms <= self.fresh_event_max_lag_ms
        if fresh:
            self.fresh_events += 1
        else:
            self.stale_events += 1

        if isinstance(event, BookEvent):
            self.book_events += 1
            self.max_book_lag_ms = max(self.max_book_lag_ms, lag_ms)
            if fresh:
                self.fresh_book_events += 1
            else:
                self.stale_book_events += 1
        elif isinstance(event, TradeEvent):
            self.trade_events += 1
            self.max_trade_lag_ms = max(self.max_trade_lag_ms, lag_ms)
            if fresh:
                self.fresh_trade_events += 1
            else:
                self.stale_trade_events += 1
        elif isinstance(event, DerivativeEvent):
            self.derivative_events += 1
            self.max_derivative_lag_ms = max(self.max_derivative_lag_ms, lag_ms)
            if fresh:
                self.fresh_derivative_events += 1
            else:
                self.stale_derivative_events += 1

    def as_dict(self) -> Dict[str, object]:
        now = time.time_ns()
        return {
            "venue": self.venue,
            "fresh_event_max_lag_ms": self.fresh_event_max_lag_ms,
            "started_ns": self.started_ns,
            "stopped_ns": self.stopped_ns,
            "clean_shutdown": self.clean_shutdown,
            "connected": self.connected,
            "reconnects": self.reconnects,
            "messages": self.messages,
            "events": self.events,
            "book_events": self.book_events,
            "trade_events": self.trade_events,
            "derivative_events": self.derivative_events,
            "fresh_events": self.fresh_events,
            "stale_events": self.stale_events,
            "fresh_book_events": self.fresh_book_events,
            "fresh_trade_events": self.fresh_trade_events,
            "fresh_derivative_events": self.fresh_derivative_events,
            "stale_book_events": self.stale_book_events,
            "stale_trade_events": self.stale_trade_events,
            "stale_derivative_events": self.stale_derivative_events,
            "max_book_lag_ms": self.max_book_lag_ms,
            "max_trade_lag_ms": self.max_trade_lag_ms,
            "max_derivative_lag_ms": self.max_derivative_lag_ms,
            "parse_errors": self.parse_errors,
            "sequence_gaps": self.sequence_gaps,
            "subscription_acks": self.subscription_acks,
            "subscription_errors": self.subscription_errors,
            "last_receive_ns": self.last_receive_ns,
            "last_event_ns": self.last_event_ns,
            "idle_ms": (now - self.last_receive_ns) / 1e6 if self.last_receive_ns else None,
            "last_exception": self.last_exception,
        }


def _write_health(path: Optional[Path], health: CollectorHealth) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(health.as_dict(), indent=2, sort_keys=True))
    tmp.replace(path)


def _control_status(venue: str, msg: object):
    if not isinstance(msg, dict):
        return None
    if venue == "binance" and "id" in msg and "result" in msg:
        return "ack" if msg.get("result") is None else "error"
    if venue == "bybit" and (msg.get("op") == "subscribe" or "success" in msg):
        return "ack" if msg.get("success", True) else "error"
    if venue == "okx" and msg.get("event") in {"subscribe", "error"}:
        return "ack" if msg.get("event") == "subscribe" else "error"
    if venue == "hyperliquid" and msg.get("channel") == "subscriptionResponse":
        return "ack"
    return None


async def _heartbeat(ws, venue: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(20)
        if stop.is_set():
            return
        if venue == "bybit":
            await ws.send(json.dumps({"op": "ping"}))
        elif venue == "hyperliquid":
            await ws.send(json.dumps({"method": "ping"}))
        elif venue == "okx":
            await ws.send("ping")
        else:
            # Binance uses protocol-level ping frames; websockets normally handles
            # them, but an explicit ping gives us a fast liveness failure too.
            pong = await ws.ping()
            await asyncio.wait_for(pong, timeout=10)


async def run_venue(
    venue: str,
    symbols: Iterable[str],
    root: str = "data/market_physics_v3",
    health_dir: str = "reports/market_physics_v3/health",
    max_backoff_s: float = 30.0,
    fresh_event_max_lag_ms: float = DEFAULT_FRESH_EVENT_MAX_LAG_MS,
) -> None:
    try:
        import websockets
        from importlib.metadata import version as package_version
    except ImportError as exc:
        raise RuntimeError(
            "Market Physics live collectors require websockets>=12,<14 in a dedicated research environment"
        ) from exc
    ws_version = package_version("websockets")
    try:
        ws_major = int(ws_version.split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("cannot parse installed websockets version: %r" % ws_version) from exc
    if ws_major < 12 or ws_major >= 14:
        raise RuntimeError("unsupported websockets %s; require >=12,<14" % ws_version)

    venue = venue.lower()
    spec = subscriptions(venue, symbols)
    parser = PARSERS[venue]
    writer = AppendOnlyEventWriter(root)
    raw_writer = RawMessageWriter(root)
    health = CollectorHealth(venue, fresh_event_max_lag_ms=fresh_event_max_lag_ms)
    health_path = Path(health_dir) / (venue + ".json")
    backoff = 1.0

    try:
        while True:
            # Incremental state is connection-local. A reconnect or sequence gap
            # invalidates it; never carry a stale book across sessions.
            state = BookDeltaState()
            heartbeat_stop = asyncio.Event()
            try:
                async with websockets.connect(
                    spec["url"],
                    ping_interval=20 if venue == "binance" else None,
                    ping_timeout=20,
                    close_timeout=10,
                    max_queue=8192,
                ) as ws:
                    connection_id = "%s-%s" % (venue, time.time_ns())
                    health.connected = True
                    health.clean_shutdown = False
                    health.stopped_ns = 0
                    health.last_exception = None
                    _write_health(health_path, health)
                    if "subscribe" in spec:
                        await ws.send(json.dumps(spec["subscribe"]))
                    else:
                        for subscription in spec.get("subscribe_many", []):
                            await ws.send(json.dumps(subscription))
                    hb = asyncio.create_task(_heartbeat(ws, venue, heartbeat_stop))
                    backoff = 1.0
                    try:
                        async for raw in ws:
                            receive_ns = time.time_ns()
                            health.messages += 1
                            health.last_receive_ns = receive_ns
                            if raw == "pong":
                                continue
                            parsed = None
                            try:
                                parsed = json.loads(raw)
                                raw_writer.append(venue, receive_ns, parsed, connection_id)
                                control = _control_status(venue, parsed)
                                if control == "ack":
                                    health.subscription_acks += 1
                                elif control == "error":
                                    health.subscription_errors += 1
                                events = parser(parsed, receive_ns, state)
                                for event in events:
                                    # Keep delayed/bootstrap/replay events losslessly.
                                    # Freshness is a qualification/use-time property,
                                    # never a reason to destroy observed exchange data.
                                    writer.append(event)
                                    health.observe_event(event)
                            except SequenceGap as exc:
                                health.parse_errors += 1
                                health.sequence_gaps += 1
                                health.last_exception = str(exc)
                                raw_writer.dead_letter(
                                    venue,
                                    receive_ns,
                                    parsed if parsed is not None else raw,
                                    exc,
                                )
                                # Fail closed: force reconnect and reconstruct venue state.
                                raise
                            except Exception as exc:
                                health.parse_errors += 1
                                health.last_exception = str(exc)
                                raw_writer.dead_letter(
                                    venue,
                                    receive_ns,
                                    parsed if parsed is not None else raw,
                                    exc,
                                )
                            if health.messages % 100 == 0:
                                _write_health(health_path, health)
                    finally:
                        heartbeat_stop.set()
                        hb.cancel()
                        health.connected = False
                        _write_health(health_path, health)
            except asyncio.CancelledError:
                health.connected = False
                health.clean_shutdown = health.last_exception in (None, "")
                health.stopped_ns = time.time_ns()
                _write_health(health_path, health)
                raise
            except Exception as exc:
                health.connected = False
                health.clean_shutdown = False
                health.reconnects += 1
                health.last_exception = str(exc)
                _write_health(health_path, health)
                await asyncio.sleep(
                    backoff + random.random() * min(1.0, backoff * 0.1)
                )
                backoff = min(max_backoff_s, backoff * 2.0)
    finally:
        writer.close()
        raw_writer.close()
        health.connected = False
        if health.stopped_ns == 0:
            health.stopped_ns = time.time_ns()
        _write_health(health_path, health)


async def run_many(
    venues: Iterable[str], symbols: Iterable[str], root: str, health_dir: str
) -> None:
    tasks = [
        asyncio.create_task(run_venue(v, symbols, root, health_dir)) for v in venues
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
